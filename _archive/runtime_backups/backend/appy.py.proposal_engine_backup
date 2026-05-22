import os
import sys

from backend.local_db_guard import apply_local_runtime_db_contract, normalize_uri

_runtime_db = apply_local_runtime_db_contract()
if _runtime_db.apply_contract and _runtime_db.selected_uri:
    configured_uri = _runtime_db.configured_uri or ""
    if configured_uri and normalize_uri(configured_uri) != normalize_uri(_runtime_db.selected_uri):
        print(
            "[LOCAL DB] configured runtime DB differs from the effective local DB.\n"
            f"configured={configured_uri}\n"
            f"effective={_runtime_db.selected_uri}\n"
            f"reason={_runtime_db.reason}",
            file=sys.stderr,
        )
    elif _runtime_db.selected_label == "fallback":
        print(
            "[LOCAL DB] using fallback local DB.\n"
            f"effective={_runtime_db.selected_uri}\n"
            f"reason={_runtime_db.reason}",
            file=sys.stderr,
        )
    elif not (_runtime_db.selected_health and _runtime_db.selected_health.healthy):
        print(
            "[LOCAL DB] no healthy local DB found.\n"
            f"effective={_runtime_db.selected_uri}\n"
            f"reason={_runtime_db.reason}",
            file=sys.stderr,
        )

from backend.extensions import db  # bound SQLAlchemy instance
from backend.helpchain_backend.src.app import create_app
from backend.routes.lead_capture import lead_capture_bp

# --------------------------------------------------
# Create Flask app
# --------------------------------------------------
app = create_app()
app.register_blueprint(lead_capture_bp)


# --------------------------------------------------
# Mail client (stub for dev / tests)
# --------------------------------------------------
class _MailClient:
    def send(self, **kwargs):
        if os.getenv("MAIL_ENABLED", "false") != "true":
            print(f"[MAIL DISABLED] {kwargs}")
            return True

        print(f"[MAIL] Sending email: {kwargs}")
        return True


mail = _MailClient()


# --------------------------------------------------
# 2FA Email helper
# --------------------------------------------------
def send_email_2fa_code(code, ip_address=None, user_agent=None):
    """
    Sends a 2FA verification code via the mail client.
    Compatible with legacy tests.
    """
    subject = "Code de vérification – HelpChain"
    body = f"Votre code de vérification est : {code}\nCe code est valable pendant 10 minutes."

    try:
        result = mail.send(
            subject=subject,
            body=body,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return True if result is None else bool(result)

    except Exception as e:
        print(f"[MAIL ERROR] {e}")
        return False


# --------------------------------------------------
# Optional: helper (useful for scripts/tests)
# --------------------------------------------------
def get_app():
    return app


# --------------------------------------------------
# Public exports
# --------------------------------------------------
__all__ = ["app", "db", "mail", "send_email_2fa_code", "get_app"]