"""
Semaphore SMS integration for vaccination reminders.
Sender Name: RHUDumingag (configurable via SEMAPHORE_SENDER_NAME)
Docs: https://semaphore.co/docs  POST https://api.semaphore.co/api/v4/messages
Params: apikey, number (comma-separated, 63...), message, sendername
"""
import logging
import re
import requests
from django.conf import settings
from utils.phone import normalize_to_63 as _normalize_to_63

logger = logging.getLogger(__name__)

SEMAPHORE_API_URL = getattr(settings, "SEMAPHORE_API_URL", "https://api.semaphore.co/api/v4/messages")


def normalize_ph_number(raw: str) -> str | None:
    """
    Wrapper for utils.phone.normalize_to_63 for backward compat.
    Semaphore expects 639XXXXXXXXX.
    """
    res = _normalize_to_63(raw)
    if not res:
        logger.warning(f"Invalid PH number after normalize: raw={raw!r}")
    return res


def build_reminder_message(patient, case, dose, reminder_type: str) -> str:
    """
    Build Filipino/English friendly SMS for vaccination.
    reminder_type: '3days_before' or 'on_day'
    Keep under 160 chars per segment if possible, but Semaphore auto-splits.
    """
    patient_name = getattr(patient, "full_name", lambda: str(patient))()
    if callable(patient_name):
        patient_name = patient_name
    else:
        # fallback if full_name is method
        try:
            patient_name = patient.full_name()
        except Exception:
            patient_name = f"{patient.first_name} {patient.last_name}"

    dose_label = dose.dose_label
    sched_date = dose.scheduled_date.strftime("%b %d, %Y (%a)")
    case_no = case.case_number

    # Short, not starting with TEST (Semaphore ignores)
    if reminder_type == "3days_before":
        return (
            f"ABTC Dumingag: Paalala, {patient_name} ({case_no}) may vaccine schedule sa {sched_date} "
            f"({dose_label}). Mangyaring pumunta sa RHU 3 araw mula ngayon. Dalhin ang card. Salamat! -RHUDumingag"
        ).strip()
    elif reminder_type == "on_day":
        return (
            f"ABTC Dumingag: Ngayon na ang vaccine schedule ni {patient_name} ({case_no}) - {dose_label} "
            f"ngayong {sched_date}. Pumunta sa RHU ngayong araw. Dalhin ang vaccination card. -RHUDumingag"
        ).strip()
    else:
        return (
            f"ABTC Dumingag: Paalala para kay {patient_name} ({case_no}) - {dose_label} sa {sched_date}. "
            f"Pumunta sa RHU. -RHUDumingag"
        ).strip()


def send_semaphore_sms(number: str, message: str, sendername: str | None = None) -> dict:
    """
    Low-level Semaphore call.
    Returns dict with keys: success (bool), response (json/list), error (str), raw (requests.Response)
    """
    # Guard: do not start with TEST
    if message.lstrip().upper().startswith("TEST"):
        message = " " + message  # prefix space to avoid silent ignore

    normalized = normalize_ph_number(number)
    if not normalized:
        return {"success": False, "error": f"Invalid PH number: {number}", "response": None}

    sender = sendername or getattr(settings, "SEMAPHORE_SENDER_NAME", "RHUDumingag") or "RHUDumingag"
    api_url = getattr(settings, "SEMAPHORE_API_URL", SEMAPHORE_API_URL)

    # If disabled, simulate success (useful for dev / no credits) — don't require API key
    if not getattr(settings, "SEMAPHORE_ENABLED", False):
        logger.info(f"[SEMAPHORE DISABLED] Would send to {normalized} via {sender}: {message[:80]}...")
        # Simulate queued response like real API
        simulated = [{
            "message_id": 0,
            "recipient": normalized,
            "message": message,
            "sender_name": sender,
            "status": "Queued (simulated - SEMAPHORE_ENABLED=False)",
            "type": "Single",
        }]
        return {"success": True, "response": simulated, "error": None, "simulated": True, "normalized": normalized}

    apikey = getattr(settings, "SEMAPHORE_API_KEY", "")
    if not apikey:
        return {"success": False, "error": "SEMAPHORE_API_KEY not configured", "response": None}

    payload = {
        "apikey": apikey,
        "number": normalized,
        "message": message,
        "sendername": sender,
    }

    try:
        resp = requests.post(api_url, data=payload, timeout=15)
        # Semaphore returns JSON list on success, or dict with error
        try:
            data = resp.json()
        except Exception:
            data = resp.text

        if resp.status_code in (200, 201):
            # Success is list of messages; check status field
            # Consider success if any item has status Queued/Pending/Sent
            return {"success": True, "response": data, "error": None, "status_code": resp.status_code, "normalized": normalized}
        else:
            logger.error(f"Semaphore HTTP {resp.status_code}: {data}")
            return {"success": False, "error": f"HTTP {resp.status_code}: {data}", "response": data, "status_code": resp.status_code}
    except requests.RequestException as e:
        logger.exception(f"Semaphore request failed: {e}")
        return {"success": False, "error": str(e), "response": None}


