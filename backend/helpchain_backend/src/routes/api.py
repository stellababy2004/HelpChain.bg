import asyncio
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request, send_file, session
from flask_login import current_user
from pywebpush import WebPushException, webpush
from sqlalchemy import func, inspect as sa_inspect, or_
from werkzeug.security import check_password_hash

from backend.ai_service import ai_service

from ..controllers.helpchain_controller import HelpChainController
from ..extensions import csrf
from ..models import (
    AdminUser,
    Case,
    CaseCollaborator,
    CaseEvent,
    IntegrationConnector,
    NotificationSubscription,
    RelayEvent,
    Request,
    RequestLog,
    RequestMetric,
    Structure,
    db,
    canonical_role,
)
from .admin import admin_required, admin_required_404, admin_role_required, _is_global_admin
from ..security.api_authz import require_api_auth, require_roles

api_bp = Blueprint("api", __name__)
controller = HelpChainController()
csrf.exempt(api_bp)

_RELAY_ALLOWED_FIELDS = {
    "external_source",
    "external_reference_id",
    "status",
    "priority",
    "category",
    "due_date",
    "relance_at",
    "structure_id",
    "structure_slug",
    "summary_label",
}
_RELAY_SENSITIVE_FIELDS = {
    "birth_date",
    "diagnosis",
    "full_name",
    "medical_notes",
    "patient_file",
    "psychological_notes",
    "social_report",
}
_RELAY_MAX_LENGTHS = {
    "external_source": 120,
    "external_reference_id": 255,
    "status": 64,
    "priority": 64,
    "category": 64,
    "summary_label": 255,
}
_RELAY_TEXT_NORMALIZER = re.compile(r"[^a-z0-9._-]+")
_RELAY_CONNECTOR_STATUSES_BLOCKED = {"paused", "revoked"}


def _relay_table_available() -> bool:
    try:
        return bool(sa_inspect(db.engine).has_table("relay_events"))
    except Exception:
        current_app.logger.exception("RelayEvent table inspection failed")
        return False


def _relay_connector_table_available() -> bool:
    try:
        return bool(sa_inspect(db.engine).has_table("integration_connectors"))
    except Exception:
        current_app.logger.exception("IntegrationConnector table inspection failed")
        return False


def _parse_relay_datetime(raw_value):
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, datetime):
        return raw_value
    value = str(raw_value).strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid datetime format") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _sanitize_summary_label(raw_value: object) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    if len(value) > _RELAY_MAX_LENGTHS["summary_label"]:
        raise ValueError("summary_label exceeds max length 255")
    lowered = value.lower()
    if "@" in value or any(marker in lowered for marker in ("diagnostic", "patient", "psycholog", "social")):
        raise ValueError("summary_label appears sensitive")
    return value


def _sanitize_relay_string(field_name: str, raw_value: object, *, required: bool = False) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    max_length = _RELAY_MAX_LENGTHS[field_name]
    if len(value) > max_length:
        raise ValueError(f"{field_name} exceeds max length {max_length}")
    return value


def _normalize_relay_token(field_name: str, raw_value: object) -> str | None:
    value = _sanitize_relay_string(field_name, raw_value, required=False)
    if value is None:
        return None
    normalized = _RELAY_TEXT_NORMALIZER.sub("_", value.strip().lower()).strip("._-")
    if not normalized:
        return None
    if len(normalized) > _RELAY_MAX_LENGTHS[field_name]:
        normalized = normalized[: _RELAY_MAX_LENGTHS[field_name]].rstrip("._-")
    return normalized or None


def _sanitize_relay_metadata_value(field_name: str, field_value):
    if field_value is None:
        return None
    if isinstance(field_value, bool):
        return field_value
    if isinstance(field_value, (int, float)):
        return field_value
    value = str(field_value).strip()
    if not value:
        return None
    if len(value) > 255:
        value = value[:255].rstrip()
    lowered = value.lower()
    if "@" in value or any(marker in lowered for marker in ("diagnostic", "patient", "psycholog", "social")):
        raise ValueError(f"metadata field {field_name} appears sensitive")
    return value


