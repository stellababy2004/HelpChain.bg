import os
import tempfile
import logging
from datetime import UTC, datetime, timedelta

from flask import render_template

from backend.core.tenant import current_structure_id
from backend.extensions import db
from backend.models import Request

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return naive UTC timestamp without relying on datetime.utcnow."""
    return datetime.now(UTC).replace(tzinfo=None)


# опитваме се да намерим моделите; ако липсват - не вдигаме ImportError при зареждане
MODELS_AVAILABLE = True
try:
    from backend.models import (
        HelpRequest,  # alias към Request
        Volunteer,
    )
except Exception:
    try:
        from backend.models import HelpRequest, Volunteer
    except Exception:
        HelpRequest = None
        Volunteer = None
        MODELS_AVAILABLE = False

try:
    from backend.models import AuditLog
except Exception:
    AuditLog = None


class HelpChainController:
    def __init__(self):
        logger.info("helpchain_controller_initialized")
        pass

    def create_help_request(self, data):
        # Логика за създаване на заявка за помощ
        pass

    def get_help_requests(self):
        # Логика за извличане на всички заявки за помощ
        pass

    def update_help_request(self, request_id, data):
        # Логика за актуализиране на заявка за помощ
        pass

    def delete_help_request(self, request_id):
        # Логика за изтриване на заявка за помощ
        pass

    def approve_request(self, help_id, admin_user, note=None):
        # ... изпълни логиката за одобрение (update status, save)
        # записваме audit запис
        log = AuditLog(
            action="approve",
            actor_id=getattr(admin_user, "id", None),
            actor_name=getattr(admin_user, "username", None),
            target_type="help_request",
            target_id=help_id,
            details={"note": note} if note else None,
        )
        db.session.add(log)
        db.session.commit()
        # върни резултат (съществуващ код)
        return {"ok": True}

    def reject_request(self, help_id, admin_user, reason=None):
        # ... reject logic ...
        log = AuditLog(
            action="reject",
            actor_id=getattr(admin_user, "id", None),
            actor_name=getattr(admin_user, "username", None),
            target_type="help_request",
            target_id=help_id,
            details={"reason": reason} if reason else None,
        )
        db.session.add(log)
        db.session.commit()
        return {"ok": True}

    def _apply_filters_query(self, q, filters):
        if not MODELS_AVAILABLE:
            return q
        if filters.get("date_from"):
            q = q.filter(
                Request.created_at >= datetime.fromisoformat(filters["date_from"])
            )
        if filters.get("date_to"):
            q = q.filter(
                Request.created_at <= datetime.fromisoformat(filters["date_to"])
            )
        if filters.get("status"):
            q = q.filter(Request.status == filters["status"])
        if filters.get("region"):
            q = q.filter(Request.region == filters["region"])
        # volunteer_id липсва в Request; пропускаме
        return q

    def get_dashboard_stats(self, filters):
        if not MODELS_AVAILABLE:
            return {
                "counts_by_status": [],
                "requests_by_city": [],
                "top_request_types": [],
                "timeseries": [],
                "warning": "Models HelpRequest/Volunteer not found - implement src/models/help_request.py and src/models/volunteer.py to enable DB stats.",
            }

        out = {}
        q = db.session.query(Request)
        q = self._apply_filters_query(q, filters)

        # counts by status
        status_counts = (
            db.session.query(Request.status, db.func.count(Request.id))
            .group_by(Request.status)
            .all()
        )
        out["counts_by_status"] = [{"status": s, "count": c} for s, c in status_counts]

        # requests by city (top 10)
        city_rows = (
            db.session.query(Request.city, db.func.count(Request.id))
            .filter(Request.city.isnot(None), Request.city != "")
            .group_by(Request.city)
            .order_by(db.func.count(Request.id).desc())
            .limit(10)
            .all()
        )
        out["requests_by_city"] = [
            {"city": city, "count": cnt} for city, cnt in city_rows
        ]

        # top request categories
        types = (
            db.session.query(Request.category, db.func.count(Request.id))
            .group_by(Request.category)
            .order_by(db.func.count(Request.id).desc())
            .limit(10)
            .all()
        )
        out["top_request_types"] = [{"type": t, "count": cnt} for t, cnt in types]

        # time series active vs completed (simple last 30 days)
        series = []
        today = utc_now().date()
        for i in range(30):
            day = today - timedelta(days=29 - i)
            start = datetime.combine(day, datetime.min.time())
            end = datetime.combine(day, datetime.max.time())
            active = (
                db.session.query(Request)
                .filter(Request.created_at <= end, Request.status != "completed")
                .count()
            )
            completed = (
                db.session.query(Request)
                .filter(Request.updated_at >= start, Request.status == "completed")
                .count()
            )
            series.append(
                {"date": day.isoformat(), "active": active, "completed": completed}
            )
        out["timeseries"] = series

        out["total_requests"] = db.session.query(Request).count()
        out["total_volunteers"] = (
            db.session.query(Volunteer).count() if Volunteer else 0
        )

        return out

    def export_requests(
        self,
        filters,
        fmt="excel",
        *,
        structure_id: int | None = None,
        allow_global: bool = False,
    ):
        if not MODELS_AVAILABLE:
            raise RuntimeError(
                "Models HelpRequest/Volunteer not found. Add them under src/models/ to enable export."
            )

        q = db.session.query(HelpRequest)
        if allow_global:
            pass
        elif structure_id is not None:
            q = q.filter(HelpRequest.structure_id == int(structure_id))
        else:
            raise PermissionError("tenant scope required for export")
        q = self._apply_filters_query(q, filters)
        rows = []
        for r in q.all():
            rows.append(
                {
                    "id": r.id,
                    "title": getattr(r, "title", None),
                    "status": getattr(r, "status", None),
                    "type": getattr(r, "type", None),
                    "region": getattr(r, "region", None),
                    "volunteer": getattr(getattr(r, "volunteer", None), "name", None),
                    "created_at": getattr(r, "created_at", None),
                    "updated_at": getattr(r, "updated_at", None),
                }
            )

        tmpdir = tempfile.gettempdir()
        if fmt == "excel":
            try:
                import pandas as pd  # local import
            except Exception:
                pd = None

            path = os.path.join(
                tmpdir, f"help_requests_{int(utc_now().timestamp())}.xlsx"
            )
            if pd is not None:
                df = pd.DataFrame(rows)
                df.to_excel(path, index=False, engine="openpyxl")
            else:
                try:
                    from openpyxl import Workbook
                except Exception as e:
                    raise RuntimeError(
                        "openpyxl is required for excel export. Install with: pip install openpyxl"
                    ) from e
                workbook = Workbook()
                sheet = workbook.active
                headers = [
                    "id",
                    "title",
                    "status",
                    "type",
                    "region",
                    "volunteer",
                    "created_at",
                    "updated_at",
                ]
                sheet.append(headers)
                for row in rows:
                    sheet.append([row.get(header) for header in headers])
                workbook.save(path)
            return (
                path,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                os.path.basename(path),
            )
        elif fmt == "pdf":
            try:
                import pandas as pd  # local import
            except Exception as e:
                raise RuntimeError(
                    "pandas is required for export_requests. Install with: pip install pandas openpyxl"
                ) from e
            df = pd.DataFrame(rows)
            try:
                from jinja2 import Template
                from weasyprint import HTML
            except Exception as e:
                raise RuntimeError(
                    "weasyprint/jinja2 required for PDF export. Install with: pip install weasyprint jinja2"
                ) from e
            html = Template("""
            <h1>Help Requests</h1>
            <table border="1" cellspacing="0" cellpadding="4">
            <tr>{% for c in cols %}<th>{{c}}</th>{% endfor %}</tr>
            {% for row in rows %}
            <tr>{% for c in cols %}<td>{{ row[c] }}</td>{% endfor %}</tr>
            {% endfor %}
            </table>
            """).render(
                cols=list(df.columns), rows=df.fillna("").to_dict(orient="records")
            )
            path = os.path.join(
                tmpdir, f"help_requests_{int(utc_now().timestamp())}.pdf"
            )
            HTML(string=html).write_pdf(path)
            return path, "application/pdf", os.path.basename(path)
        else:
            raise NotImplementedError()

    def create_help(self, data):
        logger.info(
            "help_request_create_started has_email=%s has_phone=%s category=%s",
            bool(data.get("email")),
            bool(data.get("phone")),
            data.get("category"),
        )
        # Create a new request
        req = Request(
            name=data.get("name"),
            phone=data.get("phone"),
            email=data.get("email"),
            location=data.get("location"),
            category=data.get("category"),
            description=data.get("description"),
            urgency=data.get("urgency"),
            status="pending",
            structure_id=current_structure_id(),
        )
        db.session.add(req)
        db.session.commit()
        logger.info("help_request_create_succeeded request_id=%s", req.id)
        return {"success": True, "id": req.id}

    def render_category_template(self, category, COMMON):
        show_emergency = category["ui"].get("severity") == "critical"
        return render_template(
            "request_category.html",
            category=category,
            COMMON=COMMON,
            show_emergency=show_emergency,
        )
