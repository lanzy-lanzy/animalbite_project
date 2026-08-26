"""
Central PH phone validation / normalization for Semaphore
- Accepts: 09XXXXXXXXX (11 digits), 639XXXXXXXXX (12), +639XXXXXXXXX, 9XXXXXXXXX (10)
- Displays: 09XXXXXXXXX (local) for forms, stores normalized, but SMS needs 639...
- Semaphore requires 639XXXXXXXXX (see https://semaphore.co/docs)
"""
import re
from django.core.exceptions import ValidationError

PH_LOCAL_RE = re.compile(r"^09\d{9}$")  # 09 + 9 digits = 11
PH_INTL_RE = re.compile(r"^639\d{9}$")  # 63 + 10 digits (639 + 9) actually 639 + 9 digits = 12 total ; 639 + 9 = 639 + 9 digits -> 12
# Note: Philippine mobile: 09XX XXX XXXX (11 inc 0) or 639XX XXX XXXX (12 inc 63)

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_to_63(raw: str) -> str | None:
    """Return 639XXXXXXXXX or None if invalid. Handles 09..., 639..., +639..., 9..."""
    if not raw:
        return None
    s = re.sub(r"[\s\-()]", "", str(raw).strip())
    if s.startswith("+"):
        s = s[1:]
    d = _digits(s)
    if not d:
        return None
    if d.startswith("0"):
        d = "63" + d[1:]
    elif d.startswith("9") and len(d) == 10:
        d = "63" + d
    # Now must be 639 + 9 digits
    if re.match(r"^639\d{9}$", d):
        return d
    return None

def normalize_to_09(raw: str) -> str | None:
    """Return 09XXXXXXXXX local form or None."""
    d63 = normalize_to_63(raw)
    if not d63:
        return None
    # 639 -> 09
    return "0" + d63[2:]  # 639XXXXXXXXX -> 09XXXXXXXXX

def is_valid_ph_number(raw: str) -> bool:
    return normalize_to_63(raw) is not None

def validate_ph_number(value: str):
    if not is_valid_ph_number(value):
        raise ValidationError(
            "Enter a valid Philippine mobile number: 09XXXXXXXXX, 639XXXXXXXXX, or +639XXXXXXXXX (e.g., 09123456789).",
            code="invalid_ph_number",
        )

def format_display_09(raw: str) -> str:
    """For templates: show as 09XX-XXX-XXXX or raw if invalid."""
    n09 = normalize_to_09(raw)
    if not n09:
        return raw or ""
    # Format 0912 345 6789
    return f"{n09[:4]} {n09[4:7]} {n09[7:]}"

# Django validator for model/form fields
def ph_number_validator(value):
    validate_ph_number(value)