def _resolve_relay_structure(payload: dict) -> int | None:
    raw_structure_id = payload.get("structure_id")
    raw_structure_slug = (payload.get("structure_slug") or "").strip()
    structure = None

    if raw_structure_id not in (None, ""):
        try:
            structure = db.session.get(Structure, int(raw_structure_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("structure_id invalid") from exc
        if structure is None:
            raise ValueError("structure_id unknown")

    if raw_structure_slug:
        slug_match = Structure.query.filter_by(slug=raw_structure_slug).first()
        if slug_match is None:
            raise ValueError("structure_slug unknown")
        if structure is not None and int(structure.id) != int(slug_match.id):
            raise ValueError("structure_id and structure_slug mismatch")
        structure = slug_match

    return getattr(structure, "id", None)


def _sanitize_relay_payload(payload: dict) -> tuple[dict, list[str], dict | None]:
    sanitized = {
        "external_source": _sanitize_relay_string(
            "external_source", payload.get("external_source"), required=True
        ),
        "external_reference_id": _sanitize_relay_string(
            "external_reference_id",
            payload.get("external_reference_id"),
            required=True,
        ),
        "status": _normalize_relay_token("status", payload.get("status")),
        "priority": _normalize_relay_token("priority", payload.get("priority")),
        "category": _normalize_relay_token("category", payload.get("category")),
        "due_date": _parse_relay_datetime(payload.get("due_date")),
        "relance_at": _parse_relay_datetime(payload.get("relance_at")),
        "summary_label": _sanitize_summary_label(payload.get("summary_label")),
        "structure_id": _resolve_relay_structure(payload),
    }

    rejected_fields = sorted(
        field_name for field_name in payload.keys() if field_name in _RELAY_SENSITIVE_FIELDS
    )
    metadata = {}
    for field_name, field_value in payload.items():
        if field_name in _RELAY_ALLOWED_FIELDS or field_name in _RELAY_SENSITIVE_FIELDS:
            continue
        if isinstance(field_value, (str, int, float, bool)) or field_value is None:
            sanitized_value = _sanitize_relay_metadata_value(field_name, field_value)
            if sanitized_value is not None:
                metadata[field_name] = sanitized_value
    return sanitized, rejected_fields, (metadata or None)


def _relay_connector_authenticate() -> tuple[IntegrationConnector | None, tuple | None]:
    source_slug = (request.headers.get("X-HC-Connector") or "").strip()
    provided_key = (request.headers.get("X-HC-Relay-Key") or "").strip()

    if not source_slug:
        return None, None
    if not provided_key:
        return None, (jsonify({"error": "Unauthorized relay key."}), 401)
    if not _relay_connector_table_available():
        return None, (jsonify({"error": "Relay connector storage is unavailable."}), 503)

    connector = IntegrationConnector.query.filter_by(source_slug=source_slug).first()
    if connector is None:
        return None, (jsonify({"error": "Unauthorized connector."}), 401)

    if not connector.api_key_hash or not check_password_hash(connector.api_key_hash, provided_key):
        return None, (jsonify({"error": "Unauthorized relay key."}), 401)

    if (connector.status or "").strip().lower() in _RELAY_CONNECTOR_STATUSES_BLOCKED:
        return None, (jsonify({"error": "Connector is not active."}), 403)

    return connector, None


@api_bp.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    context = data.get("context", None)
    try:
        result = asyncio.run(ai_service.generate_response(message, context))
        reply = result.get("response", "Няма отговор от AI.")
        if message and message not in reply:
            reply = f"{message} | {reply}"
        if context:
            context_text = str(context)
            if context_text not in reply:
                reply = f"{reply} | context: {context_text}"
        return jsonify({"reply": reply, "ok": True}), 200
    except Exception as e:
        return (
            jsonify(
                {
                    "reply": "Извиняваме се, възникна временен проблем с автоматичния отговор. Моля, опитайте отново по-късно или се свържете с екипа на HelpChain.",
                    "ok": False,
                }
            ),
            500,
        )


@api_bp.post("/integrations/relay")
def integrations_relay():
    if not request.is_json:
        return jsonify({"error": "JSON body required."}), 415

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    if not _relay_table_available():
        return jsonify({"error": "Relay storage is unavailable."}), 503

    connector, connector_error = _relay_connector_authenticate()
    if connector_error is not None:
        return connector_error

    if connector is None:
        expected_key = (
            current_app.config.get("HC_RELAY_API_KEY") or os.getenv("HC_RELAY_API_KEY") or ""
        ).strip()
        if not expected_key:
            return jsonify({"error": "Relay integration is not enabled."}), 503

        provided_key = (request.headers.get("X-HC-Relay-Key") or "").strip()
        if not provided_key or not hmac.compare_digest(provided_key, expected_key):
            return jsonify({"error": "Unauthorized relay key."}), 401

    try:
        sanitized_payload, rejected_fields, sanitized_metadata = _sanitize_relay_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if connector is not None:
        sanitized_payload["external_source"] = connector.source_slug
        connector_structure_id = getattr(connector, "structure_id", None)
        if connector_structure_id is not None:
            if (
                sanitized_payload["structure_id"] is not None
                and int(sanitized_payload["structure_id"]) != int(connector_structure_id)
            ):
                return jsonify({"error": "structure mismatch for connector."}), 400
            sanitized_payload["structure_id"] = int(connector_structure_id)

    event_timestamp = datetime.now(timezone.utc)

    relay_event = RelayEvent(
        external_source=sanitized_payload["external_source"],
        external_reference_id=sanitized_payload["external_reference_id"],
        status=sanitized_payload["status"],
        priority=sanitized_payload["priority"],
        category=sanitized_payload["category"],
        due_date=sanitized_payload["due_date"],
        relance_at=sanitized_payload["relance_at"],
        structure_id=sanitized_payload["structure_id"],
        connector_id=getattr(connector, "id", None),
        summary_label=sanitized_payload["summary_label"],
        rejected_fields_json=json.dumps(rejected_fields, ensure_ascii=True)
        if rejected_fields
        else None,
        metadata_json=json.dumps(sanitized_metadata, ensure_ascii=True)
        if sanitized_metadata
        else None,
    )
    if connector is not None:
        connector.last_seen_at = event_timestamp
        connector.last_event_at = event_timestamp
    db.session.add(relay_event)
    db.session.commit()

    return (
        jsonify(
            {
                "ok": True,
                "relay_event_id": relay_event.id,
                "sync_status": relay_event.sync_status,
                "rejected_fields": rejected_fields,
                "connector_id": getattr(connector, "id", None),
            }
        ),
        201,
    )


@api_bp.post("/chatbot/message")
@csrf.exempt
def chatbot_message():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON payload"}), 400

    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    session_id = data.get("session_id")
    context = data.get("context") or {}
    if session_id and "session_id" not in context:
        context["session_id"] = session_id

    try:
        result = asyncio.run(ai_service.generate_response(message, context))
    except Exception:
        result = {
            "response": "Временна грешка в AI услугата.",
            "confidence": 0.0,
            "provider": "fallback",
        }

    return (
        jsonify(
            {
                "response": result.get("response", ""),
                "confidence": float(result.get("confidence", 0.0)),
                "provider": result.get("provider", "unknown"),
                "session_id": session_id,
            }
        ),
        200,
    )


@api_bp.route("/ai/status", methods=["GET"])
def ai_status():
    return (
        jsonify(
            {
                "status": "healthy",
                "providers": ["openai"],
                "active_provider": "openai",
            }
        ),
        200,
    )


@api_bp.get("/volunteer/dashboard")
def volunteer_dashboard_legacy():
    """Legacy compatibility endpoint: requires authenticated volunteer/admin."""
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": True}), 200


@api_bp.get("/notification/vapid-public-key")
def vapid_public_key():
    # Try config first, then env
    key = current_app.config.get("VAPID_PUBLIC_KEY") or os.getenv("VAPID_PUBLIC_KEY")
    if not key:
        return jsonify({"enabled": False, "publicKey": None}), 200
    return jsonify({"enabled": True, "publicKey": key}), 200


@api_bp.route("/notification/subscribe", methods=["POST"])
def notification_subscribe():
    data = request.get_json(silent=True) or {}

    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "invalid subscription payload"}), 400

    ua = request.headers.get("User-Agent")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    existing = NotificationSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = ua
        existing.ip = ip
        db.session.commit()
        return jsonify({"ok": True, "updated": True}), 200

    sub = NotificationSubscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=ua,
        ip=ip,
    )
    db.session.add(sub)
    db.session.commit()
    return jsonify({"ok": True, "created": True}), 201


