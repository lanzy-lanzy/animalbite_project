"""
Auto-generation of patient login credentials for encoder manual creation
and pre-registration conversion.
"""
import secrets
import string
import re
from django.contrib.auth import get_user_model
from django.utils.text import slugify

User = get_user_model()

def _sanitize_username_part(s: str) -> str:
    # Keep alphanumeric, replace spaces with dots, lower
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", ".", s)
    s = re.sub(r"[^a-z0-9._-]", "", s)
    return s

def generate_patient_username(patient, try_contact_fallback=False) -> str:
    """
    Generate unique username for patient.
    Priority: first.last -> first.last + patient_number suffix -> patient_number lower -> contact
    """
    base = None
    first = _sanitize_username_part(patient.first_name)
    last = _sanitize_username_part(patient.last_name)
    if first and last:
        base = f"{first}.{last}"
    elif first:
        base = first
    elif last:
        base = last
    else:
        base = patient.patient_number.lower().replace("-", "")

    # Clean base length
    base = slugify(base).replace("-", ".") or patient.patient_number.lower()
    # Ensure base is not empty and within 150 chars
    base = base[:30]

    # Try base, then base+patient_number suffix, then with incremental numbers
    candidates = []
    # 1. base
    candidates.append(base)
    # 2. base + last 4 of patient_number
    suffix = patient.patient_number.replace("-", "").lower()[-4:]  # e.g., 0001
    candidates.append(f"{base}{suffix}")
    candidates.append(f"{base}.{suffix}")
    # 3. patient_number full lower
    candidates.append(patient.patient_number.lower())
    candidates.append(patient.patient_number.lower().replace("-", ""))
    # 4. contact based
    if try_contact_fallback and patient.contact_number:
        from utils.phone import normalize_to_09
        n09 = normalize_to_09(patient.contact_number)
        if n09:
            candidates.append(n09)

    for cand in candidates:
        cand = cand.strip().lower()
        cand = re.sub(r"[^a-z0-9._@+-]", "", cand)  # Django username allowed chars
        if not cand:
            continue
        if not User.objects.filter(username__iexact=cand).exists():
            return cand

    # Fallback incremental on base
    for i in range(1, 1000):
        cand = f"{base}{i}"
        if not User.objects.filter(username__iexact=cand).exists():
            return cand
    # Last resort: patient_number + random
    return f"{patient.patient_number.lower()}.{secrets.token_hex(2)}"

def generate_temp_password(patient=None, length=10) -> str:
    """
    Generate a memorable yet secure temp password.
    Example: Laila1234! or Silva2026!
    We generate random alphanumeric with at least one digit and one upper.
    """
    # Use secrets for strength, but keep it typable for encoder to relay via SMS/verbal
    alphabet = string.ascii_letters + string.digits
    # Ensure at least one upper, one lower, one digit
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        # Make sure it has at least one of each required and not too hard to type
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd)):
            # Avoid ambiguous?
            # Add a simple memorable prefix if patient given? But random is fine for security
            return pwd

def generate_patient_email(patient, username: str) -> str:
    """
    Generate placeholder email if not provided.
    Use username@abtc.local or patient_number@abtc.local
    Ensure uniqueness.
    """
    # Patient model has no email; we generate for User
    base = f"{username}@abtc.local"
    if not User.objects.filter(email__iexact=base).exists():
        return base
    # Try patient_number
    alt = f"{patient.patient_number.lower()}@abtc.local"
    if not User.objects.filter(email__iexact=alt).exists():
        return alt
    for i in range(1, 1000):
        cand = f"{username}{i}@abtc.local"
        if not User.objects.filter(email__iexact=cand).exists():
            return cand
    return f"{username}.{secrets.token_hex(2)}@abtc.local"

def create_patient_user(patient, created_by=None, temp_password: str | None = None):
    """
    Create User for patient with role='patient', link to patient, set temp password.
    Returns (user, temp_password, created_bool)
    If patient already has account, returns existing (with new password if temp provided)
    """
    if patient.account_id:
        # Already linked - ensure role is patient, optionally reset password if temp provided
        user = patient.account
        if temp_password:
            user.set_password(temp_password)
            user.save(update_fields=["password"])
        return user, temp_password or None, False

    username = generate_patient_username(patient)
    if temp_password is None:
        temp_password = generate_temp_password(patient)

    email = generate_patient_email(patient, username)

    user = User.objects.create_user(
        username=username,
        email=email,
        password=temp_password,
        first_name=patient.first_name,
        last_name=patient.last_name,
        mobile=patient.contact_number,
        role="patient",
    )
    # Link
    patient.account = user
    patient.save(update_fields=["account"])

    # Audit
    try:
        from audit.models import AuditLog
        AuditLog.objects.create(
            user=created_by,
            action="CREATE_PATIENT_USER",
            description=f"Auto-created login for {patient.patient_number} ({patient.full_name()}) -> {username}",
        )
    except Exception:
        pass

    return user, temp_password, True

def reset_patient_password(patient, created_by=None):
    """Generate new temp password for existing patient account, reset and return."""
    if not patient.account_id:
        return create_patient_user(patient, created_by=created_by)
    temp = generate_temp_password(patient)
    user = patient.account
    user.set_password(temp)
    user.save(update_fields=["password"])
    try:
        from audit.models import AuditLog
        AuditLog.objects.create(
            user=created_by,
            action="RESET_PATIENT_PASSWORD",
            description=f"Reset password for {patient.patient_number} ({patient.full_name()}) -> {user.username}",
        )
    except Exception:
        pass
    return user, temp, False
