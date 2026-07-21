# ruff: noqa: S105, S107, S108, S608 -- credentials/paths/SQL are disposable fixtures.
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from contact_ops.ops import db_credential_rotation as rotation


def _sample_env(
    runtime_password: str = "old-runtime-example",
    migration_password: str = "old-migration-example",
) -> bytes:
    return (
        "ENV=prod\n"
        "DATABASE_URL=postgresql+asyncpg://"
        f"contact_ops_runtime:{runtime_password}@contact-ops-postgres:5432/contact_ops_db\n"
        "AUDIT_DATABASE_URL='postgresql+asyncpg://"
        f"contact_ops_runtime:{runtime_password}@contact-ops-postgres:5432/contact_ops_db'\n"
        "MIGRATION_DATABASE_URL=postgresql://"
        f"contact_ops_admin:{migration_password}@contact-ops-postgres:5432/contact_ops_db\n"
        "UNRELATED_VALUE=preserved\n"
    ).encode()


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / ".env"
    source.write_bytes(_sample_env())
    source.chmod(0o600)
    state_parent = tmp_path / "operator-state"
    state_parent.mkdir()
    return source, state_parent / "rotation"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_stage_creates_private_two_phase_state_without_printing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, state_dir = _paths(tmp_path)
    original = source.read_bytes()

    rotation.stage(source, state_dir)

    captured = capsys.readouterr()
    assert "old-runtime-example" not in captured.out
    assert "old-migration-example" not in captured.out
    assert source.read_bytes() == original
    assert _mode(state_dir) == 0o700
    for filename in rotation.STATE_FILES:
        assert _mode(state_dir / filename) == 0o600

    state = rotation._load_state(state_dir)
    previous = rotation._validate_env(state.files["previous.env"])
    runtime = rotation._validate_env(state.files["runtime.env"])
    final = rotation._validate_env(state.files["final.env"])
    assert runtime["DATABASE_URL"].password == runtime["AUDIT_DATABASE_URL"].password
    assert runtime["DATABASE_URL"].password != previous["DATABASE_URL"].password
    assert runtime["MIGRATION_DATABASE_URL"].password == previous["MIGRATION_DATABASE_URL"].password
    assert final["DATABASE_URL"].password == runtime["DATABASE_URL"].password
    assert final["MIGRATION_DATABASE_URL"].password != runtime["MIGRATION_DATABASE_URL"].password
    assert final["MIGRATION_DATABASE_URL"].password != final["DATABASE_URL"].password
    assert b"UNRELATED_VALUE=preserved" in state.files["final.env"]


def test_stage_rejects_weak_source_permissions(tmp_path: Path) -> None:
    source, state_dir = _paths(tmp_path)
    source.chmod(0o640)

    with pytest.raises(rotation.RotationError, match="group/other"):
        rotation.stage(source, state_dir)

    assert not state_dir.exists()


def test_stage_rejects_role_or_endpoint_drift(tmp_path: Path) -> None:
    source, state_dir = _paths(tmp_path)
    source.write_bytes(_sample_env().replace(b"contact_ops_runtime", b"contact_ops_admin", 1))
    source.chmod(0o600)

    with pytest.raises(rotation.RotationError, match="runtime role"):
        rotation.stage(source, state_dir)