@api_bp.route("/notification/unsubscribe", methods=["POST"])
def notification_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")

    if not endpoint:
        return jsonify({"error": "missing endpoint"}), 400

    sub = NotificationSubscription.query.filter_by(endpoint=endpoint).first()
    if not sub:
        # idempotent success if already removed
        return jsonify({"ok": True, "deleted": False}), 200

    db.session.delete(sub)
    db.session.commit()
    return jsonify({"ok": True, "deleted": True}), 200


@api_bp.route("/notification/test", methods=["POST"])
def notification_test():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")

    q = NotificationSubscription.query
    sub = (
        q.filter_by(endpoint=endpoint).first()
        if endpoint
        else q.order_by(NotificationSubscription.created_at.desc()).first()
    )

    if not sub:
        return jsonify({"ok": False, "error": "no subscription"}), 404

    vapid_public = current_app.config.get("VAPID_PUBLIC_KEY")
    vapid_private = current_app.config.get("VAPID_PRIVATE_KEY")
    vapid_subject = current_app.config.get("VAPID_SUBJECT", "mailto:admin@localhost")

    if not vapid_public or not vapid_private:
        return jsonify({"ok": False, "error": "VAPID not configured"}), 500

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }

    payload = {
        "title": "HelpChain",
        "body": data.get("body") or "Test notification ✅",
        "url": data.get("url") or "/admin/",
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private,
            vapid_claims={"sub": vapid_subject},
        )
        return jsonify({"ok": True}), 200
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status == 410:
            db.session.delete(sub)
            db.session.commit()
        return jsonify({"ok": False, "error": str(e), "status": status}), 500


