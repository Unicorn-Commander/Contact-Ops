from __future__ import annotations

import httpx
import pytest

from contact_ops.connectors.icloud import validate_icloud_credentials


@pytest.mark.asyncio
async def test_icloud_validate_credentials_accepts_multistatus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(httpx.Response(207, text="<multistatus />")),
    )
    await validate_icloud_credentials("REMOVED", "app-pass")


@pytest.mark.asyncio
async def test_icloud_validate_credentials_maps_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(httpx.Response(401)),
    )
    with pytest.raises(ValueError, match="Apple says credentials invalid"):
        await validate_icloud_credentials("REMOVED", "bad-pass")


class _FakeClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def request(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return self.response
