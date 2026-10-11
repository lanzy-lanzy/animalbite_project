"""
QR code generation helpers for patient credentials and record verification.

Two payload styles are produced here, both rendered spec-compliant so phone
cameras and jsQR reliably detect them:

* Login-credential QRs (one-time, right after creation/reset while the
  plaintext temp password is still in hand) — plain text with the portal URL,
  username and temp password.
* Verification QRs — host-independent tokens (`ABTC-PATIENT:<number>`,
  `ABTC-PRE-REG:<number>`) resolved against the live database by the in-app
  scanner (`patients:scan_resolve`) on every scan, so a scan always shows
  fresh data and stays valid across localhost, LAN and production. A
  pre-registration token automatically follows conversion to the official
  patient record.

Rendering fixes applied: 4-module quiet zone (the QR spec minimum — a 2-module
border made many scanners fail to detect the code), pure black modules on
white for maximum contrast, and explicit UTF-8 payload bytes.
"""
import io
import base64

# The QR spec requires a 4-module white quiet zone around the symbol; codes
# rendered with a smaller border are frequently rejected by camera scanners.
QUIET_ZONE_MODULES = 4


def build_credentials_payload(login_url, username, password, patient_name=None):
    """Human-readable multi-line text embedded in the QR code."""
    lines = []
    lines.append(f"ABTC Patient Portal — {patient_name}" if patient_name else "ABTC Patient Portal")
    lines.append(f"Login: {login_url}")
    lines.append(f"Username: {username}")
    lines.append(f"Temp Password: {password}")
    lines.append("Change password after first login (Profile > Change Password).")
    return "\n".join(lines)


def build_patient_payload(patient):
    """Portable verification token for a patient's QR (resolved to the live
    record on every scan — never embeds a host or stale data)."""
    return f"ABTC-PATIENT:{patient.patient_number}"


def build_pre_registration_payload(number):
    """Portable verification token for a pre-registration slip QR."""
    return f"ABTC-PRE-REG:{number}"


def qr_png_bytes(text, box_size=8, border=QUIET_ZONE_MODULES, fill_color="#000000",
                 back_color="white", error_correction=None):
    """Render `text` as PNG bytes. Accepts str (encoded as UTF-8) or bytes."""
    import qrcode
    import qrcode.constants

    qr = qrcode.QRCode(
        box_size=box_size,
        border=border,
        error_correction=error_correction or qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(text.encode("utf-8") if isinstance(text, str) else text)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color=back_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_data_uri(text, **kwargs):
    """Return a PNG QR code for `text` as a base64 data URI (ready for <img src>)."""
    b64 = base64.b64encode(qr_png_bytes(text, **kwargs)).decode("ascii")
    return f"data:image/png;base64,{b64}"