@api_bp.post("/cases/<int:case_id>/invite-structure")
@admin_required
@admin_role_required("ops", "superadmin")
def invite_structure_to_case(case_id: int):
    admin_required_404()
    payload = request.get_json(silent=True) or {}
    structure_id_raw = payload.get("structure_id")
    role_raw = (payload.get("role") or "viewer").strip().lower()

    try:
        structure_id = int(structure_id_raw)
    except Exception:
        return jsonify({"error": "structure_id is required"}), 400

    case_row = db.session.get(Case, int(case_id))
    if not case_row:
        return jsonify({"error": "case not found"}), 404

    admin_structure_id = getattr(current_user, "structure_id", None)
    admin_user_id = (
        session.get("admin_user_id")
        or session.get("admin_id")
        or session.get("user_id")
    )
    try:
        if admin_user_id is not None:
            admin_user = db.session.get(AdminUser, int(admin_user_id))
            if admin_user is not None:
                admin_structure_id = getattr(admin_user, "structure_id", None)
    except Exception:
        admin_structure_id = getattr(current_user, "structure_id", None)
    if not _is_global_admin() and admin_structure_id is not None and case_row.structure_id != admin_structure_id:
        return jsonify({"error": "forbidden"}), 403

    if structure_id == case_row.structure_id:
        return jsonify({"error": "structure already owns case"}), 400

    structure = db.session.get(Structure, structure_id)
    if not structure:
        return jsonify({"error": "structure not found"}), 404

    existing = (
        CaseCollaborator.query.filter(CaseCollaborator.case_id == case_row.id)
        .filter(CaseCollaborator.structure_id == structure_id)
        .first()
    )
    if existing:
        existing.role = role_raw or existing.role
    else:
        db.session.add(
            CaseCollaborator(
                case_id=case_row.id,
                structure_id=structure_id,
                role=role_raw or "viewer",
            )
        )
        db.session.add(
            CaseEvent(
                case_id=case_row.id,
                actor_user_id=getattr(current_user, "id", None),
                event_type="structure_invited",
                message=f"Structure invited: {structure.name}",
                visibility="internal",
            )
        )
    db.session.commit()
    return jsonify({"ok": True}), 200


@api_bp.route("/some_endpoint", methods=["GET"])
def some_endpoint():
    # опитваме се да използваме подходящ method от контролера, ако има
    fn = (
        getattr(controller, "some_endpoint", None)
        or getattr(controller, "ping", None)
        or getattr(controller, "status", None)
    )
    if callable(fn):
        try:
            out = fn()
            if isinstance(out, dict | list):
                return jsonify(out), 200
            return jsonify({"ok": True, "result": out}), 200
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "message": "endpoint ok"}), 200


