"""Short-TTL in-memory OAuth state store with PKCE helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthState:
    provider: str
    uc_uid: str
    tenant_id: str
    code_verifier: str
    expires_at: float


_STATE: dict[str, OAuthState] = {}
TTL_SECONDS = 10 * 60


def create_state(provider: str, uc_uid: str, tenant_id: str) -> tuple[str, str, str]:
    _prune()
    state = str(uuid.uuid4())
    verifier = _token(64)
    _STATE[state] = OAuthState(
        provider=provider,
        uc_uid=uc_uid,
        tenant_id=tenant_id,
        code_verifier=verifier,
        expires_at=time.time() + TTL_SECONDS,
    )
    return state, verifier, code_challenge(verifier)


def consume_state(provider: str, state: str, uc_uid: str | None = None) -> OAuthState:
    _prune()
    value = _STATE.pop(state, None)
    if value is None or value.provider != provider:
        raise ValueError("invalid or expired oauth state")
    if uc_uid is not None and value.uc_uid != uc_uid:
        raise ValueError("oauth state initiator mismatch")
    return value


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _token(length: int) -> str:
    return secrets.token_urlsafe(length)[:length]


def _prune() -> None:
    now = time.time()
    for key, value in list(_STATE.items()):
        if value.expires_at <= now:
            _STATE.pop(key, None)
