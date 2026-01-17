"""
Security middleware and authentication for Center Deep Data Intel API.

Supports multiple authentication methods:
- API keys (Bearer sk-xxx format)
- Keycloak JWT tokens
- OAuth2 Proxy forwarded headers (X-Forwarded-User)
- Standalone mode (no auth, for development)
"""

import re
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# HTTP Bearer scheme for extracting tokens
bearer_scheme = HTTPBearer(auto_error=False)

# Pattern for API key format: sk-<prefix>-<32+ alphanumeric chars>
# Supports formats: sk-xxx (legacy) or sk-cdv-xxx (Center Deep Verify)
API_KEY_PATTERN = re.compile(r"^sk-([a-z]+-)?[a-zA-Z0-9]{32,}$")


class AppContext:
    """
    Authenticated application context.

    Contains information about the authenticated app/user making the request.
    """

    def __init__(
        self,
        app_name: str,
        tier: str,
        user_id: str | None = None,
        permissions: list[str] | None = None,
        source_apps: list[str] | None = None,
    ):
        self.app_name = app_name
        self.tier = tier
        self.user_id = user_id
        self.permissions = permissions or []
        self.source_apps = source_apps or []

    def __repr__(self) -> str:
        return f"AppContext(app_name={self.app_name!r}, tier={self.tier!r}, user_id={self.user_id!r})"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "app_name": self.app_name,
            "tier": self.tier,
            "user_id": self.user_id,
            "permissions": self.permissions,
            "source_apps": self.source_apps,
        }


async def validate_api_key(api_key: str, settings: Settings) -> AppContext | None:
    """
    Validate an API key against the database.

    Args:
        api_key: The API key to validate (without Bearer prefix)
        settings: Application settings

    Returns:
        AppContext if valid, None if invalid
    """
    import hashlib
    from app.core.database import async_session_maker
    from app.core.subscription import get_user_subscription
    from app.core.rbac import get_tier_permissions

    if not API_KEY_PATTERN.match(api_key):
        return None

    # Hash the key for lookup
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        async with async_session_maker() as db:
            # Look up the key in database
            from sqlalchemy import text
            result = await db.execute(
                text("""
                    SELECT id, user_id, app_name, source_apps, permissions,
                           is_active, expires_at
                    FROM api_keys
                    WHERE key_hash = :key_hash
                """),
                {"key_hash": key_hash}
            )
            row = result.fetchone()

            if not row:
                logger.warning("API key not found", key_prefix=api_key[:15])
                return None

            key_id, user_id, app_name, source_apps, key_permissions, is_active, expires_at = row

            # Check if key is active
            if not is_active:
                logger.warning("API key is deactivated", key_prefix=api_key[:15])
                return None

            # Check expiration
            if expires_at:
                from datetime import datetime
                if datetime.utcnow() > expires_at:
                    logger.warning("API key expired", key_prefix=api_key[:15])
                    return None

            # Get user subscription
            if user_id:
                subscription = await get_user_subscription(str(user_id))

                if not subscription.is_active:
                    logger.warning("User subscription not active",
                                   user_id=str(user_id),
                                   status=subscription.status)
                    # Return None to trigger 401
                    return None

                tier = subscription.tier
                # Get permissions from tier if not overridden on key
                if not key_permissions:
                    key_permissions = await get_tier_permissions(db, tier)
            else:
                tier = "basic"
                key_permissions = key_permissions or []

            # Update last_used_at
            await db.execute(
                text("""
                    UPDATE api_keys
                    SET last_used_at = NOW(), request_count = request_count + 1
                    WHERE id = :key_id
                """),
                {"key_id": key_id}
            )
            await db.commit()

            logger.debug("API key validated",
                         key_prefix=api_key[:15],
                         user_id=str(user_id) if user_id else None,
                         tier=tier)

            return AppContext(
                app_name=app_name or "api_client",
                tier=tier,
                user_id=str(user_id) if user_id else None,
                permissions=key_permissions,
                source_apps=source_apps or [],
            )

    except Exception as e:
        logger.error("API key validation failed", error=str(e))
        return None


