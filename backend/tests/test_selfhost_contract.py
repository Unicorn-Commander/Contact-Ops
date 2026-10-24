from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_selfhost_compose_publishes_loopback_ports_and_owns_its_network() -> None:
    compose = _read("docker-compose.selfhost.yml")

    assert '"127.0.0.1:8501:8501"' in compose
    assert '"127.0.0.1:3000:80"' in compose
    assert '"127.0.0.1:8080:8080"' in compose
    assert "external: true" not in compose
    assert "traefik" not in compose.lower()
    assert "\n  web:" not in compose
    assert "contact-ops-keycloak:" in compose
    assert "contact-ops-keycloak-bootstrap:" in compose
    assert "contact-ops-db-bootstrap:" in compose
    assert 'STANDALONE_MODE: "false"' in compose
    assert 'http://127.0.0.1:8501/health' in compose
    assert "condition: service_completed_successfully" in compose



def test_every_selfhost_service_joins_the_private_network() -> None:
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load(_read("docker-compose.selfhost.yml"))["services"]

    for name, service in services.items():
        networks = service.get("networks") or {}
        assert "contact-ops-internal" in networks, name

def test_selfhost_keycloak_alias_matches_issuer_and_has_no_host_gateway() -> None:
    compose = _read("docker-compose.selfhost.yml")
    template = _read("deploy/selfcontained/.env.selfcontained.template")
    keycloak_block = compose.split("  contact-ops-keycloak:\n", 1)[1].split(
        "  # Creates the realm contract", 1
    )[0]
    issuer_match = re.search(
        r"^KEYCLOAK_ISSUER=(\S+)$", template, flags=re.MULTILINE
    )

    assert issuer_match is not None
    issuer_host = urlsplit(issuer_match.group(1)).hostname
    assert issuer_host == "contact-ops.localhost"
    assert "extra_hosts:" not in compose
    assert "host-gateway" not in compose
    assert (
        "    networks:\n"
        "      contact-ops-internal:\n"
        "        aliases:\n"
        "          - contact-ops.localhost\n"
    ) in keycloak_block


def test_selfhost_compose_variables_are_declared_by_the_template() -> None:
    template = _read("deploy/selfcontained/.env.selfcontained.template")
    template_keys = {
        match.group(1)
        for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=", template, flags=re.MULTILINE)
    }
    compose_keys = set()
    for compose_path in ("docker-compose.selfhost.yml", "docker-compose.selfcontained.yml"):
        compose_keys.update(
            match.group(1)
            for match in re.finditer(
                r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}", _read(compose_path)
            )
        )

    assert compose_keys <= template_keys
    assert "STANDALONE_MODE=false" in template
    assert "ENV=prod" in template
    assert "KEYCLOAK_REALM=contact-ops" in template
    assert "KEYCLOAK_ISSUER=http://contact-ops.localhost:8080/realms/contact-ops" in template
    assert "SIGNUP_ENABLED=false" in template
    assert "SIGNUP_MODE=standalone" in template
    assert "BRIGADE_REGISTRY_URL=" in template
    assert "BRIGADE_TRUSTED_ISSUER=" in template


def test_keycloak_bootstrap_dry_run_creates_local_admin_without_suite_prerequisites() -> None:
    script = REPOSITORY_ROOT / "scripts" / "keycloak_bootstrap.sh"
    secret_values = {
        "KC_ADMIN_PASS": "master-secret-must-not-print",
        "KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD": "bootstrap-secret-must-not-print",
        "KEYCLOAK_SWITCH_CLIENT_SECRET": "switch-secret-must-not-print",
    }
    environment = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "KC_SERVER_URL": "http://contact-ops.localhost:8080",
        "KC_REALM": "contact-ops",
        "KC_ADMIN_USER": "admin",
        "KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME": "admin",
        "APP_ORIGIN": "http://127.0.0.1:3000",
        "MCP_ORIGIN": "http://127.0.0.1:8501",
        "SIGNUP_ENABLED": "false",
        **secret_values,
    }

    completed = subprocess.run(  # noqa: S603 - fixed repository script and arguments.
        ["/bin/bash", str(script), "--dry-run"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "would create or update realm registrationAllowed=false" in output
    assert "would create/update bootstrap admin and assign ADMIN" in output
    for secret in secret_values.values():
        assert secret not in output

    script_text = script.read_text(encoding="utf-8").lower()
    assert "uchub" not in script_text
    assert "user-attributes" not in script_text


def test_selfhost_documents_oidc_onboarding_and_signup_behavior() -> None:
    readme = _read("README.md")

    assert "docker compose -f docker-compose.selfhost.yml up -d --build" in readme
    assert "curl -fsS http://127.0.0.1:8501/health/ready" in readme
    assert "curl -fsS http://127.0.0.1:8501/health\n" not in readme
    assert "curl -fsS -X POST http://127.0.0.1:8501/api/auth/onboard" in readme
    assert "SIGNUP_ENABLED=false" in readme
    assert "registration screen" in readme
    assert "Phase 0" not in readme
    assert "uchub" not in readme.lower()


def test_prod_selfhost_template_cannot_enable_standalone_bypass() -> None:
    from contact_ops.core.config import Settings

    with pytest.raises(ValueError, match="STANDALONE_MODE=true is not permitted"):
        Settings(
            DATABASE_URL="postgresql+asyncpg://runtime:password@localhost/contact_ops_db",
            ENV="prod",
            STANDALONE_MODE=True,
            KEYCLOAK_ISSUER="",
            CONNECTOR_ENCRYPTION_KEY="test-key",
        )
