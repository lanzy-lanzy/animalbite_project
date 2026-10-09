"""
QR code generation helpers for patient login credentials.

The QR encodes a plain-text payload (portal URL + username + temporary
password) so an encoder can hand the patient a scannable image at
registration / password-reset time. Because passwords are stored hashed,
the QR can only be produced when the plaintext temp password is still in
hand (i.e. right after creation/reset).
"""
import io
import base64


def build_credentials_payload(login_url, username, password, patient_name=None):
    """Human-readable multi-line text embedded in the QR code."""
    lines = []
    lines.append(f"ABTC Patient Portal — {patient_name}" if patient_name else "ABTC Patient Portal")
    lines.append(f"Login: {login_url}")
    lines.append(f"Username: {username}")
    lines.append(f"Temp Password: {password}")
    lines.append("Change password after first login (Profile > Change Password).")
    return "\n".join(lines)


def qr_data_uri(text, box_size=8, border=2, fill_color="#0a1f1a", back_color="white"):
    """Return a PNG QR code for `text` as a base64 data URI (ready for <img src>)."""
    import qrcode
    qr = qrcode.QRCode(
        box_size=box_size,
        border=border,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color=back_color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
