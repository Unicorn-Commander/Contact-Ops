"""SSRF egress guard — validate any outbound URL before Contact-Ops fetches it.

Contact-Ops makes server-side HTTP calls on behalf of tenants (connector
webhooks, enrichment fetches, avatar/image pulls, federation hops to sibling
``-ops`` services and ``unicorn-garage``). Any user-influenced URL is an SSRF
vector: a tenant who can steer a fetch at ``http://169.254.169.254/...`` or a
private RFC-1918 host can pivot into cloud metadata or the internal network.

This module is the single chokepoint. Callers run a candidate URL through
:func:`validate_outbound_url` (or the boolean :func:`check_outbound`) before
issuing the request, and use :func:`build_ssrf_safe_client` so a 30x redirect
to a metadata address can't bypass the up-front check.

DORMANT-SAFE + SHADOW-FIRST (mirrors ``entitlement.py`` and the email/Sentry
dormant pattern):

  * The guard does nothing unless ``settings.SSRF_GUARD_ENABLED`` is True.
  * Even when enabled, ``settings.SSRF_SHADOW`` (default True) makes it
    log ``ssrf_would_block`` and ALLOW the request (return the URL). It only
    raises :class:`SSRFBlocked` once the operator flips
    ``SSRF_GUARD_ENABLED`` on AND ``SSRF_SHADOW`` off. This matches the
    ``MEMBERSHIP_GATE_ENFORCED`` / ``ENTITLEMENT_ENFORCED`` rollout caution: a
    too-aggressive allowlist would otherwise hard-break legitimate suite
    egress (sibling ops / garage on private addresses) the moment it shipped.

The module is wired into the app conditionally in ``main.py`` (not here); it
reads all of its tuning from the ``Settings`` object passed to its callables —
it never imports ``get_settings`` itself, so it stays trivially testable.

Residual TOCTOU: this is a best-effort guard. We resolve the hostname, assert
every resolved address is public, then hand the ORIGINAL url (hostname intact)
to httpx, which resolves it again at connect time. A hostile DNS server can
answer "public" on our probe and "169.254.169.254" on httpx's connect
(DNS-rebinding). Fully closing that window requires a custom httpx transport
that pins the validated IP into the socket connection; that is a deliberate
follow-up (see ``build_ssrf_safe_client``). What ships here already blocks the
overwhelming-majority static-target SSRF cases and disables redirect-based
bypass, which is correct-and-shippable now.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
import structlog

logger = structlog.get_logger(__name__)


class SSRFBlocked(Exception):
    """Raised when an outbound URL is denied by the egress guard.

    Only raised in ENFORCE mode (``SSRF_GUARD_ENABLED`` True and
    ``SSRF_SHADOW`` False). In shadow mode the offending URL is logged
    (``ssrf_would_block``) and allowed, so this never surfaces.
    """

    def __init__(self, reason: str, *, url: str | None = None) -> None:
        self.reason = reason
        self.url = url
        super().__init__(reason)


# Cloud-metadata endpoints that are NOT caught by the generic private/link-local
# checks in every form, kept explicit so the intent is auditable. The IPv4
# 169.254.169.254 IS link-local (caught anyway) but listed for clarity; the
# IPv6 fd00:ec2::254 is in the unique-local fc00::/7 block (is_private catches
# it) — both are belt-and-suspenders so a future relaxation of the generic
# checks can never silently re-expose metadata.
_METADATA_ADDRS: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS/GCP/Azure/OpenStack IMDS (IPv4)
        "fd00:ec2::254",  # AWS IMDS (IPv6)
    }
)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> str | None:
    """Return a reason string if ``ip`` is non-public, else None.

    Rejects RFC-1918 / loopback / link-local / reserved / multicast /
    unspecified ranges plus the explicit cloud-metadata addresses. An
    IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) is unwrapped first so a
    private v4 target can't sneak in wearing a v6 costume.
    """
    # Unwrap IPv4-mapped / IPv4-compatible IPv6 so e.g. ::ffff:169.254.169.254
    # is evaluated as the v4 address it really targets.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if str(ip) in _METADATA_ADDRS:
        return "cloud_metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"
    return None


def _resolve_all_ips(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve a hostname to ALL its A/AAAA addresses.

    Every returned address must pass :func:`_is_blocked_ip` for the host to be
    accepted — a DNS answer mixing one public and one private record (a classic
    SSRF trick) is rejected on the private record.

    A bare IP literal is parsed directly (getaddrinfo would echo it, but parsing
    avoids a needless syscall and normalizes bracketed v6).
    """
    stripped = host.strip("[]")
    try:
        return [ipaddress.ip_address(stripped)]
    except ValueError:
        pass

    results: list[ipaddress._BaseAddress] = []
    # AF_UNSPEC → both A and AAAA. Raises socket.gaierror on NXDOMAIN/timeout.
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        # Strip any IPv6 scope id (``fe80::1%eth0``) before parsing.
        addr = addr.split("%", 1)[0]
        try:
            results.append(ipaddress.ip_address(addr))
        except ValueError:
            # An address family we can't parse is treated as unresolvable →
            # caller fails closed.
            continue
    return results