async def validate_jwt_token(token: str, settings: Settings) -> AppContext | None:
    """
    Validate a Keycloak JWT token and extract claims.

    Args:
        token: The JWT token to validate
        settings: Application settings

    Returns:
        AppContext if valid, None if invalid
    """
    try:
        # Import here to avoid import errors if jose is not installed
        from jose import JWTError, jwt

        if not settings.KEYCLOAK_REALM_URL:
            logger.warning("Keycloak not configured, JWT validation skipped")
            return None

        # In production, fetch JWKS from Keycloak and validate properly
        # For now, decode without verification if Keycloak is not configured

        # Decode token (in production, use proper key verification)
        payload = jwt.decode(
            token,
            options={"verify_signature": False},  # TODO: Enable in production
        )

        # Get permissions from token or use tier defaults
        tier = payload.get("tier", "standard")
        permissions = payload.get("permissions")
        if not permissions:
            from app.core.database import async_session_maker
            from app.core.rbac import get_tier_permissions
            async with async_session_maker() as db:
                permissions = await get_tier_permissions(db, tier)

        return AppContext(
            app_name=payload.get("azp", "keycloak_client"),
            tier=tier,
            user_id=payload.get("sub"),
            permissions=permissions,
        )

    except JWTError as e:
        logger.debug("JWT validation failed", error=str(e))
        return None
    except ImportError:
        logger.warning("python-jose not installed, JWT validation unavailable")
        return None


async def check_oauth2_proxy_headers(request: Request) -> AppContext | None:
    """
    Check for OAuth2 Proxy forwarded headers.

    OAuth2 Proxy sets X-Forwarded-User and other headers after successful
    authentication. This allows browser users to access the API through
    the proxy.

    Args:
        request: The incoming request

    Returns:
        AppContext if headers present, None otherwise
    """
    forwarded_user = request.headers.get("X-Forwarded-User")
    forwarded_email = request.headers.get("X-Forwarded-Email")

    if forwarded_user:
        from app.core.rbac import Permission
        logger.debug(
            "OAuth2 proxy user detected",
            user=forwarded_user,
            email=forwarded_email,
        )

        # Browser users get a default set of permissions
        browser_permissions = [
            Permission.VERIFY_SINGLE.value,
            Permission.VERIFY_BATCH.value,
            Permission.CATALOGUE_SUBMIT.value,
            Permission.CATALOGUE_SEARCH_OWN.value,
            Permission.CATALOGUE_LOOKUP_OWN.value,
        ]

        return AppContext(
            app_name="oauth2_proxy",
            tier="browser",
            user_id=forwarded_user,
            permissions=browser_permissions,
        )

    return None


async def get_current_app(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AppContext:
    """
    Get the current authenticated application context.

    This is the main authentication dependency used by route handlers.
    It supports multiple authentication methods in order of precedence:

    1. Standalone mode (no auth required)
    2. OAuth2 Proxy headers (browser users)
    3. Bearer API key (sk-xxx format)
    4. Bearer JWT token (Keycloak)

    Args:
        request: The incoming request
        credentials: Bearer token credentials if provided
        settings: Application settings

    Returns:
        AppContext with authenticated app information

    Raises:
        HTTPException: 401 if authentication fails
    """
    # 1. Check standalone mode first
    if settings.STANDALONE_MODE:
        from app.core.rbac import Permission
        logger.debug("Standalone mode enabled, skipping authentication")
        return AppContext(
            app_name="standalone",
            tier="unlimited",
            user_id="00000000-0000-0000-0000-000000000000",  # Deterministic UUID for standalone
            permissions=[p.value for p in Permission],  # All permissions in standalone
        )

    # 2. Check OAuth2 Proxy headers (browser users)
    oauth2_context = await check_oauth2_proxy_headers(request)
    if oauth2_context:
        return oauth2_context

    # 3. Require Authorization header for API access
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # 4. Try API key validation first (sk-xxx format)
    if token.startswith("sk-"):
        app_context = await validate_api_key(token, settings)
        if app_context:
            return app_context
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 5. Try JWT validation
    app_context = await validate_jwt_token(token, settings)
    if app_context:
        return app_context

    # No valid authentication found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_tier(minimum_tier: str):
    """
    Decorator/dependency factory for tier-based access control.

    Args:
        minimum_tier: The minimum tier required for access

    Returns:
        Dependency that validates the tier
    """
    tier_levels = {
        "free": 0,
        "standard": 1,
        "premium": 2,
        "enterprise": 3,
        "unlimited": 99,
        "browser": 1,  # Browser users get standard access
    }

    async def check_tier(app: AppContext = Depends(get_current_app)) -> AppContext:
        app_level = tier_levels.get(app.tier, 0)
        required_level = tier_levels.get(minimum_tier, 0)

        if app_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This endpoint requires {minimum_tier} tier or higher",
            )

        return app

    return check_tier
