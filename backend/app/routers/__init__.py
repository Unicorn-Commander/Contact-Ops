"""
API routers for Center Deep Data Intel.

This module exports all API routers for inclusion in the main application.
"""

from app.routers.admin import router as admin_router
from app.routers.catalogue import router as catalogue_router
from app.routers.health import router as health_router
from app.routers.usage import router as usage_router
from app.routers.verify import router as verify_router

__all__ = [
    "admin_router",
    "catalogue_router",
    "health_router",
    "usage_router",
    "verify_router",
]
