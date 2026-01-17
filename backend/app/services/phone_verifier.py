"""
Phone verification service using free OSINT methods.

Verification stack:
1. phonenumbers (Google's libphonenumber) - Format, type, carrier patterns (free, local)
2. Veriphone API - Enhanced carrier lookup (1,000/month free)
3. phone-number-api.com - Backup lookup (free, no API key)

No paid HLR lookup - we validate format/type/carrier, not active line status.
"""

import re
import httpx
import structlog
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Google's libphonenumber Python port
try:
    import phonenumbers
    from phonenumbers import carrier, geocoder, timezone, phonenumberutil
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False

logger = structlog.get_logger(__name__)


class PhoneType(str, Enum):
    """Phone number types."""
    MOBILE = "mobile"
    FIXED_LINE = "fixed_line"
    FIXED_LINE_OR_MOBILE = "fixed_line_or_mobile"
    VOIP = "voip"
    TOLL_FREE = "toll_free"
    PREMIUM_RATE = "premium_rate"
    SHARED_COST = "shared_cost"
    PERSONAL = "personal"
    PAGER = "pager"
    UAN = "uan"
    UNKNOWN = "unknown"


class PhoneRiskLevel(str, Enum):
    """Risk level for phone numbers."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass
class PhoneVerificationResult:
    """Result of phone verification."""
    phone: str
    formatted_e164: str | None  # E.164 format (+12025551234)
    formatted_national: str | None  # National format ((202) 555-1234)
    is_valid: bool
    is_possible: bool
    phone_type: PhoneType
    carrier_name: str | None
    country_code: str | None  # ISO 3166-1 alpha-2
    region: str | None  # Geographic region (e.g., "South Carolina")
    timezone: list[str] | None
    risk_level: PhoneRiskLevel
    score: int  # 0-100
    verification_method: str  # "phonenumbers", "veriphone", "phone-number-api"
    error: str | None = None


class PhoneVerifier:
    """
    Phone verification service using free methods.

    Primary: phonenumbers library (format, type, carrier patterns)
    Fallback: Free APIs for enhanced carrier data
    """

    # High-risk area codes (known for spam/fraud)
    HIGH_RISK_AREA_CODES = {
        "900", "976",  # Premium rate
        "473", "809", "284", "649",  # Caribbean (common in scams)
    }

    # VOIP provider prefixes (partial list)
    VOIP_INDICATORS = {
        "google voice", "bandwidth.com", "level 3", "twilio",
        "vonage", "ringcentral", "grasshopper", "line2",
    }

    def __init__(
        self,
        veriphone_api_key: str | None = None,
        default_region: str = "US",
    ):
        """
        Initialize phone verifier.

        Args:
            veriphone_api_key: Optional API key for Veriphone (1000 free/month)
            default_region: Default region for parsing (ISO 3166-1 alpha-2)
        """
        self.veriphone_api_key = veriphone_api_key
        self.default_region = default_region

        if not PHONENUMBERS_AVAILABLE:
            logger.warning("phonenumbers library not available - install with: pip install phonenumbers")

    def _map_phonenumbers_type(self, pn_type: int) -> PhoneType:
        """Map phonenumbers type constant to our PhoneType enum."""
        if not PHONENUMBERS_AVAILABLE:
            return PhoneType.UNKNOWN

        type_mapping = {
            phonenumberutil.PhoneNumberType.MOBILE: PhoneType.MOBILE,
            phonenumberutil.PhoneNumberType.FIXED_LINE: PhoneType.FIXED_LINE,
            phonenumberutil.PhoneNumberType.FIXED_LINE_OR_MOBILE: PhoneType.FIXED_LINE_OR_MOBILE,
            phonenumberutil.PhoneNumberType.VOIP: PhoneType.VOIP,
            phonenumberutil.PhoneNumberType.TOLL_FREE: PhoneType.TOLL_FREE,
            phonenumberutil.PhoneNumberType.PREMIUM_RATE: PhoneType.PREMIUM_RATE,
            phonenumberutil.PhoneNumberType.SHARED_COST: PhoneType.SHARED_COST,
            phonenumberutil.PhoneNumberType.PERSONAL_NUMBER: PhoneType.PERSONAL,
            phonenumberutil.PhoneNumberType.PAGER: PhoneType.PAGER,
            phonenumberutil.PhoneNumberType.UAN: PhoneType.UAN,
        }
        return type_mapping.get(pn_type, PhoneType.UNKNOWN)

    def _calculate_risk_and_score(
        self,
        is_valid: bool,
        is_possible: bool,
        phone_type: PhoneType,
        carrier_name: str | None,
        formatted_e164: str | None,
    ) -> tuple[PhoneRiskLevel, int]:
        """
        Calculate risk level and confidence score.

        Returns:
            Tuple of (risk_level, score 0-100)
        """
        if not is_valid:
            return PhoneRiskLevel.HIGH, 0

        if not is_possible:
            return PhoneRiskLevel.HIGH, 10

        score = 50  # Base score for valid format

        # Phone type scoring
        if phone_type == PhoneType.MOBILE:
            score += 25  # Mobile numbers are most reliable for contacts
        elif phone_type == PhoneType.FIXED_LINE:
            score += 20  # Landlines are also good
        elif phone_type == PhoneType.FIXED_LINE_OR_MOBILE:
            score += 22
        elif phone_type == PhoneType.VOIP:
            score += 5  # VOIP is less reliable (could be temporary)
        elif phone_type == PhoneType.TOLL_FREE:
            score += 10  # Business toll-free
        elif phone_type in (PhoneType.PREMIUM_RATE, PhoneType.SHARED_COST):
            score -= 20  # Suspicious for contact info

        # Carrier detection bonus
        if carrier_name:
            score += 10
            # Check for VOIP indicators in carrier name
            carrier_lower = carrier_name.lower()
            if any(voip in carrier_lower for voip in self.VOIP_INDICATORS):
                score -= 10  # Reduce score for known VOIP

        # Check for high-risk area codes
        if formatted_e164:
            # Extract area code from US/Canada numbers
            match = re.match(r'^\+1(\d{3})', formatted_e164)
            if match and match.group(1) in self.HIGH_RISK_AREA_CODES:
                score -= 30

        # Clamp score
        score = max(0, min(100, score))

        # Determine risk level from score
        if score >= 70:
            risk_level = PhoneRiskLevel.LOW
        elif score >= 40:
            risk_level = PhoneRiskLevel.MEDIUM
        else:
            risk_level = PhoneRiskLevel.HIGH

        return risk_level, score

    def verify_local(self, phone: str, region: str | None = None) -> PhoneVerificationResult:
        """
        Verify phone using local phonenumbers library only.

        This is free and doesn't require any API calls.

        Args:
            phone: Phone number to verify
            region: ISO 3166-1 alpha-2 region code (default: US)

        Returns:
            PhoneVerificationResult
        """
        region = region or self.default_region
        phone = phone.strip()

        if not PHONENUMBERS_AVAILABLE:
            # Fallback to basic regex validation
            return self._verify_regex_fallback(phone)

        try:
            # Parse the phone number
            parsed = phonenumbers.parse(phone, region)

            # Get formatted versions
            formatted_e164 = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
            formatted_national = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )

            # Validation
            is_valid = phonenumbers.is_valid_number(parsed)
            is_possible = phonenumbers.is_possible_number(parsed)

            # Number type
            number_type = phonenumbers.number_type(parsed)
            phone_type = self._map_phonenumbers_type(number_type)

            # Carrier (based on prefix patterns - ~85% accurate due to portability)
            carrier_name = carrier.name_for_number(parsed, "en") or None

            # Country code
            country_code = phonenumbers.region_code_for_number(parsed)

            # Geographic region
            region_desc = geocoder.description_for_number(parsed, "en") or None

            # Timezone
            tz_list = list(timezone.time_zones_for_number(parsed)) if is_valid else None

            # Calculate risk and score
            risk_level, score = self._calculate_risk_and_score(
                is_valid, is_possible, phone_type, carrier_name, formatted_e164
            )

            return PhoneVerificationResult(
                phone=phone,
                formatted_e164=formatted_e164 if is_valid else None,
                formatted_national=formatted_national if is_valid else None,
                is_valid=is_valid,
                is_possible=is_possible,
                phone_type=phone_type,
                carrier_name=carrier_name,
                country_code=country_code,
                region=region_desc,
                timezone=tz_list,
                risk_level=risk_level,
                score=score,
                verification_method="phonenumbers",
            )

        except phonenumbers.NumberParseException as e:
            return PhoneVerificationResult(
                phone=phone,
                formatted_e164=None,
                formatted_national=None,
                is_valid=False,
                is_possible=False,
                phone_type=PhoneType.UNKNOWN,
                carrier_name=None,
                country_code=None,
                region=None,
                timezone=None,
                risk_level=PhoneRiskLevel.HIGH,
                score=0,
                verification_method="phonenumbers",
                error=f"Parse error: {e}",
            )

    def _verify_regex_fallback(self, phone: str) -> PhoneVerificationResult:
        """
        Fallback validation using regex when phonenumbers library is unavailable.
        """
        # Basic US phone regex
        us_pattern = r'^[\+]?1?[-.\s]?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}$'

        # Clean and check
        cleaned = re.sub(r'[^\d+]', '', phone)
        is_valid = bool(re.match(us_pattern, phone)) and len(cleaned) >= 10

        return PhoneVerificationResult(
            phone=phone,
            formatted_e164=f"+1{cleaned[-10:]}" if is_valid and len(cleaned) >= 10 else None,
            formatted_national=None,
            is_valid=is_valid,
            is_possible=is_valid,
            phone_type=PhoneType.UNKNOWN,
            carrier_name=None,
            country_code="US" if is_valid else None,
            region=None,
            timezone=None,
            risk_level=PhoneRiskLevel.MEDIUM if is_valid else PhoneRiskLevel.HIGH,
            score=50 if is_valid else 0,
            verification_method="regex_fallback",
        )

    async def verify_with_api(
        self,
        phone: str,
        region: str | None = None,
    ) -> PhoneVerificationResult:
        """
        Verify phone using local library + optional free API enhancement.

        Uses Veriphone API (1000 free/month) if API key is configured.
        Falls back to phone-number-api.com (no key needed) otherwise.

        Args:
            phone: Phone number to verify
            region: ISO 3166-1 alpha-2 region code

        Returns:
            PhoneVerificationResult with enhanced data if available
        """
        # Start with local verification
        result = self.verify_local(phone, region)

        # If invalid, no point calling APIs
        if not result.is_valid:
            return result

        # Try Veriphone API if configured
        if self.veriphone_api_key:
            try:
                enhanced = await self._call_veriphone(result.formatted_e164 or phone)
                if enhanced:
                    # Merge enhanced data
                    if enhanced.get("carrier"):
                        result.carrier_name = enhanced["carrier"]
                    if enhanced.get("phone_type"):
                        result.phone_type = PhoneType(enhanced["phone_type"].lower())
                    result.verification_method = "veriphone"

                    # Recalculate score with enhanced data
                    result.risk_level, result.score = self._calculate_risk_and_score(
                        result.is_valid,
                        result.is_possible,
                        result.phone_type,
                        result.carrier_name,
                        result.formatted_e164,
                    )
            except Exception as e:
                logger.warning("Veriphone API call failed", error=str(e))

        # Fallback to phone-number-api.com (no key needed)
        elif result.is_valid and not result.carrier_name:
            try:
                enhanced = await self._call_phone_number_api(result.formatted_e164 or phone)
                if enhanced and enhanced.get("carrier"):
                    result.carrier_name = enhanced["carrier"]
                    result.verification_method = "phone-number-api"

                    # Recalculate score
                    result.risk_level, result.score = self._calculate_risk_and_score(
                        result.is_valid,
                        result.is_possible,
                        result.phone_type,
                        result.carrier_name,
                        result.formatted_e164,
                    )
            except Exception as e:
                logger.warning("phone-number-api.com call failed", error=str(e))

        return result

    async def _call_veriphone(self, phone: str) -> dict | None:
        """
        Call Veriphone API for enhanced carrier lookup.

        Free tier: 1,000 requests/month
        Docs: https://veriphone.io/docs
        """
        if not self.veriphone_api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.veriphone.io/v2/verify",
                    params={
                        "phone": phone,
                        "key": self.veriphone_api_key,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        return {
                            "carrier": data.get("carrier"),
                            "phone_type": data.get("phone_type"),
                            "country_code": data.get("country_code"),
                        }
        except Exception as e:
            logger.debug("Veriphone API error", error=str(e))

        return None

    async def _call_phone_number_api(self, phone: str) -> dict | None:
        """
        Call phone-number-api.com for carrier lookup (no API key needed).

        Docs: https://phone-number-api.com/
        """
        try:
            # Remove + prefix for this API
            phone_cleaned = phone.lstrip("+")

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"https://api.phone-number-api.com/phone-number/{phone_cleaned}",
                    headers={"Accept": "application/json"},
                )

                if response.status_code == 200:
                    data = response.json()
                    return {
                        "carrier": data.get("carrier", {}).get("name"),
                        "phone_type": data.get("type"),
                        "country_code": data.get("countryCode"),
                    }
        except Exception as e:
            logger.debug("phone-number-api.com error", error=str(e))

        return None

    async def verify(
        self,
        phone: str,
        region: str | None = None,
        use_api: bool = True,
    ) -> PhoneVerificationResult:
        """
        Verify a phone number.

        Args:
            phone: Phone number to verify
            region: ISO 3166-1 alpha-2 region code (default: US)
            use_api: Whether to use free APIs for enhanced data

        Returns:
            PhoneVerificationResult
        """
        if use_api:
            return await self.verify_with_api(phone, region)
        return self.verify_local(phone, region)

    async def verify_batch(
        self,
        phones: list[str],
        region: str | None = None,
        use_api: bool = False,  # Default to local-only for batch
    ) -> list[PhoneVerificationResult]:
        """
        Verify multiple phone numbers.

        Args:
            phones: List of phone numbers
            region: ISO 3166-1 alpha-2 region code
            use_api: Whether to use APIs (careful with rate limits)

        Returns:
            List of PhoneVerificationResult
        """
        results = []
        for phone in phones:
            result = await self.verify(phone, region, use_api=use_api)
            results.append(result)
        return results


# Singleton instance
_phone_verifier: PhoneVerifier | None = None


def get_phone_verifier(veriphone_api_key: str | None = None) -> PhoneVerifier:
    """Get or create phone verifier instance."""
    global _phone_verifier
    if _phone_verifier is None:
        _phone_verifier = PhoneVerifier(veriphone_api_key=veriphone_api_key)
    return _phone_verifier