def send_dose_reminder(dose, reminder_type: str = "3days_before", dry_run: bool = False, force: bool = False):
    """
    High-level helper to send SMS for a specific dose.
    Handles logging via SMSLog and duplicate guard.

    Returns (log_instance or None, result_dict)
    """
    from .models import SMSLog

    schedule = dose.schedule
    case = schedule.bite_case
    patient = case.patient

    # Build candidate numbers: primary contact, then emergency, then account mobile (if patient has account)
    candidates = [
        patient.contact_number,
        getattr(patient, "emergency_contact_number", ""),
        getattr(getattr(patient, "account", None), "mobile", "") if getattr(patient, "account", None) else "",
    ]
    # Pick first valid PH number
    number = None
    for cand in candidates:
        if cand and normalize_ph_number(cand):
            number = cand
            break
    if not number:
        # Fallback to primary even if invalid (will fail gracefully)
        number = patient.contact_number or (candidates[1] if len(candidates) > 1 else "")
    message = build_reminder_message(patient, case, dose, reminder_type)

    # Duplicate guard: don't re-send same dose + reminder_type + scheduled_date on same day unless force
    from django.utils import timezone
    today = timezone.localdate()
    existing = SMSLog.objects.filter(
        dose=dose,
        reminder_type=reminder_type,
        scheduled_date=dose.scheduled_date,
    ).filter(status__in=["sent", "queued", "pending"]).first() if not force and not dry_run else None
    # Alternative: prevent duplicate today
    if existing and not force and not dry_run:
        # Check if already sent today or within last 24h for this reminder_type
        # If existing sent_at date is today, skip
        if existing.sent_at and existing.sent_at.date() == today:
            return existing, {"success": False, "error": "Already sent today", "response": None, "skipped": True}

    if dry_run:
        # Create a simulated log without calling API
        log = SMSLog(
            dose=dose,
            patient=patient,
            bite_case=case,
            reminder_type=reminder_type,
            recipient=normalize_ph_number(number) or number,
            message=message,
            scheduled_date=dose.scheduled_date,
            status="pending",
            api_response={"dry_run": True},
        )
        # Don't save yet; caller may save or not
        result = {"success": True, "response": [{"status": "DryRun"}], "dry_run": True}
        return log, result

    result = send_semaphore_sms(number, message)

    # Prepare log data
    normalized = result.get("normalized") or normalize_ph_number(number) or number
    status = "failed"
    api_resp = result.get("response")
    message_id = None
    if result.get("success"):
        # Real API returns list; check first item status
        if isinstance(api_resp, list) and api_resp:
            first = api_resp[0]
            status_raw = str(first.get("status", "")).lower()
            message_id = first.get("message_id")
            if "failed" in status_raw:
                status = "failed"
            elif "queued" in status_raw or "pending" in status_raw or "sent" in status_raw:
                status = "sent" if not result.get("simulated") else "queued"
                if result.get("simulated"):
                    status = "queued"
            else:
                status = "sent"
        else:
            status = "sent"
    else:
        status = "failed"

    # Save log
    log = SMSLog.objects.create(
        dose=dose,
        patient=patient,
        bite_case=case,
        reminder_type=reminder_type,
        recipient=normalized,
        message=message,
        scheduled_date=dose.scheduled_date,
        status=status,
        semaphore_message_id=str(message_id) if message_id else "",
        api_response=str(api_resp)[:4000] if api_resp is not None else result.get("error", ""),
    )
    return log, result
