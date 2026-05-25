import json
import os
import time

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)
from flask_babel import gettext as _

from ..extensions import csrf
from ..services.telemetry_policy import (
    classify_public_telemetry_request,
    extract_event_path,
    get_client_ip,
)

analytics_bp = Blueprint(
    "analytics",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "..", "..", "templates"),
)
csrf.exempt(analytics_bp)


@analytics_bp.route("/analytics")
def analytics_page():
    # Redirect to admin analytics if user is admin, otherwise to login
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_analytics"))
    else:
        flash(_("Analytics is available only to administrators."), "info")
        return redirect(url_for("admin_login"))


@analytics_bp.route("/api/analytics/data")
def analytics_data():
    # Require admin login for analytics data
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    try:
        from ....analytics_service import analytics_service

        # Get dashboard analytics from analytics service
        dashboard_data = analytics_service.get_dashboard_analytics(days=30)

        return jsonify(dashboard_data)

    except Exception as e:
        current_app.logger.error(f"Error getting analytics data: {e}")
        return jsonify({"error": "Failed to load analytics data"}), 500


def _bookmarks_path():
    path = os.path.join(current_app.instance_path, "analytics_bookmarks.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
    return path


@analytics_bp.route("/api/analytics/bookmarks", methods=["GET", "POST", "DELETE"])
def analytics_bookmarks():
    # Require admin login for bookmarks
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    path = _bookmarks_path()
    if request.method == "GET":
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    if request.method == "POST":
        payload = request.get_json(force=True)
        with open(path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data.append(payload)
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify(payload), 201
    if request.method == "DELETE":
        name = request.args.get("name")
        with open(path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data = [b for b in data if b.get("name") != name]
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"deleted": name}), 200


@analytics_bp.route("/analytics/stream")
def analytics_stream():
    # Require admin login for stream
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    # Non-blocking fallback: Ð²Ñ€ÑŠÑ‰Ð°Ð¼Ðµ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸ n ÑÑŠÐ±Ð¸Ñ‚Ð¸Ñ ÐºÐ°Ñ‚Ð¾ JSON.
    # Ð—Ð° Ñ€ÐµÐ°Ð»Ð½Ð° SSE Ñ€ÐµÐ°Ð»Ð¸Ð·Ð°Ñ†Ð¸Ñ Ð¸Ð·Ð¿Ð¾Ð»Ð·Ð²Ð°Ð¹ Ð¾Ñ‚Ð´ÐµÐ»ÐµÐ½ ASGI endpoint (EventSourceResponse Ð¾Ñ‚ starlette/fastapi)
    sample_events = [
        {"ts": int(time.time()), "msg": "new_analytics_event"},
    ]
    return jsonify({"sse_enabled": False, "events": sample_events})
from hashlib import sha256


def _hash_ip(value: str | None) -> str | None:
    if not value:
        return None
    salt = current_app.config.get("SECRET_KEY", "helpchain-local-dev")
    return sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:24]


@analytics_bp.route("/events", methods=["POST"])
def collect_event():
    """Collect real first-party analytics events.

    Stores privacy-conscious telemetry only:
    - no raw IP address
    - hashed IP only
    - no credentials
    """
    try:
        from backend.extensions import db
        from backend.models_with_analytics import AnalyticsEvent

        payload = request.get_json(silent=True) or {}
        event_path = extract_event_path(payload)
        decision = classify_public_telemetry_request(
            event_path,
            user_agent=request.headers.get("User-Agent"),
            client_ip=get_client_ip(),
        )

        if not decision.should_persist:
            current_app.logger.info(
                "analytics event ignored: reason=%s path=%s",
                decision.reason,
                event_path or "-",
            )
            return jsonify({"ok": True, "ignored": True}), 200

        event_name = (
            payload.get("event")
            or payload.get("event_type")
            or payload.get("name")
            or "unknown"
        )

        props = payload.get("props") or payload.get("properties") or {}

        event = AnalyticsEvent(
            event_type=str(event_name)[:100],
            event_category=str(props.get("category") or "first_party")[:100],
            event_action=str(props.get("action") or event_name)[:100],
            event_label=str(props.get("label") or "")[:255],
            user_session=str(
                payload.get("session_id")
                or props.get("session_id")
                or request.cookies.get("session")
                or ""
            )[:128],
            user_type="admin" if session.get("admin_logged_in") else "guest",
            user_ip=_hash_ip(get_client_ip()),
            user_agent=(request.headers.get("User-Agent") or "")[:500],
            page_url=str(decision.canonical_path or event_path or "")[:500],
            page_title=str(payload.get("title") or props.get("title") or "")[:255],
            referrer=str(
                payload.get("referrer")
                or props.get("referrer")
                or request.headers.get("Referer")
                or ""
            )[:500],
            screen_resolution=str(props.get("screen") or props.get("screen_resolution") or "")[:20],
            device_type=str(props.get("device") or props.get("device_type") or "")[:50],
        )

        db.session.add(event)
        db.session.commit()

        return jsonify({"ok": True}), 201

    except Exception as exc:
        current_app.logger.warning("analytics event collection failed: %s", exc)
        return jsonify({"ok": False}), 500
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta


