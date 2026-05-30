from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contact_ops.main import create_application
from contact_ops.services.brigade_registration import build_brigade_descriptor


def test_brigade_descriptor_shape():
    descriptor = build_brigade_descriptor()
    assert descriptor["service_id"] == "contact-ops"
    assert descriptor["auth"]["pat_format"] == "co_pat_*"
    assert any(agent["slug"] == "dedup-agent" for agent in descriptor["agents"])
    assert any(tool["name"] == "issue_personal_access_token" for tool in descriptor["tools"])


def test_well_known_mcps_json_is_unauthenticated(monkeypatch):
    async def _noop(_settings=None):
        return None

    monkeypatch.setattr("contact_ops.main.register_with_brigade", _noop)
    with TestClient(create_application()) as client:
        response = client.get("/.well-known/mcps.json")
    assert response.status_code == 200
    assert response.json()["service_id"] == "contact-ops"