def _evaluate(url: str, settings) -> str | None:
    """Pure policy check. Return a reason string if the URL is unsafe, else None.

    No logging, no raising — the gating wrappers (:func:`validate_outbound_url`,
    :func:`check_outbound`) own the shadow/enforce decision. Allowlisted hosts
    and the ``SSRF_ALLOW_PRIVATE_NETWORKS`` escape hatch short-circuit to
    allowed so suite-internal egress (sibling ``-ops`` / ``unicorn-garage`` /
    ``cloud.magicunicorn.dev``) keeps working.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return f"unparseable_url:{exc}"

    scheme = (parts.scheme or "").lower()
    allowed_schemes = {"https"} if getattr(settings, "SSRF_HTTPS_ONLY", False) else {"http", "https"}
    if scheme not in allowed_schemes:
        return f"scheme_not_allowed:{scheme or '(none)'}"

    # Reject any embedded credentials (user:pass@host) — both an exfil risk and
    # a parser-confusion vector (``http://expected@evil/``).
    if parts.username is not None or parts.password is not None:
        return "userinfo_present"

    host = parts.hostname
    if not host:
        return "missing_host"

    # Exact-host allowlist: suite-internal names the operator has vouched for.
    allowed_hosts = {h.lower() for h in (getattr(settings, "SSRF_ALLOWED_HOSTS", None) or [])}
    if host.lower() in allowed_hosts:
        return None

    # Operator escape hatch for fully-trusted private deployments (e.g. an
    # all-on-one-box self-host cell). When True, private targets are permitted
    # but cloud-metadata addresses are STILL refused — there is no legitimate
    # reason to fetch IMDS through this guard.
    allow_private = bool(getattr(settings, "SSRF_ALLOW_PRIVATE_NETWORKS", False))

    try:
        ips = _resolve_all_ips(host)
    except socket.gaierror as exc:
        return f"dns_resolution_failed:{exc}"
    except Exception as exc:  # noqa: BLE001 - any resolution failure → fail closed
        return f"dns_resolution_error:{exc}"

    if not ips:
        return "dns_no_records"

    for ip in ips:
        reason = _is_blocked_ip(ip)
        if reason is None:
            continue
        if reason == "cloud_metadata":
            # Never permitted, even under the private-network escape hatch.
            return f"blocked_ip:{reason}:{ip}"
        if allow_private:
            continue
        return f"blocked_ip:{reason}:{ip}"

    return None


async def validate_outbound_url(url: str, settings) -> str:
    """Validate an outbound URL; return it when safe (or when not enforcing).

    Behavior matrix (mirrors the entitlement shadow-first gate):

    ====================  ============  ===========================================
    SSRF_GUARD_ENABLED    SSRF_SHADOW   result
    ====================  ============  ===========================================
    False                 (any)         return url unchanged (guard dormant)
    True                  True          if unsafe: log ``ssrf_would_block``, ALLOW
    True                  False         if unsafe: log ``ssrf_blocked``, RAISE
    ====================  ============  ===========================================

    Safe URLs always return unchanged. Despite the ``async`` signature (so
    callers can ``await`` it uniformly and a future transport-pinning version
    can do async resolution), the current implementation does blocking DNS via
    ``socket.getaddrinfo``; resolves are small and cached by the OS, acceptable
    for the pre-flight check. A follow-up can move it to a thread executor.
    """
    if not getattr(settings, "SSRF_GUARD_ENABLED", False):
        return url

    reason = _evaluate(url, settings)
    if reason is None:
        return url

    shadow = getattr(settings, "SSRF_SHADOW", True)
    host = urlsplit(url).hostname
    if shadow:
        logger.info("ssrf_would_block", url=url, host=host, reason=reason)
        return url

    logger.warning("ssrf_blocked", url=url, host=host, reason=reason)
    raise SSRFBlocked(reason, url=url)


async def check_outbound(url: str, settings) -> tuple[bool, str | None]:
    """Boolean event hook: ``(allowed, reason)``.

    A non-raising sibling of :func:`validate_outbound_url` for callers that want
    to branch on the result (e.g. validating a redirect ``Location`` mid-stream)
    rather than catch an exception. ``allowed`` honors the same shadow/enforce
    semantics: in shadow mode an unsafe URL returns ``(True, reason)`` so the
    caller can proceed while the reason is still surfaced for logging.
    """
    if not getattr(settings, "SSRF_GUARD_ENABLED", False):
        return True, None

    reason = _evaluate(url, settings)
    if reason is None:
        return True, None

    shadow = getattr(settings, "SSRF_SHADOW", True)
    host = urlsplit(url).hostname
    if shadow:
        logger.info("ssrf_would_block", url=url, host=host, reason=reason)
        return True, reason

    logger.warning("ssrf_blocked", url=url, host=host, reason=reason)
    return False, reason


def build_ssrf_safe_client(settings, **kwargs) -> httpx.AsyncClient:
    """Construct an ``httpx.AsyncClient`` hardened against redirect-based SSRF.

    ``follow_redirects`` is forced OFF: an allowed initial URL can 30x to
    ``http://169.254.169.254/...`` and httpx, left to auto-follow, would chase
    it AFTER our pre-flight check already passed. With auto-follow disabled the
    caller sees the 30x, extracts ``Location``, and MUST re-run it through
    :func:`check_outbound` / :func:`validate_outbound_url` before fetching it.

    A sane default ``timeout`` (10s) is applied unless the caller overrides it.
    Any other ``httpx.AsyncClient`` kwarg passes through, EXCEPT
    ``follow_redirects`` which is always pinned to False (a caller override is
    ignored on purpose — re-enabling it would reopen the bypass).

    TOCTOU follow-up: a future transport-pinning version would attach a custom
    ``httpx.AsyncHTTPTransport`` whose resolver returns ONLY the IP we validated,
    closing the DNS-rebinding window between validate-time and connect-time.
    Not done here to keep this dependency-light and shippable; the pre-flight
    validation plus no-auto-redirect already covers the common cases.
    """
    kwargs.pop("follow_redirects", None)
    kwargs.setdefault("timeout", 10.0)
    return httpx.AsyncClient(follow_redirects=False, **kwargs)