@api_bp.route("/help", methods=["GET"])
def get_help():
    try:
        res = controller.get_help()
        return jsonify(res), 200
    except AttributeError:
        return jsonify({"error": "get_help not implemented"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/help", methods=["POST"])
def create_help():
    data = request.get_json(silent=True) or {}
    try:
        res = controller.create_help(data)
        return jsonify(res), 201
    except AttributeError:
        return jsonify({"error": "create_help not implemented"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/help/<int:help_id>/approve", methods=["POST"])
def approve_help(help_id):
    payload = request.get_json(silent=True) or {}
    admin = payload.get("admin")
    note = payload.get("note")
    try:
        res = controller.approve_request(help_id, admin, note=note)
        return jsonify(res), 200
    except AttributeError:
        return jsonify({"error": "approve_request not implemented"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/help/<int:help_id>/reject", methods=["POST"])
def reject_help(help_id):
    payload = request.get_json(silent=True) or {}
    admin = payload.get("admin")
    reason = payload.get("reason")
    try:
        res = controller.reject_request(help_id, admin, reason=reason)
        return jsonify(res), 200
    except AttributeError:
        return jsonify({"error": "reject_request not implemented"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/dashboard", methods=["GET"])
@require_roles("admin")
def dashboard():
    try:
        try:
            days = int(request.args.get("days", 30))
        except Exception:
            days = 30

        since_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

        # 1) counts by status
        status_rows = (
            db.session.query(
                func.coalesce(Request.status, "unknown").label("status"),
                func.count(Request.id).label("cnt"),
            )
            .group_by("status")
            .all()
        )
        counts_by_status = {status: int(cnt) for status, cnt in status_rows}
        total_requests = int(sum(counts_by_status.values()))

        # 2) requests by city (top 10) with fallback chain city -> region -> "unknown"
        city_expr = func.coalesce(
            func.nullif(Request.city, ""),
            func.nullif(Request.region, ""),
            "unknown",
        )
        city_rows = (
            db.session.query(
                city_expr.label("city"), func.count(Request.id).label("cnt")
            )
            .group_by("city")
            .order_by(func.count(Request.id).desc())
            .limit(10)
            .all()
        )
        requests_by_city = [{"city": c, "count": int(cnt)} for c, cnt in city_rows]

        # 3) timeseries (daily) from created_at
        ts_rows = (
            db.session.query(
                func.date(Request.created_at).label("day"),
                func.count(Request.id).label("cnt"),
            )
            .filter(Request.created_at.isnot(None))
            .filter(Request.created_at >= since_dt)
            .group_by("day")
            .order_by("day")
            .all()
        )
        timeseries = [{"date": str(day), "count": int(cnt)} for day, cnt in ts_rows]

        # Volunteer count (safe fallback)
        try:
            from ..models import Volunteer  # local import to avoid import issues

            total_volunteers = db.session.query(Volunteer).count()
        except Exception:
            total_volunteers = 0

        return (
            jsonify(
                {
                    "total_requests": total_requests,
                    "total_volunteers": total_volunteers,
                    "counts_by_status": counts_by_status,
                    "requests_by_city": requests_by_city,
                    "timeseries": timeseries,
                }
            ),
            200,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/export", methods=["GET"])
@require_roles("admin")
def export():
    fmt = (request.args.get("format") or "excel").lower()
    filters = {
        k: request.args.get(k)
        for k in ("date_from", "date_to", "status", "region", "volunteer_id")
    }
    try:
        actor_id = getattr(g, "api_user_id", None)
        actor = db.session.get(AdminUser, int(actor_id)) if actor_id else None
        if actor is None or not bool(getattr(actor, "is_active", False)):
            return jsonify({"error": "Forbidden"}), 403

        role = canonical_role(getattr(actor, "role", None))
        is_global_admin = role == "superadmin" and getattr(actor, "structure_id", None) is None
        structure_id = None if is_global_admin else getattr(actor, "structure_id", None)
        if not is_global_admin and structure_id is None:
            return jsonify({"error": "Forbidden"}), 403

        path, mimetype, filename = controller.export_requests(
            filters,
            fmt,
            structure_id=structure_id,
            allow_global=is_global_admin,
        )
        return send_file(
            path, mimetype=mimetype, as_attachment=True, download_name=filename
        )
    except PermissionError:
        return jsonify({"error": "Forbidden"}), 403
    except NotImplementedError:
        return jsonify({"error": "format not supported"}), 400
    except RuntimeError as e:
        # ясно съобщение при липсващи зависимости (pandas/openpyxl)
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/admin/change_status", methods=["POST", "OPTIONS"])
def change_status():
    if request.method == "OPTIONS":
        response = jsonify({})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        return response

    try:
        data = request.get_json()
        request_id = data.get("request_id")
        new_status = data.get("status")

        print(f"Received data: {data}")  # Debug log

        if not request_id or not new_status:
            return jsonify({"success": False, "message": "Invalid data"}), 400

        req = db.session.get(Request, request_id)
        if not req:
            return jsonify({"success": False, "message": "Request not found"}), 404

        req.status = new_status
        db.session.commit()

        # Add log entry
        log = RequestLog(request_id=request_id, status=new_status)
        db.session.add(log)
        db.session.commit()

        print(f"Status changed for request {request_id} to {new_status}")  # Debug log

        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        return response
    except Exception as e:
        print(f"Error in change_status: {e}")  # Debug log
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route("/admin/delete_request", methods=["POST", "OPTIONS"])
def delete_request():
    if request.method == "OPTIONS":
        response = jsonify({})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        return response

    try:
        data = request.get_json()
        request_id = data.get("request_id")

        print(f"Deleting request: {request_id}")  # Debug log

        if not request_id:
            return jsonify({"success": False, "message": "Invalid request ID"}), 400

        req = db.session.get(Request, request_id)
        if not req:
            return jsonify({"success": False, "message": "Request not found"}), 404

        # Delete logs first
        RequestLog.query.filter_by(request_id=request_id).delete()
        db.session.delete(req)
        db.session.commit()

        print(f"Deleted request {request_id}")  # Debug log

        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type")
        response.headers.add("Access-Control-Allow-Methods", "POST")
        return response
    except Exception as e:
        print(f"Error in delete_request: {e}")  # Debug log
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route("/volunteers/nearby", methods=["GET"])
@require_roles("admin")
def get_nearby_volunteers():
    try:
        lat = float(request.args.get("lat", 0))
        lng = float(request.args.get("lng", 0))
        radius_km = float(request.args.get("radius", 10))  # default 10km
        include_contacts = (
            request.args.get("include_contacts", "false").lower() == "true"
        )
        can_see_contacts = include_contacts and (
            getattr(g, "api_is_admin", False)
            or getattr(g, "api_role", None) == "coordinator"
        )

        # Simple distance calculation using Haversine formula
        # For production, consider using PostGIS or similar
        from math import atan2, cos, radians, sin, sqrt

        def haversine_distance(lat1, lon1, lat2, lon2):
            R = 6371  # Earth radius in km
            dlat = radians(lat2 - lat1)
            dlon = radians(lon2 - lon1)
            a = (
                sin(dlat / 2) ** 2
                + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
            )
            c = 2 * atan2(sqrt(a), sqrt(1 - a))
            return R * c

        from ..models import Volunteer

        volunteers = Volunteer.query.filter(
            Volunteer.latitude.isnot(None), Volunteer.longitude.isnot(None)
        ).all()

        nearby = []
        for vol in volunteers:
            if vol.latitude and vol.longitude:
                distance = haversine_distance(lat, lng, vol.latitude, vol.longitude)
                if distance <= radius_km:
                    v_data = {
                        "id": vol.id,
                        "name": vol.name,
                        "skills": vol.skills,
                        "location": vol.location,
                        "latitude": vol.latitude,
                        "longitude": vol.longitude,
                        "distance_km": round(distance, 2),
                    }
                    if can_see_contacts:
                        v_data["email"] = vol.email
                        v_data["phone"] = vol.phone
                    nearby.append(v_data)

        # Sort by distance
        nearby.sort(key=lambda x: x["distance_km"])

        return (
            jsonify(
                {
                    "volunteers": nearby,
                    "count": len(nearby),
                    "search_location": {"lat": lat, "lng": lng},
                    "radius_km": radius_km,
                }
            ),
            200,
        )

    except ValueError:
        return jsonify({"error": "Invalid coordinates or radius"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/volunteers/<int:volunteer_id>/location", methods=["PUT"])
def update_volunteer_location(volunteer_id):
    try:
        if not (
            getattr(g, "api_is_admin", False)
            or getattr(current_user, "is_authenticated", False)
            or session.get("volunteer_logged_in")
        ):
            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json()
        lat = data.get("latitude")
        lng = data.get("longitude")
        location = data.get("location")

        if lat is None or lng is None:
            return jsonify({"error": "latitude and longitude required"}), 400

        from ..models import Volunteer

        vol = db.session.get(Volunteer, volunteer_id)
        if not vol:
            return jsonify({"error": "Volunteer not found"}), 404

        vol.latitude = float(lat)
        vol.longitude = float(lng)
        if location is not None:
            vol.location = location
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "volunteer_id": volunteer_id,
                    "location": {
                        "lat": vol.latitude,
                        "lng": vol.longitude,
                        "location": vol.location,
                    },
                }
            ),
            200,
        )

    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.get("/public/impact")
def public_impact():
    """Privacy-safe impact stats (no personal data)."""
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        last_24h_from = now - timedelta(hours=24)
        last_7d_from = now - timedelta(days=7)
        last_30d_from = now - timedelta(days=30)

        # Define "active" as not in done/completed/rejected/closed
        inactive_statuses = ("done", "completed", "rejected", "closed")
        active_count = (
            db.session.query(func.count(Request.id))
            .filter(
                or_(Request.status.is_(None), ~Request.status.in_(inactive_statuses))
            )
            .scalar()
        )

        new_24h = (
            db.session.query(func.count(Request.id))
            .filter(Request.created_at >= last_24h_from)
            .scalar()
        )

        matched_24h = (
            db.session.query(func.count(Request.id))
            .filter(Request.assigned_volunteer_id.isnot(None))
            .filter(Request.updated_at.isnot(None))
            .filter(Request.updated_at >= last_24h_from)
            .scalar()
        )

        completed_7d = (
            db.session.query(func.count(Request.id))
            .filter(Request.completed_at.isnot(None))
            .filter(Request.completed_at >= last_7d_from)
            .scalar()
        )

        # SLA metrics from RequestMetric if available
        avg_first_response = (
            db.session.query(func.avg(RequestMetric.time_to_assign))
            .join(Request, Request.id == RequestMetric.request_id)
            .filter(Request.created_at >= last_7d_from)
            .filter(RequestMetric.time_to_assign.isnot(None))
            .scalar()
        )
        avg_first_response_minutes = None
        if avg_first_response is not None:
            avg_first_response_minutes = round(float(avg_first_response) / 60, 1)

        avg_resolution = (
            db.session.query(func.avg(RequestMetric.time_to_complete))
            .join(Request, Request.id == RequestMetric.request_id)
            .filter(Request.created_at >= last_30d_from)
            .filter(RequestMetric.time_to_complete.isnot(None))
            .scalar()
        )
        avg_resolution_hours = None
        if avg_resolution is not None:
            avg_resolution_hours = round(float(avg_resolution) / 3600, 1)

        # Categories last 7d with k-anonymity (k>=3)
        cat_rows = (
            db.session.query(Request.category, func.count(Request.id))
            .filter(Request.created_at >= last_7d_from)
            .filter(Request.category.isnot(None))
            .group_by(Request.category)
            .all()
        )
        categories = []
        other_count = 0
        for cat, cnt in cat_rows:
            if cnt < 3:
                other_count += cnt
            else:
                categories.append({"category": cat, "count": int(cnt)})
        if other_count > 0:
            categories.append({"category": "other", "count": other_count})
        categories.sort(key=lambda x: x["count"], reverse=True)

        data = {
            "generated_at": now.isoformat() + "Z",
            "window": {
                "last_24h": {
                    "from": last_24h_from.isoformat() + "Z",
                    "to": now.isoformat() + "Z",
                },
                "last_7d": {
                    "from": last_7d_from.isoformat() + "Z",
                    "to": now.isoformat() + "Z",
                },
                "last_30d": {
                    "from": last_30d_from.isoformat() + "Z",
                    "to": now.isoformat() + "Z",
                },
            },
            "counts": {
                "active_requests": int(active_count or 0),
                "new_last_24h": int(new_24h or 0),
                "matched_last_24h": int(matched_24h or 0),
                "completed_last_7d": int(completed_7d or 0),
            },
            "sla": {
                "avg_first_response_minutes_7d": avg_first_response_minutes,
                "avg_resolution_hours_30d": avg_resolution_hours,
            },
            "categories_last_7d": categories,
            "privacy": {
                "k_min": 3,
                "notes": "Counts may be bucketed/hidden when below k to reduce re-identification risk.",
            },
        }
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@api_bp.route("/ai", methods=["POST"])
def ai_endpoint():
    from flask import request, jsonify

    payload = request.get_json(silent=True) or {}
    message = payload.get("message")

    if not message:
        return jsonify({"error": "message is required"}), 400

    return jsonify({
        "response": f"Получих: {message}",
        "status": "ok"
    }), 200
