from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_bootstrap_dry_run_redacts_runtime_database_password(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    synthetic_root = tmp_path / "cell"
    script_directory = synthetic_root / "deploy" / "cell"
    fake_bin = tmp_path / "bin"
    script_directory.mkdir(parents=True)
    fake_bin.mkdir()

    source_script = repository_root / "deploy" / "cell" / "bootstrap_cell.sh"
    copied_script = script_directory / "bootstrap_cell.sh"
    shutil.copy2(source_script, copied_script)
    (synthetic_root / "docker-compose.prod.yml").write_text(
        "services: {}\n",
        encoding="utf-8",
    )

    marker = "dry-run-marker-never-render"
    (synthetic_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://contact_ops_runtime:"
                f"{marker}@contact-ops-postgres:5432/contact_ops_db",
                "MIGRATION_DATABASE_URL=${CONTACT_OPS_DATABASE_URL}"
                "synthetic-admin-value@contact-ops-postgres:5432/contact_ops_db",
                "KEYCLOAK_ISSUER=https://keycloak.invalid/realms/test",
                "CONNECTOR_ENCRYPTION_KEY=synthetic-test-key",
                "ENV=dev",
                "STANDALONE_MODE=false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  ps)
    printf '%s\n' 'contact-ops-postgres'
    ;;
  exec)
    if [[ "$*" == *"-tAc"* ]]; then
      printf '%s\n' 'f'
    fi
    ;;
  *)
    printf 'unexpected synthetic docker invocation\n' >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    completed = subprocess.run(  # noqa: S603 - fixed shell and synthetic test script.
        ["/bin/bash", str(copied_script), "--dry-run"],
        cwd=synthetic_root,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined_output = completed.stdout + completed.stderr

    assert completed.returncode == 0, combined_output
    assert marker not in combined_output
    assert "synthetic-admin-value" not in combined_output
    assert "would create/update runtime LOGIN role 'contact_ops_runtime'" in combined_output
    assert "would grant 'contact_ops_runtime' membership in contact_ops_app" in combined_output
    assert "python -m contact_ops.ops.seed_rls_probes" in combined_output
    assert "RLS_SELFTEST_TENANT_IDS" in combined_output
    assert "would run: docker compose" in combined_output


def test_bootstrap_real_path_emits_valid_postgres_dollar_quotes_without_password_output(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    synthetic_root = tmp_path / "cell"
    script_directory = synthetic_root / "deploy" / "cell"
    fake_bin = tmp_path / "bin"
    captured_sql = tmp_path / "captured-role-create.sql"
    script_directory.mkdir(parents=True)
    fake_bin.mkdir()

    source_script = repository_root / "deploy" / "cell" / "bootstrap_cell.sh"
    copied_script = script_directory / "bootstrap_cell.sh"
    shutil.copy2(source_script, copied_script)
    (synthetic_root / "docker-compose.prod.yml").write_text(
        "services: {}\n",
        encoding="utf-8",
    )

    marker = "real-path-marker-must-never-render"
    (synthetic_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql+asyncpg://contact_ops_runtime:"
                f"{marker}@contact-ops-postgres:5432/contact_ops_db",
                "MIGRATION_DATABASE_URL=${CONTACT_OPS_DATABASE_URL}"
                "synthetic-admin-value@contact-ops-postgres:5432/contact_ops_db",
                "KEYCLOAK_ISSUER=https://keycloak.invalid/realms/test",
                "CONNECTOR_ENCRYPTION_KEY=synthetic-test-key",
                "ENV=dev",
                "STANDALONE_MODE=false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  ps)
    printf '%s\n' 'contact-ops-postgres'
    ;;
  exec)
    if [[ "$*" == *"-tAc"* ]]; then
      printf '%s\n' 'f'
    elif [[ "$*" == *"psql -U contact_ops_admin"* ]]; then
      cat > "$CAPTURED_SQL_PATH"
    elif [[ "$*" == *"contact-ops-backend curl"* ]]; then
      exit 0
    else
      printf 'unexpected synthetic docker exec invocation\n' >&2
      exit 1
    fi
    ;;
  compose)
    # alembic and `up` stay silent. The seed command's stdout is the only
    # contract bootstrap parses, and it must not include the database URL.
    if [[ "$*" == *"seed_rls_probes"* ]]; then
      printf '%s\n' '11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222'
      printf '%s\n' 'RLS_SELFTEST_TENANT_IDS=11111111-1111-4111-8111-111111111111,22222222-2222-4222-8222-222222222222'
    fi
    exit 0
    ;;
  *)
    printf 'unexpected synthetic docker invocation\n' >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    completed = subprocess.run(  # noqa: S603 - fixed shell and synthetic test script.
        ["/bin/bash", str(copied_script)],
        cwd=synthetic_root,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "LC_ALL": "C",
            "CAPTURED_SQL_PATH": str(captured_sql),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    combined_output = completed.stdout + completed.stderr

    assert completed.returncode == 0, combined_output
    assert marker not in combined_output
    assert "synthetic-admin-value" not in combined_output
    updated_env = (synthetic_root / ".env").read_text(encoding="utf-8")
    assert marker in updated_env
    assert (
        "RLS_SELFTEST_TENANT_IDS=11111111-1111-4111-8111-111111111111,"
        "22222222-2222-4222-8222-222222222222"
    ) in updated_env
    emitted_sql = captured_sql.read_text(encoding="utf-8")
    assert marker in emitted_sql  # Proves the real password-bearing SQL path ran.
    assert emitted_sql.count("DO $$") == 4
    assert emitted_sql.count("END $$;") == 4
    assert "DO \\$\\$" not in emitted_sql
