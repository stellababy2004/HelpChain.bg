from flask import Blueprint, current_app, jsonify, request
import os

zoho_integrations_bp = Blueprint(
    "zoho_integrations",
    __name__,
    url_prefix="/api/integrations/zoho",
)


@zoho_integrations_bp.post("/events")
def receive_zoho_event():
    expected_token = (
        current_app.config.get("HELPCHAIN_ZOHO_INTEGRATION_TOKEN")
        or os.getenv("HELPCHAIN_ZOHO_INTEGRATION_TOKEN")
    )

    provided_token = request.headers.get("X-HelpChain-Integration-Token")

    if not expected_token:
        return jsonify({"ok": False, "error": "integration_token_not_configured"}), 500

    if provided_token != expected_token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    event_name = payload.get("event")

    allowed_events = {
        "request.created",
        "request.assigned",
        "request.escalated",
        "request.closed",
        "sla.warning",
    }

    if event_name not in allowed_events:
        return jsonify({"ok": False, "error": "unsupported_event"}), 400

    current_app.logger.info("[ZOHO_INTEGRATION] event received: %s payload=%s", event_name, payload)

    return jsonify({
        "ok": True,
        "received": True,
        "event": event_name,
    }), 200
