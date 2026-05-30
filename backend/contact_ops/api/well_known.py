from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from contact_ops.services.brigade_registration import build_brigade_descriptor

router = APIRouter(tags=["Discovery"])


@router.get("/.well-known/mcps.json", include_in_schema=False)
async def mcps_json() -> dict[str, Any]:
    return build_brigade_descriptor()