def test_state_machine_enforces_forward_order_and_supports_both_rollbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    container = "contact-ops-postgres"
    password_phases: list[str] = []
    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)
    monkeypatch.setattr(rotation, "_verify_local_role_contract", lambda _container: None)
    monkeypatch.setattr(rotation, "_verify_phase", lambda *_args, **_kwargs: None)

    def record_password_phase(*_args: object, **_kwargs: object) -> None:
        password_phases.append(rotation._load_state(state_dir).phase)

    monkeypatch.setattr(rotation, "_set_password_with_psql", record_password_phase)

    rotation.role_password(state_dir, "runtime-new", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_RUNTIME_ROLE_APPLIED
    assert source.read_bytes() == state.files["previous.env"]

    rotation.activate(state_dir, "runtime", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_RUNTIME_ACTIVE
    assert source.read_bytes() == state.files["runtime.env"]

    rotation.role_password(state_dir, "migration-new", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_MIGRATION_ROLE_APPLIED
    assert source.read_bytes() == state.files["runtime.env"]

    rotation.activate(state_dir, "final", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_FINAL_ACTIVE
    assert source.read_bytes() == state.files["final.env"]

    rotation.role_password(state_dir, "migration-rollback", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_RUNTIME_ACTIVE
    assert source.read_bytes() == state.files["runtime.env"]

    rotation.role_password(state_dir, "runtime-rollback", container)
    assert rotation._load_state(state_dir).phase == rotation.PHASE_STAGED
    assert source.read_bytes() == state.files["previous.env"]
    assert _mode(source) == 0o600
    assert password_phases == [
        rotation.PHASE_RUNTIME_NEW_PENDING,
        rotation.PHASE_MIGRATION_NEW_PENDING,
        rotation.PHASE_MIGRATION_ROLLBACK_PENDING,
        rotation.PHASE_RUNTIME_ROLLBACK_PENDING,
    ]


def test_activation_failure_compensates_under_pending_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    rotation._set_phase(state, rotation.PHASE_RUNTIME_ROLE_APPLIED)
    verify_calls = 0
    password_phase = ""

    def fail_after_activation(
        _state: rotation._State,
        _phase: str,
        _container: str,
        *,
        require_live: bool,
    ) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2 and require_live:
            raise rotation.RotationError("synthetic post-activation failure")

    def record_compensation_phase(*_args: object, **_kwargs: object) -> None:
        nonlocal password_phase
        password_phase = rotation._load_state(state_dir).phase

    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)
    monkeypatch.setattr(rotation, "_verify_phase", fail_after_activation)
    monkeypatch.setattr(rotation, "_set_password_with_psql", record_compensation_phase)

    with pytest.raises(rotation.RotationError, match="restored to staged"):
        rotation.activate(state_dir, "runtime", "contact-ops-postgres")

    assert password_phase == rotation.PHASE_RUNTIME_NEW_PENDING
    assert rotation._load_state(state_dir).phase == rotation.PHASE_STAGED
    assert source.read_bytes() == state.files["previous.env"]


def test_recover_restores_pending_transition_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    source.write_bytes(state.files["runtime.env"])
    source.chmod(0o600)
    rotation._set_phase(
        state,
        rotation.PHASE_RUNTIME_NEW_PENDING,
        rotation.PHASE_STAGED,
    )
    observed_phase = ""

    def record_recovery_phase(*_args: object, **_kwargs: object) -> None:
        nonlocal observed_phase
        observed_phase = rotation._load_state(state_dir).phase

    monkeypatch.setattr(rotation, "_verify_local_role_contract", lambda _container: None)
    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)
    monkeypatch.setattr(rotation, "_set_password_with_psql", record_recovery_phase)
    monkeypatch.setattr(rotation, "_verify_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rotation, "RECOVERY_QUIESCENCE_SECONDS", 0)

    rotation.recover(state_dir, "contact-ops-postgres")

    assert observed_phase == rotation.PHASE_RUNTIME_NEW_PENDING
    assert rotation._load_state(state_dir).phase == rotation.PHASE_STAGED
    assert source.read_bytes() == state.files["previous.env"]


def test_outer_watchdog_leaves_pending_state_without_compensation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    password_attempts = 0
    database_touched_during_early_recover = False

    def indeterminate_password(*_args: object, **_kwargs: object) -> None:
        nonlocal password_attempts
        password_attempts += 1
        raise rotation._IndeterminateMutationError("synthetic outer watchdog")

    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)
    monkeypatch.setattr(rotation, "_verify_local_role_contract", lambda _container: None)
    monkeypatch.setattr(rotation, "_verify_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rotation, "_set_password_with_psql", indeterminate_password)

    with pytest.raises(
        rotation.RotationError,
        match="remains indeterminate in runtime-new-pending",
    ):
        rotation.role_password(state_dir, "runtime-new", "contact-ops-postgres")

    pending = rotation._load_state(state_dir)
    assert password_attempts == 1
    assert pending.phase == rotation.PHASE_RUNTIME_NEW_PENDING
    assert pending.origin_phase == rotation.PHASE_STAGED
    assert pending.updated_at >= state.updated_at
    assert source.read_bytes() == state.files["previous.env"]

    def unexpected_database_access(_container: str) -> None:
        nonlocal database_touched_during_early_recover
        database_touched_during_early_recover = True

    monkeypatch.setattr(rotation, "_verify_local_role_contract", unexpected_database_access)
    with pytest.raises(rotation.RotationError, match="container quiescence window"):
        rotation.recover(state_dir, "contact-ops-postgres")
    assert database_touched_during_early_recover is False


def test_migration_new_wrong_order_is_rejected_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    original = source.read_bytes()
    database_touched = False

    def unexpected_database_access(*_args: object, **_kwargs: object) -> None:
        nonlocal database_touched
        database_touched = True

    monkeypatch.setattr(rotation, "_verify_local_role_contract", unexpected_database_access)
    monkeypatch.setattr(rotation, "_set_password_with_psql", unexpected_database_access)

    with pytest.raises(rotation.RotationError, match="not allowed from phase staged"):
        rotation.role_password(state_dir, "migration-new", "contact-ops-postgres")

    assert database_touched is False
    assert rotation._load_state(state_dir).phase == rotation.PHASE_STAGED
    assert source.read_bytes() == original


def test_runtime_rollback_wrong_order_is_rejected_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    source.write_bytes(state.files["final.env"])
    source.chmod(0o600)
    rotation._set_phase(state, rotation.PHASE_FINAL_ACTIVE)
    database_touched = False

    def unexpected_database_access(*_args: object, **_kwargs: object) -> None:
        nonlocal database_touched
        database_touched = True

    monkeypatch.setattr(rotation, "_verify_local_role_contract", unexpected_database_access)
    monkeypatch.setattr(rotation, "_set_password_with_psql", unexpected_database_access)

    with pytest.raises(rotation.RotationError, match="not allowed from phase final-active"):
        rotation.role_password(state_dir, "runtime-rollback", "contact-ops-postgres")

    assert database_touched is False
    assert rotation._load_state(state_dir).phase == rotation.PHASE_FINAL_ACTIVE
    assert source.read_bytes() == state.files["final.env"]


def test_activate_refuses_unstaged_live_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    rotation._set_phase(state, rotation.PHASE_RUNTIME_ROLE_APPLIED)
    source.write_bytes(source.read_bytes() + b"DRIFTED=true\n")
    source.chmod(0o600)
    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)

    with pytest.raises(rotation.RotationError, match="drifted"):
        rotation.activate(state_dir, "runtime", "contact-ops-postgres")


def test_state_hash_detects_tampering(tmp_path: Path) -> None:
    _source, state_dir = _paths(tmp_path)
    rotation.stage(_source, state_dir)
    runtime_path = state_dir / "runtime.env"
    runtime_path.write_bytes(runtime_path.read_bytes() + b"TAMPERED=true\n")
    runtime_path.chmod(0o600)

    with pytest.raises(rotation.RotationError, match="integrity"):
        rotation._load_state(state_dir)


def test_rotation_lock_serializes_across_processes(tmp_path: Path) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    holder_script = """
import fcntl
import os
import sys
import time

fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
print("locked", flush=True)
time.sleep(1.0)
fcntl.flock(fd, fcntl.LOCK_UN)
os.close(fd)
"""
    holder = subprocess.Popen(  # noqa: S603 - fixed interpreter and test script.
        [sys.executable, "-c", holder_script, str(state_dir / "rotation.lock")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        with rotation._state_lock(state_dir):
            pass
        elapsed = time.monotonic() - started
        _stdout, stderr = holder.communicate(timeout=2)
        assert holder.returncode == 0, stderr
        assert elapsed >= 0.7
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=2)


def test_psql_password_change_keeps_secret_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_secret = "a" * 64
    captured: dict[str, object] = {}
    monkeypatch.setattr(rotation, "_docker_binary", lambda: "/usr/bin/docker")

    def fake_run(
        command: list[str],
        payload: bytes,
        *,
        timeout: float = 60,
        indeterminate_on_timeout: bool = False,
    ) -> bytes:
        captured.update(
            command=command,
            payload=payload,
            timeout=timeout,
            indeterminate_on_timeout=indeterminate_on_timeout,
        )
        return b""

    monkeypatch.setattr(rotation, "_run_command", fake_run)

    rotation._set_password_with_psql(
        "contact-ops-postgres", rotation.RUNTIME_ROLE, generated_secret
    )

    command = captured["command"]
    payload = captured["payload"]
    assert isinstance(command, list)
    assert isinstance(payload, bytes)
    assert all(generated_secret not in argument for argument in command)
    assert rotation.PASSWORD_MUTATION_LOCK_DIR in command
    assert any("PGCONNECT_TIMEOUT" in argument for argument in command)
    assert any("timeout -s TERM" in argument for argument in command)
    assert captured["indeterminate_on_timeout"] is True
    assert captured["timeout"] == rotation.PASSWORD_HOST_WATCHDOG_SECONDS
    assert generated_secret.encode() in payload
    assert b"\\password contact_ops_runtime" in payload


def test_tcp_probe_keeps_secret_out_of_argv_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_secret = "b" * 64
    captured: dict[str, object] = {}
    monkeypatch.setattr(rotation, "_docker_binary", lambda: "/usr/bin/docker")
    monkeypatch.setattr(rotation, "_container_tcp_host", lambda _container: "172.18.0.2")

    def fake_subprocess_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"PROBE_OK\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    result = rotation._tcp_query(
        "contact-ops-postgres",
        rotation.RUNTIME_ROLE,
        generated_secret,
        "SELECT 'PROBE_OK';\n",
    )

    assert result.status is rotation._ProbeStatus.SUCCESS
    assert result.output == "PROBE_OK"
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert all(generated_secret not in argument for argument in command)
    assert rotation.TCP_PROBE_DIRECTORY_PREFIX in command
    assert any("mktemp -d" in argument for argument in command)
    assert any("timeout -s TERM" in argument for argument in command)
    assert "env" not in kwargs
    assert kwargs["timeout"] == rotation.TCP_HOST_WATCHDOG_SECONDS
    assert generated_secret.encode() in kwargs["input"]


def test_verify_requires_live_config_to_match_phase_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    source.write_bytes(state.files["runtime.env"])
    source.chmod(0o600)
    touched_database = False

    def should_not_run(_container: str) -> None:
        nonlocal touched_database
        touched_database = True

    monkeypatch.setattr(rotation, "_verify_local_role_contract", should_not_run)

    with pytest.raises(rotation.RotationError, match="does not match"):
        rotation.verify(state_dir, "previous", "contact-ops-postgres")
    assert touched_database is False


def test_finalize_removes_only_verified_rotation_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, state_dir = _paths(tmp_path)
    rotation.stage(source, state_dir)
    state = rotation._load_state(state_dir)
    source.write_bytes(state.files["final.env"])
    source.chmod(0o600)
    rotation._set_phase(state, rotation.PHASE_FINAL_ACTIVE)
    monkeypatch.setattr(rotation, "_verify_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rotation, "_verify_retired_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)

    rotation.finalize(state_dir, "contact-ops-postgres")

    assert not state_dir.exists()
    assert source.exists()
    assert source.read_bytes() == state.files["final.env"]


def test_retired_credentials_must_both_be_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    def fake_tcp(
        _container: str, username: str, _password: str, _sql: str
    ) -> rotation._ProbeResult:
        attempts.append(username)
        return rotation._ProbeResult(rotation._ProbeStatus.AUTH_REJECTED, "")

    monkeypatch.setattr(rotation, "_tcp_query", fake_tcp)
    rotation._assert_rejected("contact-ops-postgres", rotation.RUNTIME_ROLE, "old-runtime")
    rotation._assert_rejected("contact-ops-postgres", rotation.MIGRATION_ROLE, "old-admin")
    assert attempts == [rotation.RUNTIME_ROLE, rotation.MIGRATION_ROLE]


def test_retired_credential_acceptance_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rotation,
        "_tcp_query",
        lambda *_args: rotation._ProbeResult(rotation._ProbeStatus.SUCCESS, "ACCEPTED"),
    )

    with pytest.raises(rotation.RotationError, match="still authenticates"):
        rotation._assert_rejected("contact-ops-postgres", rotation.RUNTIME_ROLE, "retired-runtime")


def test_retired_probe_generic_failure_does_not_count_as_auth_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rotation,
        "_tcp_query",
        lambda *_args: rotation._ProbeResult(rotation._ProbeStatus.FAILURE, ""),
    )

    with pytest.raises(rotation.RotationError, match="without auth rejection"):
        rotation._assert_rejected("contact-ops-postgres", rotation.RUNTIME_ROLE, "retired-runtime")


def test_stage_rejects_symlinked_source(tmp_path: Path) -> None:
    source, state_dir = _paths(tmp_path)
    real_source = source.with_name("real.env")
    source.replace(real_source)
    source.symlink_to(real_source)

    with pytest.raises(rotation.RotationError, match="symlink"):
        rotation.stage(source, state_dir)


def test_private_file_reader_rejects_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "secret-fifo"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(rotation.RotationError, match="regular file"):
        rotation._read_private_file(fifo)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_DB_ROTATION_DOCKER_TEST") != "1",
    reason="set RUN_DB_ROTATION_DOCKER_TEST=1 for the disposable Postgres gate",
)
def test_disposable_postgres_full_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    docker = rotation._docker_binary()
    container = f"contact-ops-db-rotation-it-{uuid.uuid4().hex[:12]}"
    old_runtime = "fixture-runtime-before"
    old_migration = "fixture-migration-before"
    docker_env = tmp_path / "postgres.env"
    docker_env.write_text(
        "POSTGRES_USER=contact_ops_admin\n"
        f"POSTGRES_PASSWORD={old_migration}\n"
        "POSTGRES_DB=contact_ops_db\n"
        "POSTGRES_HOST_AUTH_METHOD=scram-sha-256\n"
    )
    docker_env.chmod(0o600)
    init_sql = f"""
CREATE ROLE contact_ops_app NOLOGIN;
CREATE ROLE contact_ops_audit NOLOGIN;
CREATE ROLE contact_ops_runtime LOGIN NOBYPASSRLS
  PASSWORD '{old_runtime}' IN ROLE contact_ops_app;
CREATE TABLE tenants (id uuid PRIMARY KEY);
CREATE TABLE persons (
  id uuid PRIMARY KEY,
  canonical_owner_tenant_id uuid NOT NULL REFERENCES tenants(id)
);
CREATE TABLE action_event (id uuid PRIMARY KEY);
ALTER TABLE persons ENABLE ROW LEVEL SECURITY;
ALTER TABLE persons FORCE ROW LEVEL SECURITY;
CREATE POLICY persons_select ON persons FOR SELECT TO contact_ops_app
  USING (
    canonical_owner_tenant_id =
      NULLIF(current_setting('app.tenant_id', true), '')::uuid
  );
GRANT USAGE ON SCHEMA public TO contact_ops_app;
GRANT SELECT ON persons TO contact_ops_app;
GRANT INSERT, SELECT ON action_event TO contact_ops_app;
REVOKE DELETE ON action_event FROM contact_ops_app;
INSERT INTO tenants (id) VALUES
  ('00000000-0000-4000-8000-000000000001'),
  ('00000000-0000-4000-8000-000000000002');
INSERT INTO persons (id, canonical_owner_tenant_id) VALUES
  ('10000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000001'),
  ('10000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000002');
"""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "deployment.env"
    source.write_bytes(_sample_env(old_runtime, old_migration))
    source.chmod(0o600)
    state_parent = tmp_path / "state"
    state_parent.mkdir()
    state_dir = state_parent / "rotation"
    monkeypatch.setattr(rotation, "_confirm", lambda _expected: None)

    rotation._run_command(
        [
            docker,
            "run",
            "--rm",
            "-d",
            "--name",
            container,
            "--env-file",
            str(docker_env),
            "postgres:16-alpine",
        ],
        b"",
    )
    try:
        for _attempt in range(100):
            try:
                rotation._run_command(
                    [
                        docker,
                        "exec",
                        "-i",
                        container,
                        "psql",
                        "-X",
                        "-qAt",
                        "-U",
                        rotation.MIGRATION_ROLE,
                        "-d",
                        rotation.DB_NAME,
                    ],
                    b"SELECT 1;\n",
                )
                break
            except rotation.RotationError:
                time.sleep(0.1)
        else:
            pytest.fail("disposable Postgres did not become ready")

        rotation._run_command(
            [
                docker,
                "exec",
                "-i",
                container,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                rotation.MIGRATION_ROLE,
                "-d",
                rotation.DB_NAME,
            ],
            init_sql.encode(),
        )

        # Killing the host-side Docker CLI does not kill its container-side
        # exec. Hold the exact mutation lock in such a surviving exec and prove
        # a password action cannot race it or change the role.
        ready_marker = "/tmp/contact-ops-rotation-client-timeout-ready"
        done_marker = "/tmp/contact-ops-rotation-client-timeout-done"
        delayed_exec_script = r"""
lock_dir="$1"
ready_marker="$2"
done_marker="$3"
mkdir "$lock_dir"
cleanup() {
  rm -f "$lock_dir/owner-pid"
  rmdir "$lock_dir" 2>/dev/null || :
}
trap cleanup EXIT
printf '%s\n' "$$" >"$lock_dir/owner-pid"
touch "$ready_marker"
sleep 3
touch "$done_marker"
"""
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(  # noqa: S603 - fixed Docker test command.
                [
                    docker,
                    "exec",
                    "-i",
                    container,
                    "sh",
                    "-ceu",
                    delayed_exec_script,
                    "--",
                    rotation.PASSWORD_MUTATION_LOCK_DIR,
                    ready_marker,
                    done_marker,
                ],
                capture_output=True,
                check=False,
                timeout=0.5,
            )
        rotation._run_command([docker, "exec", container, "test", "-f", ready_marker], b"")
        collision_started = time.monotonic()
        with pytest.raises(rotation.RotationError) as lock_collision:
            rotation._set_password_with_psql(
                container,
                rotation.RUNTIME_ROLE,
                "c" * 64,
            )
        assert isinstance(lock_collision.value, rotation._IndeterminateMutationError)
        assert time.monotonic() - collision_started < 2
        rotation._verify_runtime(container, old_runtime)
        for _attempt in range(50):
            try:
                rotation._run_command([docker, "exec", container, "test", "-f", done_marker], b"")
                break
            except rotation.RotationError:
                time.sleep(0.1)
        else:
            pytest.fail("container-side exec did not survive the Docker client timeout")
        rotation._run_command(
            [
                docker,
                "exec",
                container,
                "test",
                "!",
                "-e",
                rotation.PASSWORD_MUTATION_LOCK_DIR,
            ],
            b"",
        )

        # Hold the runtime role row/object lock in PostgreSQL. The password
        # helper must exit under its inner PG/timeout bounds, before the longer
        # host watchdog, and must leave the old credential valid.
        lock_sql = f"""
BEGIN;
ALTER ROLE {rotation.RUNTIME_ROLE} PASSWORD '{old_runtime}';
SELECT pg_sleep(3) /* contact_ops_rotation_inner_deadline */;
COMMIT;
"""
        lock_holder = subprocess.Popen(  # noqa: S603 - fixed Docker test command.
            [
                docker,
                "exec",
                "-i",
                container,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                rotation.MIGRATION_ROLE,
                "-d",
                rotation.DB_NAME,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert lock_holder.stdin is not None
            lock_holder.stdin.write(lock_sql.encode())
            lock_holder.stdin.close()
            lock_holder.stdin = None
            for _attempt in range(50):
                active = rotation._admin_query(
                    container,
                    """
SELECT count(*) FROM pg_stat_activity
WHERE query LIKE '%contact_ops_rotation_inner_deadline%'
  AND wait_event = 'PgSleep';
""",
                )
                if active == "1":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("could not establish the PostgreSQL role-lock fixture")

            with monkeypatch.context() as deadline_patch:
                deadline_patch.setattr(rotation, "PASSWORD_CONTAINER_DEADLINE_SECONDS", 2)
                deadline_patch.setattr(rotation, "CONTAINER_KILL_GRACE_SECONDS", 1)
                deadline_patch.setattr(rotation, "PASSWORD_HOST_WATCHDOG_SECONDS", 6)
                deadline_patch.setattr(rotation, "PG_CONNECT_TIMEOUT_SECONDS", 1)
                deadline_patch.setattr(rotation, "PG_LOCK_TIMEOUT_MILLISECONDS", 500)
                deadline_patch.setattr(rotation, "PG_STATEMENT_TIMEOUT_MILLISECONDS", 5_000)
                deadline_started = time.monotonic()
                with pytest.raises(rotation.RotationError) as bounded_failure:
                    rotation._set_password_with_psql(
                        container,
                        rotation.RUNTIME_ROLE,
                        "d" * 64,
                    )
                deadline_elapsed = time.monotonic() - deadline_started
                assert not isinstance(bounded_failure.value, rotation._IndeterminateMutationError)
                assert 0.3 <= deadline_elapsed < 5
            lock_stdout, lock_stderr = lock_holder.communicate(timeout=5)
            assert lock_holder.returncode == 0, (lock_stdout, lock_stderr)
        finally:
            if lock_holder.poll() is None:
                lock_holder.kill()
                lock_holder.wait(timeout=2)
        rotation._verify_runtime(container, old_runtime)

        # A long TCP query must die inside the container, and the trap must
        # remove both credential and query files before the host call returns.
        with monkeypatch.context() as probe_patch:
            probe_patch.setattr(rotation, "TCP_CONTAINER_DEADLINE_SECONDS", 1)
            probe_patch.setattr(rotation, "CONTAINER_KILL_GRACE_SECONDS", 1)
            probe_patch.setattr(rotation, "TCP_HOST_WATCHDOG_SECONDS", 5)
            probe_patch.setattr(rotation, "PG_CONNECT_TIMEOUT_SECONDS", 1)
            probe_patch.setattr(rotation, "PG_LOCK_TIMEOUT_MILLISECONDS", 5_000)
            probe_patch.setattr(rotation, "PG_STATEMENT_TIMEOUT_MILLISECONDS", 5_000)
            probe_started = time.monotonic()
            probe_result = rotation._tcp_query(
                container,
                rotation.MIGRATION_ROLE,
                old_migration,
                "SELECT pg_sleep(3);\n",
            )
            probe_elapsed = time.monotonic() - probe_started
            assert probe_result.status is rotation._ProbeStatus.FAILURE
            assert 0.8 <= probe_elapsed < 4
        rotation._run_command(
            [
                docker,
                "exec",
                container,
                "sh",
                "-ceu",
                'for path in "$1".*; do test ! -e "$path" || exit 1; done',
                "--",
                rotation.TCP_PROBE_DIRECTORY_PREFIX,
            ],
            b"",
        )

        rotation.stage(source, state_dir)
        rotation.preflight(state_dir, container)

        # The historical wrong-order failure changed DBP3 while the previous
        # runtime config was still live. The journal must reject it before any
        # database access and leave the previous phase verifiable.
        with pytest.raises(rotation.RotationError, match="not allowed from phase staged"):
            rotation.role_password(state_dir, "migration-new", container)
        rotation.verify(state_dir, "previous", container)

        # Roll back both before and after runtime config activation. These are
        # separate supported recovery points in the state machine.
        rotation.role_password(state_dir, "runtime-new", container)
        rotation.role_password(state_dir, "runtime-rollback", container)
        rotation.verify(state_dir, "previous", container)

        rotation.role_password(state_dir, "runtime-new", container)
        rotation.activate(state_dir, "runtime", container)
        rotation.verify(state_dir, "runtime", container)
        rotation.role_password(state_dir, "runtime-rollback", container)
        rotation.verify(state_dir, "previous", container)

        rotation.role_password(state_dir, "runtime-new", container)
        rotation.activate(state_dir, "runtime", container)

        # Likewise, prove DBP3 rollback on both sides of final activation.
        rotation.role_password(state_dir, "migration-new", container)
        rotation.role_password(state_dir, "migration-rollback", container)
        rotation.verify(state_dir, "runtime", container)

        rotation.role_password(state_dir, "migration-new", container)
        rotation.activate(state_dir, "final", container)
        rotation.verify(state_dir, "final", container)

        with pytest.raises(rotation.RotationError, match="not allowed from phase final-active"):
            rotation.role_password(state_dir, "runtime-rollback", container)
        rotation.verify(state_dir, "final", container)

        rotation.role_password(state_dir, "migration-rollback", container)
        rotation.verify(state_dir, "runtime", container)
        rotation.role_password(state_dir, "migration-new", container)
        rotation.activate(state_dir, "final", container)
        rotation.verify(state_dir, "final", container)
        rotation.verify_retired(state_dir, container)
        rotation.finalize(state_dir, container)

        output = capsys.readouterr()
        assert old_runtime not in output.out
        assert old_migration not in output.out
        assert not state_dir.exists()
    finally:
        rotation._run_command([docker, "rm", "-f", container], b"")
