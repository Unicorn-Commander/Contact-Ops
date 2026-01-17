"""
SMTP Email Verification Engine for Center Deep Data Intel.

This is PRIORITY #1 - the core verification engine that must work reliably.

Features:
- RFC 5322 email syntax validation
- DNS MX record verification
- SMTP handshake verification (RCPT TO without sending)
- Catch-all domain detection
- Disposable email domain detection
- Role-based email detection
- Risk score calculation (0.0 to 1.0)
- Timeout and network error handling
- Both async and sync verification support
- Batch verification with concurrency control
- Greylisting detection with retry logic
- Rate limiting awareness

Author: Center Deep Data Intel Team
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import socket
import smtplib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from email.utils import parseaddr
from typing import Any, Optional, Sequence

# DNS resolution
try:
    import dns.resolver
    import dns.exception
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

# Async SMTP
try:
    import aiosmtplib
    ASYNC_SMTP_AVAILABLE = True
except ImportError:
    ASYNC_SMTP_AVAILABLE = False

from .disposable_domains import is_disposable_domain

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================================
# Configuration Constants
# ============================================================================

# Timeouts (in seconds)
DEFAULT_DNS_TIMEOUT: float = 5.0
DEFAULT_SMTP_TIMEOUT: float = 10.0
DEFAULT_CONNECT_TIMEOUT: float = 5.0

# Concurrency limits
DEFAULT_MAX_CONCURRENCY: int = 10
DEFAULT_BATCH_SIZE: int = 100

# Retry configuration
MAX_RETRIES: int = 2
RETRY_DELAY_BASE: float = 1.0
GREYLIST_RETRY_DELAY: float = 5.0

# SMTP response codes
SMTP_CODE_OK = 250
SMTP_CODE_USER_NOT_LOCAL = 251
SMTP_CODE_MAILBOX_UNAVAILABLE = 550
SMTP_CODE_USER_NOT_FOUND = 551
SMTP_CODE_MAILBOX_FULL = 552
SMTP_CODE_INVALID_MAILBOX = 553
SMTP_CODE_TRANSACTION_FAILED = 554
SMTP_CODE_TEMP_FAILURE = 450
SMTP_CODE_SERVICE_UNAVAILABLE = 421

# Greylisting codes (temporary failures that may resolve on retry)
GREYLIST_CODES = {421, 450, 451, 452}

# Role-based email prefixes (commonly generic/shared mailboxes)
ROLE_BASED_PREFIXES: frozenset[str] = frozenset({
    "abuse", "admin", "administrator", "billing", "contact", "contacts",
    "customerservice", "enquiries", "enquiry", "feedback", "hello",
    "help", "helpdesk", "hi", "hostmaster", "info", "information",
    "jobs", "legal", "mail", "mailer-daemon", "marketing", "media",
    "news", "noc", "noreply", "no-reply", "null", "office", "orders",
    "postmaster", "press", "privacy", "remove", "request", "root",
    "sales", "security", "service", "spam", "subscribe", "support",
    "sysadmin", "team", "tech", "technical", "unsubscribe", "usenet",
    "uucp", "webmaster", "www",
})

# RFC 5322 compliant email regex pattern
# This is a simplified but robust pattern that handles most valid emails
EMAIL_REGEX = re.compile(
    r"^(?P<local>[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*)"
    r"@"
    r"(?P<domain>(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})$"
)

# HELO/EHLO hostname for SMTP sessions
DEFAULT_HELO_HOSTNAME = "mail.centerdeep.online"

# Sender email for MAIL FROM command
DEFAULT_SENDER_EMAIL = "REMOVED"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class VerificationResult:
    """
    Complete result of an email verification check.

    Attributes:
        email: The email address that was verified
        is_valid: Overall validity assessment (combines all checks)
        syntax_valid: Whether the email syntax is valid (RFC 5322)
        mx_valid: Whether valid MX records exist for the domain
        smtp_verified: Whether SMTP verification succeeded (None if not performed)
        is_catchall: Whether the domain accepts all addresses (None if unknown)
        is_disposable: Whether this is a known disposable email domain
        is_role_based: Whether this is a role-based address (info@, admin@, etc)
        risk_score: Risk assessment (0.0 = safe, 1.0 = high risk)
        mx_records: List of MX hostnames found
        smtp_response_code: The SMTP response code (0 if not checked)
        smtp_response_message: The SMTP response message
        verification_time_ms: Total time taken in milliseconds
        checks_performed: List of check names that were performed
        error: Error message if verification failed
    """
    email: str
    is_valid: bool = False
    syntax_valid: bool = False
    mx_valid: bool = False
    smtp_verified: Optional[bool] = None
    is_catchall: Optional[bool] = None
    is_disposable: bool = False
    is_role_based: bool = False
    risk_score: float = 1.0
    mx_records: list[str] = field(default_factory=list)
    smtp_response_code: int = 0
    smtp_response_message: str = ""
    verification_time_ms: int = 0
    checks_performed: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return asdict(self)


@dataclass
class VerificationConfig:
    """
    Configuration for email verification.

    Attributes:
        check_mx: Whether to check MX records
        check_smtp: Whether to perform SMTP verification
        check_catchall: Whether to detect catch-all domains
        dns_timeout: Timeout for DNS lookups in seconds
        smtp_timeout: Timeout for SMTP operations in seconds
        connect_timeout: Timeout for initial connection in seconds
        helo_hostname: Hostname to use in HELO/EHLO command
        sender_email: Email to use in MAIL FROM command
        max_retries: Maximum number of retries for transient failures
        retry_on_greylist: Whether to retry on greylisting responses
    """
    check_mx: bool = True
    check_smtp: bool = True
    check_catchall: bool = True
    dns_timeout: float = DEFAULT_DNS_TIMEOUT
    smtp_timeout: float = DEFAULT_SMTP_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    helo_hostname: str = DEFAULT_HELO_HOSTNAME
    sender_email: str = DEFAULT_SENDER_EMAIL
    max_retries: int = MAX_RETRIES
    retry_on_greylist: bool = True


# ============================================================================
# Email Syntax Validation
# ============================================================================

def validate_email_syntax(email: str) -> tuple[bool, str, str]:
    """
    Validate email syntax according to RFC 5322.

    Args:
        email: The email address to validate

    Returns:
        Tuple of (is_valid, local_part, domain)
    """
    if not email or not isinstance(email, str):
        return False, "", ""

    # Clean whitespace
    email = email.strip().lower()

    # Basic length checks
    if len(email) > 254:  # RFC 5321 limit
        return False, "", ""

    # Parse with email.utils for additional validation
    _, parsed_email = parseaddr(email)
    if not parsed_email or parsed_email != email:
        # If parseaddr modified the email, it might have issues
        pass

    # Match against regex
    match = EMAIL_REGEX.match(email)
    if not match:
        return False, "", ""

    local_part = match.group("local")
    domain = match.group("domain")

    # Additional local part validation
    if len(local_part) > 64:  # RFC 5321 limit
        return False, "", ""

    # Additional domain validation
    if len(domain) > 253:
        return False, "", ""

    return True, local_part, domain


def is_role_based_email(local_part: str) -> bool:
    """
    Check if the local part indicates a role-based email.

    Args:
        local_part: The part before @ in the email

    Returns:
        True if this is a role-based email address
    """
    if not local_part:
        return False

    # Normalize by removing common separators
    normalized = local_part.lower().replace(".", "").replace("-", "").replace("_", "")

    return normalized in ROLE_BASED_PREFIXES or local_part.lower() in ROLE_BASED_PREFIXES


# ============================================================================
# DNS MX Record Checking
# ============================================================================

def get_mx_records(
    domain: str,
    timeout: float = DEFAULT_DNS_TIMEOUT
) -> tuple[bool, list[str], str]:
    """
    Get MX records for a domain.

    Args:
        domain: The domain to look up
        timeout: DNS query timeout in seconds

    Returns:
        Tuple of (success, mx_hosts, error_message)
    """
    if not DNS_AVAILABLE:
        return False, [], "dnspython not installed"

    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout

        # Query MX records
        mx_records = resolver.resolve(domain, "MX")

        # Sort by priority and extract hostnames
        sorted_records = sorted(mx_records, key=lambda r: r.preference)
        mx_hosts = [str(r.exchange).rstrip(".") for r in sorted_records]

        if mx_hosts:
            return True, mx_hosts, ""

        # No MX records, fall back to A record
        try:
            a_records = resolver.resolve(domain, "A")
            if a_records:
                return True, [domain], "No MX records, using A record"
        except dns.exception.DNSException:
            pass

        return False, [], "No MX or A records found"

    except dns.resolver.NXDOMAIN:
        return False, [], "Domain does not exist"
    except dns.resolver.NoAnswer:
        # Try A record fallback
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            a_records = resolver.resolve(domain, "A")
            if a_records:
                return True, [domain], "No MX records, using A record"
        except dns.exception.DNSException:
            pass
        return False, [], "No MX records found"
    except dns.resolver.NoNameservers:
        return False, [], "No nameservers available"
    except dns.exception.Timeout:
        return False, [], "DNS lookup timed out"
    except dns.exception.DNSException as e:
        return False, [], f"DNS error: {str(e)}"


async def get_mx_records_async(
    domain: str,
    timeout: float = DEFAULT_DNS_TIMEOUT
) -> tuple[bool, list[str], str]:
    """
    Async version of MX record lookup.

    Args:
        domain: The domain to look up
        timeout: DNS query timeout in seconds

    Returns:
        Tuple of (success, mx_hosts, error_message)
    """
    # Run in executor to avoid blocking
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: get_mx_records(domain, timeout)
    )


# ============================================================================
# SMTP Verification (Synchronous)
# ============================================================================

def verify_email_smtp(
    email: str,
    mx_hosts: list[str],
    config: VerificationConfig
) -> tuple[bool, int, str, Optional[bool]]:
    """
    Verify email via SMTP RCPT TO command.

    Args:
        email: The email address to verify
        mx_hosts: List of MX hosts to try
        config: Verification configuration

    Returns:
        Tuple of (verified, smtp_code, smtp_message, is_catchall)
    """
    if not mx_hosts:
        return False, 0, "No MX hosts provided", None

    last_error = ""
    last_code = 0

    for mx_host in mx_hosts[:3]:  # Try first 3 MX hosts
        for attempt in range(config.max_retries + 1):
            try:
                result = _smtp_check_single(
                    email=email,
                    mx_host=mx_host,
                    config=config
                )

                verified, code, message, is_catchall = result

                # If we got a definitive answer, return it
                if verified or code in (SMTP_CODE_MAILBOX_UNAVAILABLE,
                                        SMTP_CODE_USER_NOT_FOUND,
                                        SMTP_CODE_INVALID_MAILBOX):
                    return verified, code, message, is_catchall

                # If greylisting, wait and retry
                if code in GREYLIST_CODES and config.retry_on_greylist:
                    if attempt < config.max_retries:
                        time.sleep(GREYLIST_RETRY_DELAY * (attempt + 1))
                        continue

                last_code = code
                last_error = message
                break  # Move to next MX host

            except socket.timeout:
                last_error = "Connection timed out"
                logger.debug(f"SMTP timeout for {mx_host}: {last_error}")
            except socket.gaierror as e:
                last_error = f"DNS resolution failed: {e}"
                logger.debug(f"SMTP DNS error for {mx_host}: {last_error}")
            except smtplib.SMTPServerDisconnected:
                last_error = "Server disconnected unexpectedly"
                logger.debug(f"SMTP disconnect for {mx_host}: {last_error}")
            except smtplib.SMTPConnectError as e:
                last_error = f"Connection error: {e}"
                logger.debug(f"SMTP connect error for {mx_host}: {last_error}")
            except smtplib.SMTPException as e:
                last_error = str(e)
                logger.debug(f"SMTP error for {mx_host}: {last_error}")
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.warning(f"SMTP unexpected error for {mx_host}: {last_error}")

            # Exponential backoff between retries
            if attempt < config.max_retries:
                time.sleep(RETRY_DELAY_BASE * (2 ** attempt))

    return False, last_code, last_error, None


def _smtp_check_single(
    email: str,
    mx_host: str,
    config: VerificationConfig
) -> tuple[bool, int, str, Optional[bool]]:
    """
    Perform SMTP check against a single MX host.

    Args:
        email: Email to verify
        mx_host: MX host to connect to
        config: Verification configuration

    Returns:
        Tuple of (verified, code, message, is_catchall)
    """
    is_catchall: Optional[bool] = None

    smtp = smtplib.SMTP(timeout=config.smtp_timeout)

    try:
        # Connect
        smtp.connect(mx_host, 25)

        # Send EHLO/HELO
        code, message = smtp.ehlo(config.helo_hostname)
        if code >= 400:
            code, message = smtp.helo(config.helo_hostname)
            if code >= 400:
                return False, code, message.decode() if isinstance(message, bytes) else str(message), None

        # MAIL FROM
        code, message = smtp.mail(config.sender_email)
        if code >= 400:
            msg = message.decode() if isinstance(message, bytes) else str(message)
            return False, code, msg, None

        # RCPT TO - the actual verification
        code, message = smtp.rcpt(email)
        msg = message.decode() if isinstance(message, bytes) else str(message)

        # Check for catch-all if configured
        if config.check_catchall and code == SMTP_CODE_OK:
            is_catchall = _check_catchall(smtp, email)

        # Reset the transaction
        try:
            smtp.rset()
        except smtplib.SMTPException:
            pass

        # Interpret the result
        if code == SMTP_CODE_OK or code == SMTP_CODE_USER_NOT_LOCAL:
            return True, code, msg, is_catchall
        elif code in (SMTP_CODE_MAILBOX_UNAVAILABLE, SMTP_CODE_USER_NOT_FOUND,
                      SMTP_CODE_INVALID_MAILBOX, SMTP_CODE_TRANSACTION_FAILED):
            return False, code, msg, is_catchall
        elif code in GREYLIST_CODES:
            return False, code, msg, is_catchall
        else:
            # Unknown code, treat as inconclusive
            return False, code, msg, is_catchall

    finally:
        try:
            smtp.quit()
        except smtplib.SMTPException:
            pass
        try:
            smtp.close()
        except Exception:
            pass


def _check_catchall(smtp: smtplib.SMTP, real_email: str) -> bool:
    """
    Check if domain is a catch-all by testing a random non-existent address.

    Args:
        smtp: Active SMTP connection
        real_email: The real email to extract domain from

    Returns:
        True if domain appears to be catch-all
    """
    try:
        domain = real_email.split("@")[1]

        # Generate random non-existent address
        random_local = f"verify_test_{random.randint(100000, 999999)}_{int(time.time())}"
        random_email = f"{random_local}@{domain}"

        code, _ = smtp.rcpt(random_email)

        # If random address is accepted, it's likely catch-all
        return code == SMTP_CODE_OK or code == SMTP_CODE_USER_NOT_LOCAL

    except Exception as e:
        logger.debug(f"Catch-all check failed: {e}")
        return False


# ============================================================================
# SMTP Verification (Asynchronous)
# ============================================================================

async def verify_email_smtp_async(
    email: str,
    mx_hosts: list[str],
    config: VerificationConfig
) -> tuple[bool, int, str, Optional[bool]]:
    """
    Async version of SMTP email verification.

    Args:
        email: The email address to verify
        mx_hosts: List of MX hosts to try
        config: Verification configuration

    Returns:
        Tuple of (verified, smtp_code, smtp_message, is_catchall)
    """
    if not ASYNC_SMTP_AVAILABLE:
        # Fall back to sync version in executor
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: verify_email_smtp(email, mx_hosts, config)
        )

    if not mx_hosts:
        return False, 0, "No MX hosts provided", None

    last_error = ""
    last_code = 0

    for mx_host in mx_hosts[:3]:
        for attempt in range(config.max_retries + 1):
            try:
                result = await _smtp_check_single_async(
                    email=email,
                    mx_host=mx_host,
                    config=config
                )

                verified, code, message, is_catchall = result

                if verified or code in (SMTP_CODE_MAILBOX_UNAVAILABLE,
                                        SMTP_CODE_USER_NOT_FOUND,
                                        SMTP_CODE_INVALID_MAILBOX):
                    return verified, code, message, is_catchall

                if code in GREYLIST_CODES and config.retry_on_greylist:
                    if attempt < config.max_retries:
                        await asyncio.sleep(GREYLIST_RETRY_DELAY * (attempt + 1))
                        continue

                last_code = code
                last_error = message
                break

            except asyncio.TimeoutError:
                last_error = "Connection timed out"
                logger.debug(f"Async SMTP timeout for {mx_host}")
            except aiosmtplib.SMTPConnectError as e:
                last_error = f"Connection error: {e}"
                logger.debug(f"Async SMTP connect error for {mx_host}: {e}")
            except aiosmtplib.SMTPException as e:
                last_error = str(e)
                logger.debug(f"Async SMTP error for {mx_host}: {e}")
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.warning(f"Async SMTP unexpected error for {mx_host}: {e}")

            if attempt < config.max_retries:
                await asyncio.sleep(RETRY_DELAY_BASE * (2 ** attempt))

    return False, last_code, last_error, None


async def _smtp_check_single_async(
    email: str,
    mx_host: str,
    config: VerificationConfig
) -> tuple[bool, int, str, Optional[bool]]:
    """
    Async SMTP check against a single MX host.

    Args:
        email: Email to verify
        mx_host: MX host to connect to
        config: Verification configuration

    Returns:
        Tuple of (verified, code, message, is_catchall)
    """
    is_catchall: Optional[bool] = None

    smtp = aiosmtplib.SMTP(
        hostname=mx_host,
        port=25,
        timeout=config.smtp_timeout
    )

    try:
        await smtp.connect()

        # EHLO
        response = await smtp.ehlo(config.helo_hostname)
        if response.code >= 400:
            response = await smtp.helo(config.helo_hostname)
            if response.code >= 400:
                return False, response.code, response.message, None

        # MAIL FROM
        response = await smtp.mail(config.sender_email)
        if response.code >= 400:
            return False, response.code, response.message, None

        # RCPT TO
        response = await smtp.rcpt(email)

        # Check catch-all
        if config.check_catchall and response.code == SMTP_CODE_OK:
            is_catchall = await _check_catchall_async(smtp, email)

        # Reset
        try:
            await smtp.rset()
        except aiosmtplib.SMTPException:
            pass

        # Interpret result
        if response.code == SMTP_CODE_OK or response.code == SMTP_CODE_USER_NOT_LOCAL:
            return True, response.code, response.message, is_catchall
        elif response.code in (SMTP_CODE_MAILBOX_UNAVAILABLE, SMTP_CODE_USER_NOT_FOUND,
                               SMTP_CODE_INVALID_MAILBOX, SMTP_CODE_TRANSACTION_FAILED):
            return False, response.code, response.message, is_catchall
        else:
            return False, response.code, response.message, is_catchall

    finally:
        try:
            await smtp.quit()
        except Exception:
            pass


async def _check_catchall_async(smtp: aiosmtplib.SMTP, real_email: str) -> bool:
    """
    Async catch-all detection.

    Args:
        smtp: Active async SMTP connection
        real_email: The real email to extract domain from

    Returns:
        True if domain appears to be catch-all
    """
    try:
        domain = real_email.split("@")[1]
        random_local = f"verify_test_{random.randint(100000, 999999)}_{int(time.time())}"
        random_email = f"{random_local}@{domain}"

        response = await smtp.rcpt(random_email)
        return response.code == SMTP_CODE_OK or response.code == SMTP_CODE_USER_NOT_LOCAL

    except Exception as e:
        logger.debug(f"Async catch-all check failed: {e}")
        return False


# ============================================================================
# Risk Score Calculation
# ============================================================================

def calculate_risk_score(
    syntax_valid: bool,
    mx_valid: bool,
    smtp_verified: Optional[bool],
    is_catchall: Optional[bool],
    is_disposable: bool,
    is_role_based: bool,
    smtp_code: int
) -> float:
    """
    Calculate a risk score for the email address.

    Args:
        syntax_valid: Whether syntax is valid
        mx_valid: Whether MX records exist
        smtp_verified: SMTP verification result (None if not checked)
        is_catchall: Whether domain is catch-all (None if unknown)
        is_disposable: Whether domain is disposable
        is_role_based: Whether email is role-based
        smtp_code: SMTP response code

    Returns:
        Risk score from 0.0 (safe) to 1.0 (risky)
    """
    # Start with base risk
    risk = 0.0

    # Invalid syntax is maximum risk
    if not syntax_valid:
        return 1.0

    # No MX records is very high risk
    if not mx_valid:
        return 0.95

    # Disposable domains are high risk
    if is_disposable:
        risk += 0.5

    # Role-based addresses have moderate risk (higher bounce potential)
    if is_role_based:
        risk += 0.15

    # Catch-all domains have moderate risk
    if is_catchall:
        risk += 0.20
    elif is_catchall is None:
        # Unknown catch-all status adds slight risk
        risk += 0.05

    # SMTP verification results
    if smtp_verified is True:
        # Verified - reduce risk
        risk -= 0.15
    elif smtp_verified is False:
        # Rejected - high risk
        risk += 0.45
    else:
        # Not checked - moderate uncertainty
        risk += 0.10

    # Specific SMTP codes
    if smtp_code in (SMTP_CODE_MAILBOX_UNAVAILABLE, SMTP_CODE_USER_NOT_FOUND):
        risk += 0.2  # Definitely bad
    elif smtp_code in GREYLIST_CODES:
        risk += 0.1  # Temporary issue

    # Clamp to valid range
    return max(0.0, min(1.0, risk))


# ============================================================================
# Main Verification Functions
# ============================================================================

def verify_email(
    email: str,
    config: Optional[VerificationConfig] = None
) -> VerificationResult:
    """
    Perform complete email verification (synchronous).

    This is the main entry point for synchronous email verification.

    Args:
        email: The email address to verify
        config: Optional verification configuration

    Returns:
        VerificationResult with all verification data
    """
    start_time = time.time()
    config = config or VerificationConfig()
    checks_performed = []

    result = VerificationResult(email=email)

    try:
        # Step 1: Syntax validation
        checks_performed.append("syntax")
        syntax_valid, local_part, domain = validate_email_syntax(email)
        result.syntax_valid = syntax_valid

        if not syntax_valid:
            result.error = "Invalid email syntax"
            result.checks_performed = checks_performed
            result.verification_time_ms = int((time.time() - start_time) * 1000)
            result.risk_score = 1.0
            return result

        # Step 2: Check for disposable domain
        checks_performed.append("disposable")
        result.is_disposable = is_disposable_domain(domain)

        # Step 3: Check for role-based email
        checks_performed.append("role_based")
        result.is_role_based = is_role_based_email(local_part)

        # Step 4: MX record lookup
        if config.check_mx:
            checks_performed.append("mx_lookup")
            mx_valid, mx_hosts, mx_error = get_mx_records(domain, config.dns_timeout)
            result.mx_valid = mx_valid
            result.mx_records = mx_hosts

            if not mx_valid:
                result.error = mx_error
                result.is_valid = False
                result.checks_performed = checks_performed
                result.verification_time_ms = int((time.time() - start_time) * 1000)
                result.risk_score = calculate_risk_score(
                    syntax_valid=True,
                    mx_valid=False,
                    smtp_verified=None,
                    is_catchall=None,
                    is_disposable=result.is_disposable,
                    is_role_based=result.is_role_based,
                    smtp_code=0
                )
                return result
        else:
            # Skip MX check - assume valid
            result.mx_valid = True

        # Step 5: SMTP verification
        if config.check_smtp and result.mx_records:
            checks_performed.append("smtp_verify")
            smtp_verified, smtp_code, smtp_message, is_catchall = verify_email_smtp(
                email=email,
                mx_hosts=result.mx_records,
                config=config
            )

            result.smtp_verified = smtp_verified
            result.smtp_response_code = smtp_code
            result.smtp_response_message = smtp_message
            result.is_catchall = is_catchall

            if not smtp_verified and smtp_code in (SMTP_CODE_MAILBOX_UNAVAILABLE,
                                                    SMTP_CODE_USER_NOT_FOUND,
                                                    SMTP_CODE_INVALID_MAILBOX):
                result.error = f"SMTP rejected: {smtp_message}"

        # Step 6: Calculate risk score
        result.risk_score = calculate_risk_score(
            syntax_valid=result.syntax_valid,
            mx_valid=result.mx_valid,
            smtp_verified=result.smtp_verified,
            is_catchall=result.is_catchall,
            is_disposable=result.is_disposable,
            is_role_based=result.is_role_based,
            smtp_code=result.smtp_response_code
        )

        # Step 7: Determine overall validity
        result.is_valid = (
            result.syntax_valid and
            result.mx_valid and
            (result.smtp_verified is not False) and  # True or None is acceptable
            result.risk_score < 0.8
        )

    except Exception as e:
        logger.exception(f"Verification error for {email}")
        result.error = str(e)
        result.risk_score = 1.0

    result.checks_performed = checks_performed
    result.verification_time_ms = int((time.time() - start_time) * 1000)
    return result


async def verify_email_async(
    email: str,
    config: Optional[VerificationConfig] = None
) -> VerificationResult:
    """
    Perform complete email verification (asynchronous).

    This is the main entry point for async email verification.

    Args:
        email: The email address to verify
        config: Optional verification configuration

    Returns:
        VerificationResult with all verification data
    """
    start_time = time.time()
    config = config or VerificationConfig()
    checks_performed = []

    result = VerificationResult(email=email)

    try:
        # Step 1: Syntax validation
        checks_performed.append("syntax")
        syntax_valid, local_part, domain = validate_email_syntax(email)
        result.syntax_valid = syntax_valid

        if not syntax_valid:
            result.error = "Invalid email syntax"
            result.checks_performed = checks_performed
            result.verification_time_ms = int((time.time() - start_time) * 1000)
            result.risk_score = 1.0
            return result

        # Step 2: Check for disposable domain
        checks_performed.append("disposable")
        result.is_disposable = is_disposable_domain(domain)

        # Step 3: Check for role-based email
        checks_performed.append("role_based")
        result.is_role_based = is_role_based_email(local_part)

        # Step 4: MX record lookup
        if config.check_mx:
            checks_performed.append("mx_lookup")
            mx_valid, mx_hosts, mx_error = await get_mx_records_async(domain, config.dns_timeout)
            result.mx_valid = mx_valid
            result.mx_records = mx_hosts

            if not mx_valid:
                result.error = mx_error
                result.is_valid = False
                result.checks_performed = checks_performed
                result.verification_time_ms = int((time.time() - start_time) * 1000)
                result.risk_score = calculate_risk_score(
                    syntax_valid=True,
                    mx_valid=False,
                    smtp_verified=None,
                    is_catchall=None,
                    is_disposable=result.is_disposable,
                    is_role_based=result.is_role_based,
                    smtp_code=0
                )
                return result
        else:
            result.mx_valid = True

        # Step 5: SMTP verification
        if config.check_smtp and result.mx_records:
            checks_performed.append("smtp_verify")
            smtp_verified, smtp_code, smtp_message, is_catchall = await verify_email_smtp_async(
                email=email,
                mx_hosts=result.mx_records,
                config=config
            )

            result.smtp_verified = smtp_verified
            result.smtp_response_code = smtp_code
            result.smtp_response_message = smtp_message
            result.is_catchall = is_catchall

            if not smtp_verified and smtp_code in (SMTP_CODE_MAILBOX_UNAVAILABLE,
                                                    SMTP_CODE_USER_NOT_FOUND,
                                                    SMTP_CODE_INVALID_MAILBOX):
                result.error = f"SMTP rejected: {smtp_message}"

        # Step 6: Calculate risk score
        result.risk_score = calculate_risk_score(
            syntax_valid=result.syntax_valid,
            mx_valid=result.mx_valid,
            smtp_verified=result.smtp_verified,
            is_catchall=result.is_catchall,
            is_disposable=result.is_disposable,
            is_role_based=result.is_role_based,
            smtp_code=result.smtp_response_code
        )

        # Step 7: Determine overall validity
        result.is_valid = (
            result.syntax_valid and
            result.mx_valid and
            (result.smtp_verified is not False) and
            result.risk_score < 0.8
        )

    except Exception as e:
        logger.exception(f"Async verification error for {email}")
        result.error = str(e)
        result.risk_score = 1.0

    result.checks_performed = checks_performed
    result.verification_time_ms = int((time.time() - start_time) * 1000)
    return result


# ============================================================================
# Batch Verification
# ============================================================================

def verify_emails_batch(
    emails: Sequence[str],
    config: Optional[VerificationConfig] = None,
    max_workers: int = DEFAULT_MAX_CONCURRENCY,
    progress_callback: Optional[callable] = None
) -> list[VerificationResult]:
    """
    Verify multiple emails with concurrency control (synchronous).

    Args:
        emails: Sequence of email addresses to verify
        config: Optional verification configuration
        max_workers: Maximum concurrent workers
        progress_callback: Optional callback(completed, total) for progress updates

    Returns:
        List of VerificationResult objects in same order as input
    """
    config = config or VerificationConfig()
    results: dict[int, VerificationResult] = {}
    total = len(emails)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all verification tasks
        future_to_index = {
            executor.submit(verify_email, email, config): idx
            for idx, email in enumerate(emails)
        }

        # Collect results as they complete
        completed = 0
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = VerificationResult(
                    email=emails[idx],
                    error=str(e),
                    risk_score=1.0
                )

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    # Return results in original order
    return [results[i] for i in range(total)]


async def verify_emails_batch_async(
    emails: Sequence[str],
    config: Optional[VerificationConfig] = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    progress_callback: Optional[callable] = None
) -> list[VerificationResult]:
    """
    Verify multiple emails with concurrency control (asynchronous).

    Args:
        emails: Sequence of email addresses to verify
        config: Optional verification configuration
        max_concurrency: Maximum concurrent verifications
        progress_callback: Optional async callback(completed, total) for progress

    Returns:
        List of VerificationResult objects in same order as input
    """
    config = config or VerificationConfig()
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[Optional[VerificationResult]] = [None] * len(emails)
    completed = 0
    total = len(emails)

    async def verify_with_semaphore(idx: int, email: str):
        nonlocal completed
        async with semaphore:
            try:
                result = await verify_email_async(email, config)
            except Exception as e:
                result = VerificationResult(
                    email=email,
                    error=str(e),
                    risk_score=1.0
                )

            results[idx] = result
            completed += 1

            if progress_callback:
                await progress_callback(completed, total)

    # Create and run all tasks
    tasks = [
        verify_with_semaphore(idx, email)
        for idx, email in enumerate(emails)
    ]

    await asyncio.gather(*tasks)

    return results


# ============================================================================
# Convenience Functions
# ============================================================================

def quick_check(email: str) -> bool:
    """
    Quick synchronous check if an email is valid.

    This performs only syntax and MX validation, no SMTP check.
    Use for rapid filtering before more expensive verification.

    Args:
        email: Email address to check

    Returns:
        True if email passes basic checks
    """
    config = VerificationConfig(
        check_smtp=False,
        check_catchall=False,
        dns_timeout=3.0
    )
    result = verify_email(email, config)
    return result.syntax_valid and result.mx_valid and not result.is_disposable


async def quick_check_async(email: str) -> bool:
    """
    Quick async check if an email is valid.

    This performs only syntax and MX validation, no SMTP check.

    Args:
        email: Email address to check

    Returns:
        True if email passes basic checks
    """
    config = VerificationConfig(
        check_smtp=False,
        check_catchall=False,
        dns_timeout=3.0
    )
    result = await verify_email_async(email, config)
    return result.syntax_valid and result.mx_valid and not result.is_disposable


def is_valid_email(email: str) -> bool:
    """
    Simple syntax-only validation.

    Args:
        email: Email address to validate

    Returns:
        True if email syntax is valid
    """
    valid, _, _ = validate_email_syntax(email)
    return valid


# ============================================================================
# Module Info
# ============================================================================

__all__ = [
    # Data classes
    "VerificationResult",
    "VerificationConfig",

    # Main verification functions
    "verify_email",
    "verify_email_async",

    # Batch verification
    "verify_emails_batch",
    "verify_emails_batch_async",

    # Quick checks
    "quick_check",
    "quick_check_async",
    "is_valid_email",

    # Utility functions
    "validate_email_syntax",
    "is_role_based_email",
    "get_mx_records",
    "get_mx_records_async",
    "calculate_risk_score",

    # Constants
    "ROLE_BASED_PREFIXES",
    "DEFAULT_DNS_TIMEOUT",
    "DEFAULT_SMTP_TIMEOUT",
    "DEFAULT_MAX_CONCURRENCY",
]