def _safe_rate(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


@analytics_bp.route("/admin/api/conversion-funnel")
def admin_conversion_funnel_api():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    try:
        from backend.models_with_analytics import AnalyticsEvent

        days = request.args.get("days", default=30, type=int)
        days = max(1, min(days, 365))
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

        events = (
            AnalyticsEvent.query
            .filter(AnalyticsEvent.created_at >= since)
            .order_by(AnalyticsEvent.created_at.desc())
            .all()
        )

        by_type = Counter(e.event_type for e in events)

        page_views = by_type.get("page_view", 0)
        cta_clicks = sum(count for event_type, count in by_type.items() if event_type.startswith("cta_"))
        form_submits = sum(count for event_type, count in by_type.items() if event_type.endswith("_form_submit"))

        page_stats = defaultdict(lambda: {"page": "", "views": 0, "clicks": 0, "submits": 0})
        cta_stats = Counter()

        for event in events:
            page = event.page_url or "/unknown"
            row = page_stats[page]
            row["page"] = page

            if event.event_type == "page_view":
                row["views"] += 1
            elif event.event_type.startswith("cta_"):
                row["clicks"] += 1
                cta_stats[event.event_type] += 1
            elif event.event_type.endswith("_form_submit"):
                row["submits"] += 1

        pages = []
        for row in page_stats.values():
            row["view_to_click"] = _safe_rate(row["clicks"], row["views"])
            row["click_to_submit"] = _safe_rate(row["submits"], row["clicks"])
            row["dropoff_after_click"] = max(0, row["clicks"] - row["submits"])
            pages.append(row)

        pages.sort(key=lambda item: (item["clicks"], item["views"]), reverse=True)

        return jsonify({
            "period_days": days,
            "summary": {
                "events": len(events),
                "page_views": page_views,
                "cta_clicks": cta_clicks,
                "form_submits": form_submits,
                "view_to_click": _safe_rate(cta_clicks, page_views),
                "click_to_submit": _safe_rate(form_submits, cta_clicks),
            },
            "pages": pages[:20],
            "top_ctas": [{"event": key, "count": value} for key, value in cta_stats.most_common(10)],
        })

    except Exception as exc:
        current_app.logger.warning("conversion funnel API failed: %s", exc)
        return jsonify({"error": "Failed to load conversion funnel"}), 500


@analytics_bp.route("/admin/conversion-dashboard")
def admin_conversion_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin.admin_login"))
    return current_app.response_class(_conversion_dashboard_html(), mimetype="text/html")


def _conversion_dashboard_html():
    return """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>HelpChain - Conversion Engine</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/vendor/jsdelivr/bootstrap-5.3.0.min.css">
  <link rel="stylesheet" href="/static/css/design-system.css">
  <link rel="stylesheet" href="/static/css/styles.css?v=20260214-reqcards">
  <style>
    body { background:#f6f8fb; }
    .hc-conv-wrap { max-width:1180px; margin:32px auto; padding:0 18px; }
    .hc-conv-hero { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:18px; }
    .hc-conv-title { font-size:28px; font-weight:800; margin:0; color:#0f172a; }
    .hc-conv-subtitle { color:#64748b; margin-top:6px; max-width:760px; }
    .hc-conv-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin:18px 0; }
    .hc-conv-card { background:#fff; border:1px solid #e5e7eb; border-radius:18px; padding:16px; box-shadow:0 10px 30px rgba(15,23,42,.05); }
    .hc-conv-label { color:#64748b; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
    .hc-conv-value { font-size:26px; font-weight:800; color:#0f172a; margin-top:5px; }
    .hc-conv-table { background:#fff; border:1px solid #e5e7eb; border-radius:18px; overflow:hidden; box-shadow:0 10px 30px rgba(15,23,42,.05); }
    .hc-conv-table table { margin:0; }
    @media (max-width:900px){ .hc-conv-grid{grid-template-columns:1fr 1fr;} .hc-conv-hero{display:block;} }
      .hc-rev-session { display:inline-flex; max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size:12px; color:#475569; background:#f8fafc; border:1px solid #e2e8f0; border-radius:999px; padding:4px 8px; }
    .hc-rev-tier { display:inline-flex; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:800; letter-spacing:.04em; }
    .hc-rev-tier--cold { background:#f1f5f9; color:#475569; }
    .hc-rev-tier--warm { background:#fef3c7; color:#92400e; }
    .hc-rev-tier--hot { background:#ffedd5; color:#c2410c; }
    .hc-rev-tier--ready { background:#dcfce7; color:#166534; }
    .hc-rev-pages { display:flex; flex-wrap:wrap; gap:6px; max-width:520px; }
    .hc-rev-chip { display:inline-flex; border-radius:999px; background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; padding:4px 8px; font-size:12px; font-weight:700; }
      .hc-rev-value { font-weight:800; font-size:16px; color:#0f172a; }
    .hc-rev-row-hot { background:linear-gradient(90deg, rgba(255,237,213,0.5), transparent); }
    .hc-rev-tier--hot { background:#fed7aa; color:#9a3412; }
    .hc-rev-tier--ready { background:#bbf7d0; color:#166534; }
      .hc-hot-alert { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; border:1px solid #fed7aa; background:#fff7ed; border-radius:16px; padding:14px 16px; margin-bottom:10px; }
    .hc-hot-alert strong { display:inline-flex; margin-right:8px; color:#9a3412; }
    .hc-hot-alert span { color:#0f172a; }
    .hc-hot-alert small { display:block; margin-top:6px; color:#64748b; }
    .hc-hot-alert__pages { display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end; max-width:480px; }
    .hc-hot-alert__pages span { border:1px solid #bfdbfe; background:#eff6ff; color:#1d4ed8; border-radius:999px; padding:4px 8px; font-size:12px; font-weight:700; }
    .hc-alert-empty { color:#64748b; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:14px; padding:14px; }
      .hc-rev-row--hot { background:linear-gradient(90deg, rgba(255,237,213,.8), #fff); }
    .hc-rev-row--warm { background:linear-gradient(90deg, rgba(254,243,199,.65), #fff); }
    .hc-rev-row--cold { opacity:.82; }
    .hc-conv-table table td { padding:14px 10px; vertical-align:middle; }
    .hc-conv-table table th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:#475569; }
  #revTotal {
  font-size: 28px;
  font-weight: 700;
  color: #16a34a;
  margin-left: 6px;
}
.hc-rev-tier--hot {
  background: #fed7aa;
  color: #9a3412;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 999px;
}
    .hc-rev-actions { display:flex; flex-wrap:wrap; gap:6px; }
    .hc-rev-action { border:1px solid #cbd5e1; background:#fff; color:#334155; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:800; text-decoration:none; cursor:pointer; }
    .hc-rev-action:hover { background:#f8fafc; color:#0f172a; }
    .hc-rev-action--primary { border-color:#2563eb; background:#eff6ff; color:#1d4ed8; }
    .hc-rev-row--dismissed { opacity:.45; filter:grayscale(.25); }
      .hc-rev-actions--muted { opacity:.45; }
  </style>
</head>
<body>
  <main class="hc-conv-wrap">
    <section class="hc-conv-hero">
      <div>
        <h1 class="hc-conv-title">Conversion Engine</h1>
        <p class="hc-conv-subtitle">
          Lecture reelle des evenements collectes par HelpChain : pages vues, clics CTA, formulaires soumis et points de friction.
        </p>
      </div>
      <select id="days" class="form-select form-select-sm" style="width:160px">
        <option value="7">7 jours</option>
        <option value="30" selected>30 jours</option>
        <option value="90">90 jours</option>
      </select>
    </section>

    <section class="hc-conv-grid">
      <article class="hc-conv-card"><div class="hc-conv-label">Events</div><div id="events" class="hc-conv-value">-</div></article>
      <article class="hc-conv-card"><div class="hc-conv-label">Page views</div><div id="views" class="hc-conv-value">-</div></article>
      <article class="hc-conv-card"><div class="hc-conv-label">CTA clicks</div><div id="clicks" class="hc-conv-value">-</div></article>
      <article class="hc-conv-card"><div class="hc-conv-label">View to click</div><div id="v2c" class="hc-conv-value">-</div></article>
      <article class="hc-conv-card"><div class="hc-conv-label">Click to submit</div><div id="c2s" class="hc-conv-value">-</div></article>
    </section>

    <section class="hc-conv-table">
      <table class="table table-hover align-middle">
        <thead>
          <tr>
            <th>Page</th>
            <th>Views</th>
            <th>CTA clicks</th>
            <th>Submits</th>
            <th>View to click</th>
            <th>Click to submit</th>
            <th>Drop-off</th>
          </tr>
        </thead>
        <tbody id="pagesBody">
          <tr><td colspan="7" class="text-muted">Chargement...</td></tr>
        </tbody>
      </table>
    </section>
          <section class="hc-conv-table" style="margin-top:20px">
      <h5 style="padding:16px">Hot Revenue Alerts</h5>
      <div id="revAlerts" style="padding:0 16px 16px">
        <div class="hc-alert-empty">Loading alerts...</div>
      </div>
    </section>
    <section class="hc-conv-table" style="margin-top:20px">
      <h5 style="padding:16px">Revenue Intelligence</h5>

      <div style="padding:0 16px 12px">
        <strong>Total estimated:</strong>
        <span id="revTotal">-</span>
<div style="padding:0 16px 12px; color:#b45309; font-weight:600">
  Potential lost: <span id="revLost">-</span>
</div>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>Session</th>
            <th>Score</th>
            <th>Tier</th>
            <th>EUR</th>
            <th>Pages</th><th>Action</th>
          </tr>
        </thead>
        <tbody id="revBody"></tbody>
      </table>
    </section>
  </main>

  <script src="/static/js/conversion-dashboard.js"></script>
  <script src="/static/js/revenue-intelligence.js"></script>
  <script src="/static/js/revenue-alerts.js"></script>
  <script src="/static/js/revenue-alert-dispatch.js"></script>
<section class="hc-conv-table" style="margin-top:20px">
  <h5 style="padding:16px">Recommended Actions</h5>

  <div id="revActions" style="padding:0 16px 16px"></div>
</section>

<script src="/static/js/revenue-actions.js"></script>
</body>
</html>
"""
@analytics_bp.route("/admin/api/revenue-intelligence")
def admin_revenue_intelligence():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    from backend.models_with_analytics import AnalyticsEvent
    from collections import defaultdict

    events = AnalyticsEvent.query.all()

    sessions = defaultdict(list)

    for e in events:
        sid = e.user_session or "anon"
        sessions[sid].append(e)

    results = []

    for sid, evts in sessions.items():
        score = 0
        pages = set()

        for e in evts:
            pages.add(e.page_url)

            if e.event_type == "page_view":
                score += 1
            elif e.event_type.startswith("cta_"):
                score += 10
            elif e.event_type == "demo_form_start":
                score += 30
            elif e.event_type == "demo_form_submit":
                score += 100

            if e.page_url == "/demo":
                score += 20

        if score >= 80:
            tier = "READY"
            value = 800
        elif score >= 40:
            tier = "HOT"
            value = 300
        elif score >= 10:
            tier = "WARM"
            value = 80
        else:
            tier = "COLD"
            value = 0

        results.append({
            "session": sid,
            "score": score,
            "tier": tier,
            "value": value,
            "pages": list(pages)
        })

    total_value = sum(r["value"] for r in results)

    return jsonify({
        "sessions": results,
        "total_estimated_revenue": total_value
    })




@analytics_bp.route("/admin/api/revenue-alerts")
def admin_revenue_alerts():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    from backend.models_with_analytics import AnalyticsEvent
    from collections import defaultdict

    events = AnalyticsEvent.query.order_by(AnalyticsEvent.created_at.desc()).all()
    sessions = defaultdict(list)

    for e in events:
        sid = e.user_session or "anon"
        sessions[sid].append(e)

    alerts = []

    for sid, evts in sessions.items():
        score = 0
        pages = set()
        has_demo = False
        has_cta = False
        has_submit = False

        for e in evts:
            pages.add(e.page_url or "/unknown")

            if e.event_type == "page_view":
                score += 1
            if e.event_type.startswith("cta_"):
                score += 10
                has_cta = True
            if e.page_url == "/demo":
                score += 20
                has_demo = True
            if e.event_type.endswith("_form_submit"):
                score += 100
                has_submit = True

        if score >= 40 or (has_demo and has_cta and not has_submit):
            alerts.append({
                "session": sid[:12],
                "score": score,
                "level": "HOT" if score < 80 else "READY",
                "message": "Visitor proche conversion: demo/CTA detecte sans formulaire soumis.",
                "estimated_value": 300 if score < 80 else 800,
                "pages": list(pages)[:5],
            })

    alerts.sort(key=lambda a: a["score"], reverse=True)

    return jsonify({
        "count": len(alerts),
        "alerts": alerts[:10],
    })












_SENT_REVENUE_ALERTS = set()

@analytics_bp.route("/admin/api/revenue-alert-dispatch", methods=["POST"])
def admin_revenue_alert_dispatch():
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 403

    import smtplib
    import urllib.request
    from email.message import EmailMessage
    from backend.models_with_analytics import AnalyticsEvent
    from collections import defaultdict

    slack_webhook = os.getenv("HC_REVENUE_ALERT_SLACK_WEBHOOK")
    email_to = os.getenv("HC_REVENUE_ALERT_EMAIL_TO")
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_from = os.getenv("SMTP_FROM") or smtp_user

    events = AnalyticsEvent.query.all()
    sessions = defaultdict(list)

    for e in events:
        sessions[e.user_session or "anon"].append(e)

    dispatched = []

    for sid, evts in sessions.items():
        score = 0
        pages = set()
        has_demo = False
        has_cta = False
        has_submit = False

        for e in evts:
            pages.add(e.page_url or "/unknown")
            if e.event_type == "page_view":
                score += 1
            if e.event_type.startswith("cta_"):
                score += 10
                has_cta = True
            if e.page_url == "/demo":
                score += 20
                has_demo = True
            if e.event_type.endswith("_form_submit"):
                score += 100
                has_submit = True

        if not (score >= 40 or (has_demo and has_cta and not has_submit)):
            continue

        alert_key = f"{sid}:{score}"
        if alert_key in _SENT_REVENUE_ALERTS:
            continue

        level = "READY" if score >= 80 else "HOT"
        value = 800 if score >= 80 else 300
        message = f"[HelpChain] {level} visitor detected | score={score} | estimated={value} EUR | pages={', '.join(list(pages)[:5])}"

        if slack_webhook:
            try:
                payload = json.dumps({"text": message}).encode("utf-8")
                req = urllib.request.Request(slack_webhook, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
            except Exception as exc:
                current_app.logger.warning("Revenue Slack alert failed: %s", exc)

        if email_to and smtp_host and smtp_user and smtp_password and email_from:
            try:
                msg = EmailMessage()
                msg["Subject"] = "HelpChain HOT revenue alert"
                msg["From"] = email_from
                msg["To"] = email_to
                msg.set_content(message)

                with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.send_message(msg)
            except Exception as exc:
                current_app.logger.warning("Revenue email alert failed: %s", exc)

        _SENT_REVENUE_ALERTS.add(alert_key)
        dispatched.append({"session": sid[:12], "level": level, "score": score, "value": value})

    return jsonify({"ok": True, "dispatched": dispatched, "count": len(dispatched)})

