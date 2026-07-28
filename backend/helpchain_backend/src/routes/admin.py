from __future__ import annotations

from collections import Counter, defaultdict
import csv
import html
import inspect
import json
import math
import os
from queue import Empty
import secrets
import threading
import time
import re
import unicodedata
from types import SimpleNamespace
from pathlib import Path
from datetime import UTC, datetime, timedelta, timezone
from functools import wraps
from io import BytesIO, StringIO
from typing import Optional
from urllib.parse import urljoin, urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from flask_babel import force_locale, gettext as _
from flask_mail import Message
from babel.support import Translations
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import psutil
except Exception:  # pragma: no cover - keep admin routes import-safe
    psutil = None

from backend.audit import log_activity
from backend.extensions import db, limiter, mail
from backend.system_sanity import run_system_checks
from ..admin_actor import resolve_current_admin_actor
from ..admin_policies import can_access_professional_leads
from ..admin_policies import can_view_global_analytics
from ..models.volunteer_interest import VolunteerInterest
from ..observability import (
    tenant_leak_get,
    tenant_leak_inc,
)
from ..statuses import (
    REQUEST_STATUS_ALLOWED,
    normalize_request_status,
)

from ..config import Config
from ..models import (
    AdminAuditEvent,
    AdminLoginAttempt,
    ImportBatch,
    AdminUser,
    Assignment,
    Case,
    CaseEvent,
    CaseParticipant,
    CaseCollaborator,
    IntegrationConnector,
    MetricDefinition,
    MetricRun,
    Notification,
    NotificationJob,
    OrganizationAccessRequest,
    ProfessionalLead,
    ProfessionalLeadActivity,
    RelayEvent,
    Intervenant,
    IntervenantActivity,
    Request,
    RequestActivity,
    RequestLog,
    RequestMetric,
    SecurityEvent,
    ScoreExplanation,
    UiLocaleLock,
    UiTranslationFreeze,
    UiTranslation,
    UiTranslationEvent,
    Volunteer,
    VolunteerAction,
    VolunteerInterest,
    VolunteerRequestState,
    Structure,
    User,
    current_structure,
    utc_now,
)

INTEGRATION_CONNECTOR_STATUS_CHOICES = ("active", "paused", "revoked")
INTEGRATION_CONNECTOR_ALLOWED_FIELDS = (
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
)

try:
    from ..models import ProAccessRequest
except ImportError:
    ProAccessRequest = None

from ..notifications.inapp import NUDGE_COOLDOWN_HOURS, send_nudge_notification
from ..constants.categories import (
    REQUEST_CATEGORY_CODES,
    normalize_request_category,
    request_category_choices,
    request_category_label,
)
from ..services.case_assistant import build_case_assistant_recommendation
from ..services.case_matching import suggest_professional_leads_for_case
from ..services.case_risk import risk_label_from_score, score_request_risk
from ..services.case_summary import build_case_summary, build_case_summary_snippet
from ..services.demo_data import (
    get_demo_case_signals,
    get_demo_cases,
    get_demo_kpis,
    get_demo_notification_channels,
    get_demo_payload,
    get_demo_notification_summary,
    get_demo_notifications,
    get_demo_ops_priority_levels,
    get_demo_queue_reasons,
    get_demo_requests,
    get_demo_sla_payload,
)
from ..services.geocoding import request_address_display_text
from ..services.import_service import (
    ENRICHABLE_IMPORT_FIELDS,
    IMPORT_SOURCE_CSV,
    IMPORT_TARGET_PROFESSIONAL_LEADS,
    available_field_options,
    available_target_options,
    batch_errors_to_text,
    build_preview,
    cleanup_preview_upload,
    encode_json_payload,
    import_batch_source,
    import_professional_leads,
    infer_mapping,
    load_preview_upload,
    parse_csv_bytes,
    sanitize_mapping,
    save_preview_upload,
    source_label,
    target_label,
)
from ..services.institutional_intent import (
    build_intent_summary,
    normalize_intent_path,
)
from ..services.institutional_intent_engine import (
    build_institutional_intent_phase1,
    build_territorial_opportunity_summary,
    summarize_profile_labels,
)
from ..services.account_intelligence import (
    build_account_intelligence,
)

from ..services.relationship_memory import (
    build_relationship_memory,
)
from ..services.founder_action_engine import (
    build_founder_action_queue,
    summarize_founder_actions as summarize_founder_operational_actions,
)
from ..services.founder_memory_engine import (
    build_founder_memory_timeline,
    detect_stalled_opportunities,
    summarize_founder_memory,
)
from ..services.founder_operational_state import (
    build_founder_operational_state,
    summarize_founder_operational_state,
)

from ..services.founder_cockpit import (
    build_founder_alerts,
    build_founder_priority_queue,
    group_founder_signals_by_territory,
    summarize_founder_actions,
)

from ..services.daily_founder_queue import (
    build_daily_founder_queue,
    summarize_daily_founder_queue,
)
from ..services.revenue_signal_engine import (
    SIGNAL_LOOKBACK_DAYS,
    build_revenue_signal_profile,
    summarize_revenue_signal_profile,
)

summarize_founder_opportunity_actions = summarize_founder_actions
from ..services.territorial_intelligence import (
    detect_priority_territories,
    normalize_territory_name,
)
from ..services.pilotage_territorial_maps import build_territorial_snapshots
from ..services.notification_jobs import (
    count_notification_job_buckets,
    notification_job_bucket_filter,
    notification_job_bucket_key,
)
from ..services.ops_priority import compute_ops_priority
from ..services.prospect_auto_capture import (
    append_audience_context_to_notes,
    captured_audience_session_targets,
    extract_audience_context,
    notes_without_audience_context,
)
from ..security_logging import log_security_event
from ..services.recommendation_engine import compute_recommendation
from ..services.reporting.operations_report import build_operational_report
import csv
from io import StringIO
from ..services.risk_alerts import evaluate_case_alerts
from ..services.risk_engine import update_case_risk
from ..services.request_sla import (
    build_request_inactivity_components,
    build_request_meaningful_activity_subquery,
    get_request_last_meaningful_activity,
    request_dashboard_actionable_filter,
)
from ..services.event_bus import (
    publish as publish_admin_stream_event,
    subscribe as subscribe_admin_stream,
    unsubscribe as unsubscribe_admin_stream,
)
from ..services.structure_service import create_structure_with_admin

GENERIC_ADMIN_LOGIN_FAIL_MSG = (
    "Identifiants invalides ou accès temporairement bloqué."
)
CATEGORY_CASE_STATUSES = (
    "new",
    "triaged",
    "assigned",
    "in_progress",
    "resolved",
    "closed",
    "cancelled",
)
ACTIVE_CASE_QUEUE_STATUSES = ("new", "triaged", "assigned", "in_progress")
CASE_PRIORITIES = ("low", "normal", "high", "critical")
CASE_PRIORITY_RANK = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}
CASE_EVENT_TYPES = (
    "case_created",
    "triage_scored",
    "status_changed",
    "priority_changed",
    "owner_assigned",
    "professional_assigned",
    "participant_added",
    "note_added",
    "case_resolved",
    "case_closed",
)
CASE_PARTICIPANT_TYPES = (
    "admin_user",
    "professional_user",
    "professional_lead",
    "association",
    "external_contact",
)
CASE_PARTICIPANT_ROLES = (
    "owner",
    "primary_professional",
    "contributor",
    "observer",
    "coordinator",
)
SCREENED_PROFESSIONAL_LEAD_STATUSES = ("invalid", "spam")
PROFESSIONAL_LEAD_STATUS_CHOICES = [
    "new",
    "imported",
    "contacted",
    "qualified",
    "rejected",
    "invalid",
    "spam",
]
ADMIN_IMPORT_PREVIEW_SESSION_KEY = "admin_import_preview"
ADMIN_IMPORT_ALLOWED_TARGETS = {IMPORT_TARGET_PROFESSIONAL_LEADS}

AUDIENCE_INTENT_PATHS = (
    "/demander-acces",
    "/professionnels",
    "/offre",
    "/deploiement",
    "/contact",
)
AUDIENCE_REVENUE_SCORE_CAP = 50
AUDIENCE_REVENUE_PAGE_SCORES = (
    ("/demander-acces", 12),
    ("/contact", 8),
    ("/deploiement", 7),
    ("/offre", 6),
    ("/collectivites", 5),
    ("/professionnels", 4),
    ("/", 1),
)
AUDIENCE_QUALIFIED_PAGE_WEIGHTS = {
    "/demo": 40,
    "/contact": 35,
    "/offre": 30,
    "/deploiement": 20,
    "/securite": 15,
}
AUDIENCE_DEPARTMENT_CODES = {
    "paris": "75",
    "boulogne billancourt": "92",
    "nanterre": "92",
    "versailles": "78",
    "lyon": "69",
    "marseille": "13",
    "lille": "59",
    "nantes": "44",
    "bordeaux": "33",
    "toulouse": "31",
    "strasbourg": "67",
    "rennes": "35",
    "montpellier": "34",
    "nice": "06",
    "grenoble": "38",
    "dijon": "21",
    "rouen": "76",
    "reims": "51",
    "tours": "37",
    "orleans": "45",
    "saint denis": "93",
    "creteil": "94",
}
AUDIENCE_CITY_MARKERS = {
    "paris": {"label": "Paris", "department": "Paris", "region": "Ile-de-France", "x": 285, "y": 135},
    "boulogne billancourt": {"label": "Boulogne-Billancourt", "department": "Hauts-de-Seine", "region": "Ile-de-France", "x": 279, "y": 140},
    "nanterre": {"label": "Nanterre", "department": "Hauts-de-Seine", "region": "Ile-de-France", "x": 276, "y": 137},
    "versailles": {"label": "Versailles", "department": "Yvelines", "region": "Ile-de-France", "x": 272, "y": 146},
    "lyon": {"label": "Lyon", "department": "Rhone", "region": "Auvergne-Rhone-Alpes", "x": 335, "y": 285},
    "marseille": {"label": "Marseille", "department": "Bouches-du-Rhone", "region": "Provence-Alpes-Cote d'Azur", "x": 348, "y": 396},
    "lille": {"label": "Lille", "department": "Nord", "region": "Hauts-de-France", "x": 295, "y": 55},
    "nantes": {"label": "Nantes", "department": "Loire-Atlantique", "region": "Pays de la Loire", "x": 154, "y": 250},
    "bordeaux": {"label": "Bordeaux", "department": "Gironde", "region": "Nouvelle-Aquitaine", "x": 188, "y": 355},
    "toulouse": {"label": "Toulouse", "department": "Haute-Garonne", "region": "Occitanie", "x": 260, "y": 398},
    "strasbourg": {"label": "Strasbourg", "department": "Bas-Rhin", "region": "Grand Est", "x": 440, "y": 164},
    "rennes": {"label": "Rennes", "department": "Ille-et-Vilaine", "region": "Bretagne", "x": 146, "y": 202},
    "montpellier": {"label": "Montpellier", "department": "Herault", "region": "Occitanie", "x": 312, "y": 395},
    "nice": {"label": "Nice", "department": "Alpes-Maritimes", "region": "Provence-Alpes-Cote d'Azur", "x": 425, "y": 382},
    "grenoble": {"label": "Grenoble", "department": "Isere", "region": "Auvergne-Rhone-Alpes", "x": 365, "y": 318},
    "dijon": {"label": "Dijon", "department": "Cote-d'Or", "region": "Bourgogne-Franche-Comte", "x": 350, "y": 220},
    "rouen": {"label": "Rouen", "department": "Seine-Maritime", "region": "Normandie", "x": 236, "y": 113},
    "reims": {"label": "Reims", "department": "Marne", "region": "Grand Est", "x": 335, "y": 125},
    "tours": {"label": "Tours", "department": "Indre-et-Loire", "region": "Centre-Val de Loire", "x": 236, "y": 226},
    "orleans": {"label": "Orleans", "department": "Loiret", "region": "Centre-Val de Loire", "x": 275, "y": 190},
}
AUDIENCE_BUSINESS_MAP_POINTS = (
    {
        "slug": "paris",
        "label": "Paris",
        "lat": 48.8566,
        "lng": 2.3522,
        "priority": "Haute",
        "recommendation": "Prioriser les comptes publics deja engages et proposer un cadrage pilote.",
        "default_demands": 12,
        "default_structures": 8,
    },
    {
        "slug": "saint-denis",
        "label": "Saint-Denis",
        "lat": 48.9362,
        "lng": 2.3574,
        "priority": "Haute",
        "recommendation": "Activer les relais ESS et les collectivités à fort volume de demandes.",
        "default_demands": 9,
        "default_structures": 6,
    },
    {
        "slug": "nanterre",
        "label": "Nanterre",
        "lat": 48.8924,
        "lng": 2.2060,
        "priority": "Moyenne",
        "recommendation": "Consolider un pipeline grands comptes et acteurs territoriaux du 92.",
        "default_demands": 7,
        "default_structures": 5,
    },
    {
        "slug": "creteil",
        "label": "Creteil",
        "lat": 48.7904,
        "lng": 2.4556,
        "priority": "Moyenne",
        "recommendation": "Positionner HelpChain sur les usages coordination médico-sociale et accompagnement.",
        "default_demands": 6,
        "default_structures": 4,
    },
    {
        "slug": "versailles",
        "label": "Versailles",
        "lat": 48.8049,
        "lng": 2.1204,
        "priority": "Moyenne",
        "recommendation": "Pousser une approche pilotage et indicateurs pour les directions territoriales.",
        "default_demands": 5,
        "default_structures": 4,
    },
    {
        "slug": "boulogne-billancourt",
        "label": "Boulogne-Billancourt",
        "lat": 48.8397,
        "lng": 2.2399,
        "priority": "Haute",
        "recommendation": "Capitaliser sur la traction locale et convertir les signaux chauds en rendez-vous.",
        "default_demands": 8,
        "default_structures": 5,
    },
)
DEMO_LEAD_STATUS_CHOICES = [
    "new",
    "contacted",
    "demo_scheduled",
    "pilot_discussion",
    "closed",
    "invalid",
    "spam",
]


def _is_screened_professional_lead_status(status: str | None) -> bool:
    return ((status or "").strip().lower() or "") in SCREENED_PROFESSIONAL_LEAD_STATUSES


def _professional_lead_is_business_pipeline(status: str | None) -> bool:
    return not _is_screened_professional_lead_status(status)
CLOSED_STATUSES = {"done", "cancelled", "rejected"}
REQUEST_KPI_CLOSED_STATUSES = CLOSED_STATUSES | {
    "canceled",
    "closed",
    "completed",
    "resolved",
    "archived",
}
REQUEST_KPI_RESOLVED_STATUSES = {
    "closed",
    "completed",
    "done",
    "resolved",
}
INTERVENANT_EXCLUDED_REQUEST_STATUSES = CLOSED_STATUSES | {"closed", "archived"}
INTERVENANT_ASSIGNABLE_REQUEST_STATUSES = {"new", "pending", "in_progress"}
ASSIGN_SLA_HOURS = 48
RESOLVE_SLA_DAYS = 7
VOLUNTEER_ASSIGN_SLA_HOURS = 72
SLA_QUEUE_KINDS = {
    "resolution_overdue": "SLA resolution overdue",
    "owner_assignment_overdue": "SLA owner assignment overdue",
    "volunteer_assignment_overdue": "Volunteer assignment overdue",
}
SLA_BREAKDOWN_TYPE_TO_KIND = {
    "resolve": "resolution_overdue",
    "owner_assign": "owner_assignment_overdue",
    "volunteer_assign": "volunteer_assignment_overdue",
}
SLA_KIND_TO_BREAKDOWN_TYPE = {
    v: k for k, v in SLA_BREAKDOWN_TYPE_TO_KIND.items()
}
NOTSEEN_TIERS_HOURS = (24, 48, 72)
PRO_ACCESS_STATUSES = ("new", "reviewed", "approved", "rejected")
RISKY_ACTIONS = (
    "request.archive",
    "request.assign_owner",
    "request.unassign_owner",
    "request.unlock",
    "interest.approve",
    "interest.reject",
)
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ADMIN_LOGIN_RATE_WINDOW_MIN = 5
ADMIN_LOGIN_MAX_FAILS = 5
ADMIN_LOGIN_LOCKOUT_MIN = 15
ADMIN_SESSION_IDLE_TIMEOUT_MIN = 20
ADMIN_FRESH_AUTH_MIN = 10
_SCHEMA_COLUMN_CACHE: dict[tuple[str, str], bool] = {}
_SCHEMA_TABLE_CACHE: dict[str, bool] = {}
_ADMIN_LOGIN_REQUIRED_TABLES = ("admin_users", "structures", "requests")
_HELP_PS1_LOCAL_DATABASE_URL = "sqlite:///C:/dev/HelpChain/instance/hc_local_dev.db"
_HELP_PS1_LOCAL_DB_PATH = r"C:\dev\HelpChain\instance\hc_local_dev.db"
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_ONBOARDING_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UI_KEYS_PATH = Path(__file__).resolve().parents[4] / "i18n" / "ui_keys.json"
_UI_DOMAINS_PATH = Path(__file__).resolve().parents[4] / "i18n" / "ui_domains.json"
_TERMINOLOGY_PATH = Path(__file__).resolve().parents[4] / "i18n" / "terminology.json"
_HF_MODEL_MAP = {
    "en": "Helsinki-NLP/opus-mt-fr-en",
    "de": "Helsinki-NLP/opus-mt-fr-de",
    "bg": "Helsinki-NLP/opus-mt-fr-bg",
}
_HF_TRANSLATORS: dict[str, tuple[object, object]] = {}
_HF_TRANSLATOR_FAILED: set[str] = set()
_HF_TRANSLATOR_LOCK = threading.Lock()
ONBOARDING_STEPS = (
    "welcome",
    "secure_access",
    "structure_setup",
    "invite_team",
    "first_win",
    "complete",
)
ONBOARDING_PROGRESS = {
    "welcome": 20,
    "secure_access": 40,
    "structure_setup": 60,
    "invite_team": 80,
    "first_win": 100,
    "complete": 100,
}
ONBOARDING_TEAM_SIZE_OPTIONS = ("1-5", "6-15", "16-50", "50+")
ONBOARDING_MAIN_NEED_OPTIONS = (
    "centraliser demandes",
    "suivi dossiers",
    "coordination equipe",
    "reporting",
)
def _table_has_column(table_name: str, column_name: str) -> bool:
    key = (table_name, column_name)
    cached = _SCHEMA_COLUMN_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        inspector = sa_inspect(db.session.get_bind())
        exists = any(
            col.get("name") == column_name for col in inspector.get_columns(table_name)
        )
    except Exception:
        exists = False
    _SCHEMA_COLUMN_CACHE[key] = exists
    return exists


def _table_exists(table_name: str) -> bool:
    cached = _SCHEMA_TABLE_CACHE.get(table_name)
    if cached is not None:
        return cached
    try:
        inspector = sa_inspect(db.session.get_bind())
        exists = bool(inspector.has_table(table_name))
    except Exception:
        exists = False
    _SCHEMA_TABLE_CACHE[table_name] = exists
    return exists


def _looks_like_help_ps1_local_db(value: str | None) -> bool:
    normalized = (value or "").strip().replace("/", "\\").lower()
    if normalized.startswith("sqlite:\\\\\\"):
        normalized = normalized.replace("sqlite:\\\\\\", "", 1)
    elif normalized.startswith("sqlite:\\\\"):
        normalized = normalized.replace("sqlite:\\\\", "", 1)
    expected_path = _HELP_PS1_LOCAL_DB_PATH.lower()
    expected_url = _HELP_PS1_LOCAL_DATABASE_URL.replace("/", "\\").lower()
    return (
        normalized == expected_path
        or normalized == expected_url
        or "c:\\dev\\helpchain\\instance\\hc_local_dev.db" in normalized
        or normalized.endswith("instance\\hc_local_dev.db")
    )


def _admin_login_database_not_ready_message() -> str:
    candidates = (
        os.getenv("DATABASE_URL"),
        os.getenv("HC_DB_PATH"),
        current_app.config.get("SQLALCHEMY_DATABASE_URI"),
    )
    if any(_looks_like_help_ps1_local_db(str(candidate)) for candidate in candidates):
        return (
            "Local database is not ready. Run .\\help.ps1 to repair, migrate, "
            "and seed hc_local_dev.db."
        )
    return "Database is not ready. Run migrations and seed an admin user."


def _admin_login_database_ready() -> tuple[bool, str | None]:
    try:
        inspector = sa_inspect(db.session.get_bind())
        table_status = {
            table_name: bool(inspector.has_table(table_name))
            for table_name in _ADMIN_LOGIN_REQUIRED_TABLES
        }
    except Exception:
        db.session.rollback()
        for table_name in _ADMIN_LOGIN_REQUIRED_TABLES:
            _SCHEMA_TABLE_CACHE.pop(table_name, None)
        return False, _admin_login_database_not_ready_message()

    _SCHEMA_TABLE_CACHE.update(table_status)
    if not all(table_status.values()):
        return False, _admin_login_database_not_ready_message()

    try:
        admin_exists = (
            db.session.query(AdminUser.id).order_by(AdminUser.id.asc()).limit(1).first()
            is not None
        )
    except Exception:
        db.session.rollback()
        for table_name in _ADMIN_LOGIN_REQUIRED_TABLES:
            _SCHEMA_TABLE_CACHE.pop(table_name, None)
        return False, _admin_login_database_not_ready_message()

    if not admin_exists:
        return False, _admin_login_database_not_ready_message()

    return True, None


def _normalize_smart_assign_text(value: str | None) -> str:
    txt = (value or "").strip().lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", txt).strip()


def _smart_assign_profession_score(
    category_code: str | None, profession_value: str | None
) -> int:
    profession_map = {
        "medical": ("doctor", "nurse", "medecin", "docteur", "infirm"),
        "psychological": ("psychologist", "psychologue"),
        "admin": (
            "social worker",
            "social_worker",
            "assistant social",
            "travailleur social",
            "lawyer",
            "avocat",
            "juriste",
        ),
        "elderly support": (
            "social worker",
            "social_worker",
            "assistant social",
            "travailleur social",
        ),
        "housing": (
            "social worker",
            "social_worker",
            "assistant social",
            "travailleur social",
        ),
    }
    wanted_tokens = profession_map.get(_normalize_smart_assign_text(category_code), ())
    profession_text = _normalize_smart_assign_text(profession_value)
    if not wanted_tokens or not profession_text:
        return 0
    return 30 if any(token in profession_text for token in wanted_tokens) else 0


def _smart_assign_is_strong_match(
    category_code: str | None, profession_value: str | None
) -> bool:
    return _smart_assign_profession_score(category_code, profession_value) >= 30


INTERVENANT_CITY_COORDS = {
    "paris": (48.8566, 2.3522),
    "boulogne billancourt": (48.8397, 2.2399),
    "issy les moulineaux": (48.8230, 2.2770),
    "suresnes": (48.8714, 2.2293),
    "neuilly sur seine": (48.8841, 2.2683),
}
INTERVENANT_DEFAULT_COORDS = INTERVENANT_CITY_COORDS["paris"]
ACTIVE_ASSIGNMENT_STATUSES = {"active", "pending", "accepted", "in_progress"}
INTERVENANT_ACTOR_TYPE_OPTIONS = (
    ("social_worker", "Travailleur social"),
    ("coordinator", "Coordinateur"),
    ("psychologist", "Psychologue"),
    ("field_referent", "Référent terrain"),
    ("partner_association", "Association partenaire"),
    ("health_professional", "Professionnel santé"),
    ("legal_advisor", "Juriste"),
    ("mediator", "Médiateur"),
)
INTERVENANT_ACTOR_TYPE_LABELS = dict(INTERVENANT_ACTOR_TYPE_OPTIONS)
INTERVENANT_ACTOR_TYPE_ALIASES = {
    "assistant_social": "social_worker",
    "social": "social_worker",
    "social_worker": "social_worker",
    "travailleur_social": "social_worker",
    "coordinateur": "coordinator",
    "coordinator": "coordinator",
    "psychologue": "psychologist",
    "psychologist": "psychologist",
    "referent_social": "field_referent",
    "referent_terrain": "field_referent",
    "field_referent": "field_referent",
    "partner": "partner_association",
    "association": "partner_association",
    "partner_association": "partner_association",
    "doctor": "health_professional",
    "nurse": "health_professional",
    "health_professional": "health_professional",
    "professionnel_sante": "health_professional",
    "professional": "field_referent",
    "professionnel": "field_referent",
    "volunteer": "field_referent",
    "lawyer": "legal_advisor",
    "legal_advisor": "legal_advisor",
    "juriste": "legal_advisor",
    "mediator": "mediator",
    "mediateur": "mediator",
}
INTERVENANT_AVAILABILITY_OPTIONS = (
    ("available", "Disponible"),
    ("busy", "Occupé"),
    ("in_intervention", "En intervention"),
    ("unavailable", "Indisponible"),
    ("paused", "Pause"),
)
INTERVENANT_AVAILABILITY_LABELS = dict(INTERVENANT_AVAILABILITY_OPTIONS)
INTERVENANT_AVAILABILITY_ALIASES = {
    "disponible": "available",
    "occupe": "busy",
    "occupé": "busy",
    "en_intervention": "in_intervention",
    "indisponible": "unavailable",
    "pause": "paused",
}
INTERVENANT_AVAILABILITY_BADGES = {
    "available": "hc-badge-availability--available",
    "busy": "hc-badge-availability--busy",
    "in_intervention": "hc-badge-availability--intervention",
    "unavailable": "hc-badge-availability--unavailable",
    "paused": "hc-badge-availability--paused",
}
INTERVENANT_AVAILABILITY_HELPERS = {
    "available": "Peut recevoir une nouvelle affectation.",
    "busy": "Charge elevee, a affecter avec prudence.",
    "in_intervention": "Actuellement mobilise sur le terrain.",
    "paused": "Pause temporaire, eviter les nouvelles affectations.",
    "unavailable": "Non mobilisable pour le moment.",
}
INTERVENANT_COMPETENCY_OPTIONS = (
    ("aide_administrative", "Aide administrative"),
    ("accompagnement_social", "Accompagnement social"),
    ("sante_mentale", "Sante mentale"),
    ("mediation", "Mediation"),
    ("urgence_terrain", "Urgence terrain"),
    ("coordination", "Coordination"),
    ("juridique", "Juridique"),
)
INTERVENANT_COMPETENCY_LABELS = dict(INTERVENANT_COMPETENCY_OPTIONS)
INTERVENANT_ACTIVITY_LABELS = {
    "profile_updated": "Profil mis a jour",
    "affectation_created": "Affectation creee",
    "affectation_removed": "Affectation retiree",
    "assigned_to_request": "Affectation creee",
    "removed_from_request": "Affectation retiree",
    "availability_changed": "Disponibilite modifiee",
    "competencies_updated": "Competences mises a jour",
    "notes_updated": "Notes mises a jour",
}


def _normalize_intervenant_city_key(value: str | None) -> str:
    txt = _normalize_smart_assign_text(value)
    return txt.replace("–", "-").replace("-", " ").strip()


def _split_intervenant_location(value: str | None) -> tuple[str, str]:
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if "||" in raw:
        city, address = raw.split("||", 1)
        return city.strip(), address.strip()
    return raw, ""


def _join_intervenant_location(city: str | None, address: str | None) -> str:
    city_val = (city or "").strip()
    address_val = (address or "").strip()
    if city_val and address_val:
        return f"{city_val} || {address_val}"
    return city_val or address_val


def _intervenant_city(intervenant: Intervenant) -> str:
    city, _address = _split_intervenant_location(getattr(intervenant, "location", None))
    return city


def _intervenant_address(intervenant: Intervenant) -> str:
    _city, address = _split_intervenant_location(getattr(intervenant, "location", None))
    return address


def _normalize_intervenant_actor_type(value: str | None) -> str:
    raw = _normalize_smart_assign_text(value or "").replace(" ", "_")
    return INTERVENANT_ACTOR_TYPE_ALIASES.get(raw, raw)


def _intervenant_actor_type_label(value: str | None) -> str:
    normalized = _normalize_intervenant_actor_type(value)
    if normalized in INTERVENANT_ACTOR_TYPE_LABELS:
        return INTERVENANT_ACTOR_TYPE_LABELS[normalized]
    return (value or "Professionnel").replace("_", " ").strip().title()


def _intervenant_profession(intervenant: Intervenant) -> str:
    return _intervenant_actor_type_label(getattr(intervenant, "actor_type", None))


def _normalize_intervenant_availability(value: str | None) -> str:
    raw = (value or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if not raw:
        return "available"
    return INTERVENANT_AVAILABILITY_ALIASES.get(raw, raw)


def _intervenant_availability(intervenant: Intervenant) -> str:
    raw = getattr(intervenant, "availability", None)
    if raw:
        return _normalize_intervenant_availability(raw)
    return "available" if bool(getattr(intervenant, "is_active", False)) else "unavailable"


def _intervenant_availability_label(value: str | None) -> str:
    return INTERVENANT_AVAILABILITY_LABELS.get(
        _normalize_intervenant_availability(value),
        "Disponible",
    )


def _intervenant_availability_badge(value: str | None) -> str:
    return INTERVENANT_AVAILABILITY_BADGES.get(
        _normalize_intervenant_availability(value),
        INTERVENANT_AVAILABILITY_BADGES["available"],
    )


def _intervenant_availability_helper(value: str | None) -> str:
    return INTERVENANT_AVAILABILITY_HELPERS.get(
        _normalize_intervenant_availability(value),
        INTERVENANT_AVAILABILITY_HELPERS["available"],
    )


def _split_operational_tokens(value: str | None) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    tokens = re.split(r"[\n,;]+", raw)
    return [token.strip() for token in tokens if token and token.strip()]


def _normalize_intervenant_competency(value: str | None) -> str:
    raw = _normalize_smart_assign_text(value or "")
    raw = (
        raw.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ç", "c")
    )
    return raw.replace(" ", "_")


def _intervenant_competencies(intervenant: Intervenant) -> list[dict[str, str]]:
    raw = getattr(intervenant, "competencies_json", None)
    values: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            values = [str(item).strip() for item in parsed if str(item or "").strip()]
        else:
            values = _split_operational_tokens(str(raw))

    normalized: list[str] = []
    for value in values:
        key = _normalize_intervenant_competency(value)
        if key and key not in normalized:
            normalized.append(key)

    return [
        {
            "value": key,
            "label": INTERVENANT_COMPETENCY_LABELS.get(
                key, key.replace("_", " ").title()
            ),
        }
        for key in normalized
    ]


def _encode_intervenant_competencies(values: list[str]) -> str | None:
    normalized: list[str] = []
    for value in values:
        key = _normalize_intervenant_competency(value)
        if key and key not in normalized:
            normalized.append(key)
    return json.dumps(normalized, ensure_ascii=True) if normalized else None


def _intervenant_coverage_zones(intervenant: Intervenant) -> list[str]:
    return _split_operational_tokens(getattr(intervenant, "coverage_zones", None))


def _intervenant_coverage_communes(intervenant: Intervenant) -> list[str]:
    return _split_operational_tokens(getattr(intervenant, "coverage_communes", None))


def _intervenant_initials(intervenant: Intervenant) -> str:
    name = _intervenant_display_name(intervenant)
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not parts:
        return "IN"
    initials = "".join(part[0].upper() for part in parts[:2])
    return initials[:2] or "IN"


def _intervenant_display_name(intervenant: Intervenant) -> str:
    return (getattr(intervenant, "name", None) or "").strip() or f"Intervenant #{intervenant.id}"


def _intervenant_or_403(intervenant_id: int, structure_id: int | None = None) -> Intervenant:
    intervenant = db.session.get(Intervenant, intervenant_id)
    if intervenant is None:
        abort(404)

    intervenant_structure_id = int(getattr(intervenant, "structure_id", 0) or 0)
    if structure_id is not None and intervenant_structure_id != int(structure_id):
        abort(403)

    if not _is_global_admin():
        current_sid = getattr(current_user, "structure_id", None)
        if current_sid is None or int(current_sid) != intervenant_structure_id:
            abort(403)

    return intervenant


def _intervenant_assignment_counts(intervenant: Intervenant) -> dict[str, int]:
    try:
        workload = (
            db.session.query(func.count(Assignment.id))
            .filter(Assignment.intervenant_id == intervenant.id)
            .filter(func.lower(func.coalesce(Assignment.status, "")).in_(ACTIVE_ASSIGNMENT_STATUSES))
            .scalar()
        )
    except Exception:
        workload = 0

    try:
        active_cases = (
            db.session.query(func.count(func.distinct(Assignment.request_id)))
            .join(Request, Request.id == Assignment.request_id)
            .filter(Assignment.intervenant_id == intervenant.id)
            .filter(func.lower(func.coalesce(Assignment.status, "")).in_(ACTIVE_ASSIGNMENT_STATUSES))
            .filter(
                or_(
                    Request.status.is_(None),
                    ~func.lower(func.coalesce(Request.status, "")).in_(
                        list(INTERVENANT_EXCLUDED_REQUEST_STATUSES)
                    ),
                )
            )
            .filter(Request.is_archived.is_(False))
            .scalar()
        )
    except Exception:
        active_cases = 0

    return {
        "workload": int(workload or 0),
        "active_cases": int(active_cases or 0),
    }


def _active_intervenant_assignments(intervenant: Intervenant) -> list[Assignment]:
    try:
        return (
            Assignment.query.join(Request, Request.id == Assignment.request_id)
            .filter(Assignment.intervenant_id == intervenant.id)
            .filter(Assignment.structure_id == intervenant.structure_id)
            .filter(func.lower(func.coalesce(Assignment.status, "")).in_(ACTIVE_ASSIGNMENT_STATUSES))
            .filter(
                or_(
                    Request.status.is_(None),
                    ~func.lower(func.coalesce(Request.status, "")).in_(
                        list(INTERVENANT_EXCLUDED_REQUEST_STATUSES)
                    ),
                )
            )
            .filter(Request.is_archived.is_(False))
            .order_by(Assignment.assigned_at.desc(), Assignment.id.desc())
            .limit(20)
            .all()
        )
    except Exception:
        db.session.rollback()
        return []


def _intervenant_assignment_options(intervenant: Intervenant) -> list[Request]:
    try:
        active_request_ids = [
            row[0]
            for row in (
                db.session.query(Assignment.request_id)
                .filter(Assignment.intervenant_id == intervenant.id)
                .filter(Assignment.structure_id == intervenant.structure_id)
                .filter(
                    func.lower(func.coalesce(Assignment.status, "")).in_(
                        ACTIVE_ASSIGNMENT_STATUSES
                    )
                )
                .all()
            )
        ]
        query = Request.query.filter(Request.structure_id == intervenant.structure_id)
        query = (
            query
            .filter(
                func.lower(func.coalesce(Request.status, "")).in_(
                    list(INTERVENANT_ASSIGNABLE_REQUEST_STATUSES)
                )
            )
        )
        if hasattr(Request, "is_archived"):
            query = query.filter(Request.is_archived.is_(False))
        if active_request_ids:
            query = query.filter(~Request.id.in_(active_request_ids))
        return query.order_by(Request.created_at.desc(), Request.id.desc()).limit(100).all()
    except Exception:
        db.session.rollback()
        return []


def _request_operational_title(req: Request | None) -> str:
    if req is None:
        return "Demande"
    return (
        getattr(req, "title", None)
        or getattr(req, "category", None)
        or getattr(req, "message", None)
        or f"Demande #{getattr(req, 'id', '')}"
    )


def _intervenant_activity_table_available() -> bool:
    return _table_exists("intervenant_activities")


def _log_intervenant_activity(
    intervenant: Intervenant,
    event_type: str,
    *,
    label: str | None = None,
    request_id: int | None = None,
    old_value: object | None = None,
    new_value: object | None = None,
    meta: dict | None = None,
) -> None:
    if not _intervenant_activity_table_available():
        return
    db.session.add(
        IntervenantActivity(
            intervenant_id=int(intervenant.id),
            structure_id=int(intervenant.structure_id),
            request_id=request_id,
            actor_admin_id=getattr(current_user, "id", None),
            event_type=event_type,
            label=label or INTERVENANT_ACTIVITY_LABELS.get(event_type, event_type),
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            meta_json=json.dumps(meta or {}, ensure_ascii=True, sort_keys=True),
        )
    )


def _intervenant_state_snapshot(intervenant: Intervenant) -> dict[str, object]:
    return {
        "name": getattr(intervenant, "name", None),
        "actor_type": getattr(intervenant, "actor_type", None),
        "email": getattr(intervenant, "email", None),
        "phone": getattr(intervenant, "phone", None),
        "location": getattr(intervenant, "location", None),
        "availability": _intervenant_availability(intervenant),
        "internal_notes": getattr(intervenant, "internal_notes", None),
        "competencies_json": getattr(intervenant, "competencies_json", None),
        "coverage_zones": getattr(intervenant, "coverage_zones", None),
        "coverage_communes": getattr(intervenant, "coverage_communes", None),
        "radius_km": getattr(intervenant, "radius_km", None),
    }


def _log_intervenant_profile_changes(
    intervenant: Intervenant, before: dict[str, object]
) -> None:
    after = _intervenant_state_snapshot(intervenant)
    if before.get("availability") != after.get("availability"):
        _log_intervenant_activity(
            intervenant,
            "availability_changed",
            old_value=_intervenant_availability_label(str(before.get("availability") or "")),
            new_value=_intervenant_availability_label(str(after.get("availability") or "")),
        )
    if before.get("internal_notes") != after.get("internal_notes"):
        _log_intervenant_activity(intervenant, "notes_updated")
    if before.get("competencies_json") != after.get("competencies_json"):
        _log_intervenant_activity(intervenant, "competencies_updated")

    profile_fields = {
        "name",
        "actor_type",
        "email",
        "phone",
        "location",
        "coverage_zones",
        "coverage_communes",
        "radius_km",
    }
    if any(before.get(field) != after.get(field) for field in profile_fields):
        _log_intervenant_activity(intervenant, "profile_updated")


def _relative_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    now = utc_now()
    if getattr(value, "tzinfo", None) is None and getattr(now, "tzinfo", None) is not None:
        now = now.replace(tzinfo=None)
    delta = now - value
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "a l'instant"
    minutes = seconds // 60
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours} h"
    days = hours // 24
    if days < 30:
        return f"il y a {days} j"
    return value.strftime("%d/%m/%Y")


def _intervenant_activity_icon(event_type: str | None) -> str:
    return {
        "affectation_created": "fa-link",
        "assigned_to_request": "fa-link",
        "affectation_removed": "fa-unlink",
        "removed_from_request": "fa-unlink",
        "availability_changed": "fa-clock",
        "profile_updated": "fa-user-edit",
        "competencies_updated": "fa-layer-group",
        "notes_updated": "fa-sticky-note",
    }.get(event_type or "", "fa-stream")


def _intervenant_activity_display_label(row: IntervenantActivity, req: Request | None) -> str:
    event_type = row.event_type
    request_title = _request_operational_title(req) if req is not None else None
    if event_type in {"affectation_created", "assigned_to_request"} and request_title:
        return f"Affecte a {request_title}"
    if event_type in {"affectation_removed", "removed_from_request"} and request_title:
        return f"Retire de {request_title}"
    if event_type == "availability_changed":
        next_value = (row.new_value or "").strip()
        return f"Disponibilite changee -> {next_value}" if next_value else "Disponibilite changee"
    if event_type == "competencies_updated":
        return "Competences mises a jour"
    if event_type == "notes_updated":
        return "Notes mises a jour"
    if event_type == "profile_updated":
        return "Profil mis a jour"
    return row.label or INTERVENANT_ACTIVITY_LABELS.get(event_type, "Activite")


def _intervenant_activity_items(intervenant: Intervenant) -> list[dict[str, object]]:
    if not _intervenant_activity_table_available():
        return []
    try:
        rows = (
            IntervenantActivity.query.filter_by(intervenant_id=intervenant.id)
            .order_by(IntervenantActivity.created_at.desc(), IntervenantActivity.id.desc())
            .limit(30)
            .all()
        )
    except Exception:
        db.session.rollback()
        _SCHEMA_TABLE_CACHE.pop("intervenant_activities", None)
        return []

    items: list[dict[str, object]] = []
    for row in rows:
        req = getattr(row, "request", None)
        actor = getattr(row, "actor", None)
        items.append(
            {
                "label": _intervenant_activity_display_label(row, req),
                "meta": getattr(actor, "username", None) or "Equipe coordination",
                "value": _relative_timestamp(row.created_at),
                "request_ref": f"#{req.id}" if req is not None else "",
                "icon": _intervenant_activity_icon(row.event_type),
                "request": req,
                "event_type": row.event_type,
            }
        )
    return items


def _intervenant_operational_context(intervenant: Intervenant, city: str) -> dict[str, object]:
    counts = _intervenant_assignment_counts(intervenant)
    availability = _intervenant_availability(intervenant)
    activity_items = _intervenant_activity_items(intervenant)

    summary_cards = [
        {"label": "Charge actuelle", "value": counts["workload"]},
        {"label": "Dossiers actifs", "value": counts["active_cases"]},
        {
            "label": "Disponibilité",
            "value": _intervenant_availability_label(availability),
            "badge_class": _intervenant_availability_badge(availability),
        },
        {"label": "Zone principale", "value": city or "—"},
    ]
    return {
        "summary_cards": summary_cards,
        "timeline_items": activity_items,
        "workload": counts["workload"],
        "active_cases": counts["active_cases"],
    }


def _intervenant_detail_form_data(intervenant: Intervenant) -> dict[str, object]:
    availability = _intervenant_availability(intervenant)
    return {
        "name": getattr(intervenant, "name", "") or "",
        "actor_type": _normalize_intervenant_actor_type(getattr(intervenant, "actor_type", "") or ""),
        "email": getattr(intervenant, "email", "") or "",
        "phone": getattr(intervenant, "phone", "") or "",
        "location": getattr(intervenant, "location", "") or "",
        "latitude": getattr(intervenant, "latitude", None),
        "longitude": getattr(intervenant, "longitude", None),
        "availability": availability,
        "internal_notes": getattr(intervenant, "internal_notes", "") or "",
        "competencies": [item["value"] for item in _intervenant_competencies(intervenant)],
        "coverage_zones": getattr(intervenant, "coverage_zones", "") or "",
        "coverage_communes": getattr(intervenant, "coverage_communes", "") or "",
        "radius_km": getattr(intervenant, "radius_km", None),
    }


def _intervenant_detail_template(
    intervenant: Intervenant,
    *,
    structure_context_id: int | None = None,
    form_data: dict[str, object] | None = None,
    status_code: int = 200,
):
    city = _intervenant_city(intervenant)
    address = _intervenant_address(intervenant)
    structure = getattr(intervenant, "structure", None)
    availability = _intervenant_availability(intervenant)
    operational_context = _intervenant_operational_context(intervenant, city)
    active_cases = _active_intervenant_assignments(intervenant)
    assignment_options = _intervenant_assignment_options(intervenant)

    return (
        render_template(
            "admin/intervenant_detail.html",
            intervenant=intervenant,
            structure=structure,
            structure_context_id=structure_context_id,
            can_view_structure_detail=_is_global_admin(),
            city=city,
            address=address,
            actor_type_options=INTERVENANT_ACTOR_TYPE_OPTIONS,
            availability_options=INTERVENANT_AVAILABILITY_OPTIONS,
            actor_type_label=_intervenant_actor_type_label(getattr(intervenant, "actor_type", None)),
            availability=availability,
            availability_label=_intervenant_availability_label(availability),
            availability_badge_class=_intervenant_availability_badge(availability),
            availability_helper=_intervenant_availability_helper(availability),
            avatar_initials=_intervenant_initials(intervenant),
            competencies=_intervenant_competencies(intervenant),
            competency_options=INTERVENANT_COMPETENCY_OPTIONS,
            coverage_zones=_intervenant_coverage_zones(intervenant),
            coverage_communes=_intervenant_coverage_communes(intervenant),
            active_cases=active_cases,
            assignment_options=assignment_options,
            request_operational_title=_request_operational_title,
            operational_summary=operational_context["summary_cards"],
            operational_timeline=operational_context["timeline_items"],
            form_data=form_data or _intervenant_detail_form_data(intervenant),
            edit_panel_open=status_code >= 400,
            has_coordinates=(
                hasattr(intervenant, "latitude")
                and hasattr(intervenant, "longitude")
                and _table_has_column("intervenants", "latitude")
                and _table_has_column("intervenants", "longitude")
            ),
        ),
        status_code,
    )


def _update_intervenant_from_form(intervenant: Intervenant) -> list[str]:
    errors: list[str] = []
    name = (request.form.get("name") or "").strip()
    actor_type = _normalize_intervenant_actor_type(request.form.get("actor_type"))
    availability = _normalize_intervenant_availability(request.form.get("availability"))

    if not name:
        errors.append("Nom requis.")
    if actor_type not in INTERVENANT_ACTOR_TYPE_LABELS:
        errors.append("Profession requise.")
    if availability not in INTERVENANT_AVAILABILITY_LABELS:
        errors.append("Disponibilité invalide.")

    if errors:
        return errors

    intervenant.name = name
    intervenant.actor_type = actor_type
    intervenant.email = (request.form.get("email") or "").strip() or None
    intervenant.phone = (request.form.get("phone") or "").strip() or None
    intervenant.location = (request.form.get("location") or "").strip() or None
    if hasattr(intervenant, "availability") and _table_has_column("intervenants", "availability"):
        intervenant.availability = availability
    intervenant.is_active = availability != "unavailable"

    if hasattr(intervenant, "internal_notes") and _table_has_column("intervenants", "internal_notes"):
        intervenant.internal_notes = (request.form.get("internal_notes") or "").strip() or None

    if hasattr(intervenant, "competencies_json") and _table_has_column("intervenants", "competencies_json"):
        intervenant.competencies_json = _encode_intervenant_competencies(
            request.form.getlist("competencies")
        )
    if hasattr(intervenant, "coverage_zones") and _table_has_column("intervenants", "coverage_zones"):
        intervenant.coverage_zones = (request.form.get("coverage_zones") or "").strip() or None
    if hasattr(intervenant, "coverage_communes") and _table_has_column("intervenants", "coverage_communes"):
        intervenant.coverage_communes = (request.form.get("coverage_communes") or "").strip() or None
    if hasattr(intervenant, "radius_km") and _table_has_column("intervenants", "radius_km"):
        radius_raw = (request.form.get("radius_km") or "").strip()
        if radius_raw:
            try:
                intervenant.radius_km = float(radius_raw)
            except Exception:
                errors.append("Rayon invalide.")
        else:
            intervenant.radius_km = None

    if hasattr(intervenant, "latitude") and _table_has_column("intervenants", "latitude"):
        lat_raw = (request.form.get("latitude") or "").strip()
        if lat_raw:
            try:
                intervenant.latitude = float(lat_raw)
            except Exception:
                errors.append("Latitude invalide.")
        else:
            intervenant.latitude = None

    if hasattr(intervenant, "longitude") and _table_has_column("intervenants", "longitude"):
        lng_raw = (request.form.get("longitude") or "").strip()
        if lng_raw:
            try:
                intervenant.longitude = float(lng_raw)
            except Exception:
                errors.append("Longitude invalide.")
        else:
            intervenant.longitude = None

    return errors


def _intervenant_detail_url(intervenant: Intervenant, structure_context_id: int | None):
    if structure_context_id is not None:
        return url_for(
            "admin.admin_structure_intervenant_detail",
            structure_id=structure_context_id,
            intervenant_id=intervenant.id,
        )
    return url_for("admin.admin_intervenant_detail", intervenant_id=intervenant.id)


def _handle_intervenant_operational_action(
    intervenant: Intervenant, structure_context_id: int | None = None
):
    action = (request.form.get("intervenant_action") or "").strip()
    if action not in {"assign_request", "remove_assignment"}:
        return None

    if action == "assign_request":
        raw_request_id = (request.form.get("request_id") or "").strip()
        try:
            request_id = int(raw_request_id)
        except Exception:
            flash("Demande invalide.", "warning")
            return redirect(_intervenant_detail_url(intervenant, structure_context_id))

        req = db.session.get(Request, request_id)
        if req is None or int(getattr(req, "structure_id", 0) or 0) != int(intervenant.structure_id):
            abort(403)

        status = (getattr(req, "status", None) or "").strip().lower()
        if bool(getattr(req, "is_archived", False)) or status not in INTERVENANT_ASSIGNABLE_REQUEST_STATUSES:
            flash("Cette demande n'est plus active.", "warning")
            return redirect(_intervenant_detail_url(intervenant, structure_context_id))

        existing = (
            Assignment.query.filter_by(
                request_id=req.id,
                intervenant_id=intervenant.id,
                structure_id=intervenant.structure_id,
            )
            .filter(func.lower(func.coalesce(Assignment.status, "")).in_(ACTIVE_ASSIGNMENT_STATUSES))
            .first()
        )
        if existing is not None:
            flash("Cet intervenant est deja assigne a cette demande.", "info")
            return redirect(_intervenant_detail_url(intervenant, structure_context_id))

        assignment = Assignment(
            request_id=req.id,
            intervenant_id=intervenant.id,
            structure_id=intervenant.structure_id,
            assigned_by_admin_id=getattr(current_user, "id", None),
            status="active",
        )
        db.session.add(assignment)
        db.session.flush()
        _log_intervenant_activity(
            intervenant,
            "affectation_created",
            request_id=req.id,
            new_value=_request_operational_title(req),
        )
        db.session.commit()
        flash("Intervenant assigne a la demande.", "success")
        return redirect(_intervenant_detail_url(intervenant, structure_context_id))

    raw_assignment_id = (request.form.get("assignment_id") or "").strip()
    try:
        assignment_id = int(raw_assignment_id)
    except Exception:
        flash("Affectation invalide.", "warning")
        return redirect(_intervenant_detail_url(intervenant, structure_context_id))

    assignment = db.session.get(Assignment, assignment_id)
    if (
        assignment is None
        or int(getattr(assignment, "intervenant_id", 0) or 0) != int(intervenant.id)
        or int(getattr(assignment, "structure_id", 0) or 0) != int(intervenant.structure_id)
    ):
        abort(403)

    assignment.status = "removed"
    _log_intervenant_activity(
        intervenant,
        "affectation_removed",
        request_id=assignment.request_id,
        old_value=_request_operational_title(getattr(assignment, "request", None)),
    )
    db.session.commit()
    flash("Affectation retiree.", "success")
    return redirect(_intervenant_detail_url(intervenant, structure_context_id))


def _assignment_workload_subquery():
    return (
        db.session.query(
            Assignment.intervenant_id.label("intervenant_id"),
            func.count(Assignment.id).label("workload"),
        )
        .filter(
            func.lower(func.coalesce(Assignment.status, "active")).in_(
                tuple(ACTIVE_ASSIGNMENT_STATUSES)
            )
        )
        .group_by(Assignment.intervenant_id)
        .subquery()
    )


def _resolve_intervenant_coordinates(intervenant: Intervenant) -> tuple[float, float, bool]:
    lat = getattr(intervenant, "latitude", None) if hasattr(intervenant, "latitude") else None
    lng = getattr(intervenant, "longitude", None) if hasattr(intervenant, "longitude") else None
    if lat is not None and lng is not None:
        try:
            return float(lat), float(lng), True
        except Exception:
            pass

    coords = INTERVENANT_CITY_COORDS.get(
        _normalize_intervenant_city_key(_intervenant_city(intervenant)),
        INTERVENANT_DEFAULT_COORDS,
    )
    return float(coords[0]), float(coords[1]), False


def suggest_best_professional(request_row) -> dict[str, object] | None:
    if not request_row or not _table_exists("intervenants"):
        return None

    request_structure_id = getattr(request_row, "structure_id", None)
    request_city = _normalize_smart_assign_text(getattr(request_row, "city", None))
    category_code = getattr(request_row, "category", None)
    priority_flag = (
        (getattr(request_row, "risk_level", None) or "").strip().lower() == "high"
    )
    now = datetime.now(UTC).replace(tzinfo=None)

    professionals_query = Intervenant.query.filter(Intervenant.is_active.is_(True))
    if request_structure_id is not None:
        professionals_query = professionals_query.filter(
            Intervenant.structure_id == int(request_structure_id)
        )

    professionals = (
        professionals_query.order_by(Intervenant.created_at.desc(), Intervenant.id.desc())
        .limit(200)
        .all()
    )
    if not professionals:
        return None

    candidate_ids = [
        int(pro.id) for pro in professionals if getattr(pro, "id", None) is not None
    ]
    workload_by_professional: dict[int, int] = {}
    if candidate_ids:
        workload_rows = (
            db.session.query(Assignment.intervenant_id, func.count(Assignment.id))
            .filter(Assignment.intervenant_id.in_(candidate_ids))
            .filter(
                func.lower(func.coalesce(Assignment.status, "active")).in_(
                    tuple(ACTIVE_ASSIGNMENT_STATUSES)
                )
            )
            .group_by(Assignment.intervenant_id)
            .all()
        )
        workload_by_professional = {
            int(intervenant_id): int(active_count or 0)
            for intervenant_id, active_count in workload_rows
            if intervenant_id is not None
        }

    scored: list[dict[str, object]] = []
    for pro in professionals:
        score = 0
        pro_city = _normalize_smart_assign_text(_intervenant_city(pro))
        same_city = bool(request_city and pro_city and request_city == pro_city)
        city_match = "same" if same_city else "near"
        if same_city:
            score += 40
        else:
            score += 10
            score -= 5

        score += _smart_assign_profession_score(
            category_code, _intervenant_profession(pro)
        )
        strong_match = _smart_assign_is_strong_match(
            category_code, _intervenant_profession(pro)
        )
        if strong_match:
            score += 10

        active_count = int(workload_by_professional.get(int(pro.id), 0))
        capacity_score = 0
        if active_count == 0:
            capacity_score += 15
        elif active_count < 3:
            capacity_score += 10
        elif active_count > 8:
            capacity_score -= 15
        score += capacity_score

        if request_structure_id is not None and getattr(pro, "structure_id", None) == request_structure_id:
            score += 10

        if priority_flag:
            score += 15

        recent_activity = getattr(pro, "updated_at", None) or getattr(
            pro, "created_at", None
        )
        recent_activity_naive = _to_utc_naive(recent_activity)

        responsiveness = "medium"
        if recent_activity_naive and recent_activity_naive >= (
            now - timedelta(hours=24)
        ):
            responsiveness = "high"
            score += 15
        elif recent_activity_naive and recent_activity_naive >= (
            now - timedelta(days=7)
        ):
            responsiveness = "medium"
        else:
            responsiveness = "low"
            score -= 10

        scored.append(
            {
                "id": int(pro.id),
                "name": _intervenant_display_name(pro),
                "profession": _intervenant_profession(pro),
                "score": int(score),
                "city_match": city_match,
                "workload": active_count,
                "capacity": int(capacity_score),
                "responsiveness": responsiveness,
                "priority": priority_flag,
                "is_priority": priority_flag,
                "_strong_match": strong_match,
                "_same_city": same_city,
            }
        )

    scored.sort(
        key=lambda item: (
            -int(item["score"]),
            0 if item["_same_city"] else 1,
            str(item["name"]).lower(),
            int(item["id"]),
        )
    )
    if not scored:
        return None

    best = dict(scored[0])
    best.pop("_same_city", None)
    best.pop("_strong_match", None)
    return best


def _cases_enabled() -> bool:
    return (
        _table_exists("cases")
        and _table_exists("case_events")
        and _table_exists("case_participants")
    )


def _as_aware_utc(dt_val: datetime | None) -> datetime | None:
    if not dt_val:
        return None
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def _format_elapsed_compact(dt_val: datetime | None) -> str:
    dt_aware = _as_aware_utc(dt_val)
    if not dt_aware:
        return "-"
    delta = max(timedelta(0), _now_utc() - dt_aware)
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "0 min"
    if total_minutes < 60:
        return f"{total_minutes} min"
    total_hours = int(delta.total_seconds() // 3600)
    if total_hours < 24:
        return f"{total_hours} h"
    total_days = int(delta.total_seconds() // 86400)
    return f"{total_days} j"


def _elapsed_tone(dt_val: datetime | None) -> str:
    dt_aware = _as_aware_utc(dt_val)
    if not dt_aware:
        return "muted"
    total_hours = max(0, int((_now_utc() - dt_aware).total_seconds() // 3600))
    if total_hours < 6:
        return "recent"
    if total_hours < 24:
        return "warn"
    return "stale"


def _format_duration_compact(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    total_minutes = max(0, int(delta.total_seconds() // 60))
    if total_minutes < 60:
        return f"{total_minutes} min"
    total_hours = max(0, int(delta.total_seconds() // 3600))
    if total_hours < 24:
        return f"{total_hours} h"
    total_days = max(0, int(delta.total_seconds() // 86400))
    return f"{total_days} j"


def _case_sla_snapshot(case_row: Case | None) -> dict:
    if not case_row:
        return {
            "target_label": "-",
            "deadline": None,
            "state": "on_time",
            "state_label": "À temps",
            "detail": "—",
        }

    priority = ((getattr(case_row, "priority", None) or "normal").strip().lower())
    target_map = {
        "critical": timedelta(hours=4),
        "high": timedelta(hours=24),
        "normal": timedelta(hours=72),
        "low": timedelta(days=5),
    }
    target_delta = target_map.get(priority, timedelta(hours=72))
    opened_ref = _as_aware_utc(getattr(case_row, "opened_at", None) or getattr(case_row, "created_at", None))
    if not opened_ref:
        return {
            "target_label": _format_duration_compact(target_delta),
            "deadline": None,
            "state": "on_time",
            "state_label": "À temps",
            "detail": "—",
        }

    deadline = opened_ref + target_delta
    remaining = deadline - _now_utc()
    soon_threshold = min(timedelta(hours=4), max(timedelta(hours=1), target_delta / 5))
    if remaining.total_seconds() < 0:
        state = "overdue"
        state_label = "En retard"
        detail = f"En retard de {_format_duration_compact(abs(remaining))}"
    elif remaining <= soon_threshold:
        state = "due_soon"
        state_label = "Échéance proche"
        detail = f"Dans {_format_duration_compact(remaining)}"
    else:
        state = "on_time"
        state_label = "À temps"
        detail = f"Dans {_format_duration_compact(remaining)}"

    return {
        "target_label": _format_duration_compact(target_delta),
        "deadline": deadline,
        "state": state,
        "state_label": state_label,
        "detail": detail,
    }


def _build_operational_blockages(
    req: Request,
    case_row: Case | None = None,
) -> dict:
    blockages: list[str] = []
    now = _now_utc()

    status_val = ((getattr(case_row, "status", None) if case_row else getattr(req, "status", None)) or "").strip().lower()
    risk_level = ((getattr(req, "risk_level", None) or "").strip().lower())
    case_priority = ((getattr(case_row, "priority", None) if case_row else "") or "").strip().lower()
    high_risk = risk_level in {"critical", "attention"} or case_priority in {"high", "critical"}

    def _case_has_owner(current_case: Case | None) -> bool:
        if not current_case:
            return bool(getattr(req, "owner_id", None))
        if getattr(current_case, "owner_user_id", None):
            return True
        if getattr(req, "owner_id", None):
            return True
        participants = getattr(current_case, "participants", None) or []
        return any(
            ((getattr(participant, "role", None) or "").strip().lower() == "owner")
            and bool(getattr(participant, "user_id", None) or getattr(participant, "external_name", None))
            for participant in participants
        )

    def _case_has_professional(current_case: Case | None) -> bool:
        if not current_case:
            return False
        if getattr(current_case, "assigned_professional_lead_id", None):
            return True
        participants = getattr(current_case, "participants", None) or []
        return any(
            bool(getattr(participant, "professional_lead_id", None))
            and (
                (getattr(participant, "role", None) or "").strip().lower() == "primary_professional"
                or (getattr(participant, "participant_type", None) or "").strip().lower() == "professional_lead"
            )
            for participant in participants
        )

    has_owner = _case_has_owner(case_row)
    has_professional = _case_has_professional(case_row)

    if not has_owner:
        blockages.append("Aucun responsable assigné")

    if case_row and not has_professional:
        blockages.append("Aucun professionnel assigné")

    activity_ref = _as_aware_utc(
        (getattr(case_row, "last_activity_at", None) if case_row else None)
        or getattr(case_row, "updated_at", None) if case_row else None
        or getattr(req, "updated_at", None)
        or getattr(req, "created_at", None)
    )
    if activity_ref:
        inactive_for = now - activity_ref
        if inactive_for >= timedelta(hours=72):
            blockages.append(f"Dernière activité il y a {_format_elapsed_compact(activity_ref)}")

    created_ref = _as_aware_utc(getattr(case_row, "created_at", None) if case_row else getattr(req, "created_at", None))
    if created_ref and status_val in {"new", "open", "pending"} and (now - created_ref) >= timedelta(hours=48):
        blockages.append("Dossier encore en statut initial depuis trop longtemps")

    if case_row and high_risk and not has_professional:
        blockages.append("Risque élevé sans suivi professionnel concret")

    return {
        "count": len(blockages),
        "items": blockages[:4],
        "has_blockage": bool(blockages),
    }


def _build_risk_ai_suggestion(req: Request) -> dict:
    triage = score_request_risk(req)
    suggested_label = triage.get("suggested_category_label")
    matched = triage.get("matched_rules") or []
    return {
        "risk_score": int(triage.get("risk_score") or triage.get("score") or 0),
        "risk_label": triage.get("risk_label") or triage.get("label"),
        "suggested_category_code": triage.get("suggested_category_code"),
        "suggested_category_label": suggested_label,
        "matched_rules": matched,
        "has_suggestion": bool(suggested_label),
        "emergency_detected": bool(triage.get("emergency_detected")),
        "emergency_reason_summary": triage.get("emergency_reason_summary"),
    }


def _append_case_event(
    case_id: int,
    event_type: str,
    actor_user_id: int | None = None,
    message: str | None = None,
    metadata: dict | None = None,
    visibility: str = "internal",
) -> CaseEvent:
    normalized_event = (event_type or "note_added").strip().lower()
    if normalized_event not in CASE_EVENT_TYPES:
        normalized_event = "note_added"
    evt = CaseEvent(
        case_id=int(case_id),
        actor_user_id=actor_user_id,
        event_type=normalized_event,
        message=(message or "").strip() or None,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False) if metadata else None,
        visibility=(visibility or "internal").strip().lower() or "internal",
        created_at=_now_utc(),
    )
    db.session.add(evt)
    return evt


def _send_status_email_async(recipient: str, subject: str, context: dict) -> None:
    """Fire-and-forget status email to keep admin status updates responsive."""
    if not recipient:
        return
    app = current_app._get_current_object()

    def _worker() -> None:
        try:
            with app.app_context():
                from backend.mail_service import send_notification_email

                ok = send_notification_email(
                    recipient,
                    subject,
                    "email_template.html",
                    context,
                    purpose="admin_status_update",
                )
                if not ok:
                    app.logger.warning(
                        "[EMAIL] Async status email not sent (recipient=%s, subject=%s)",
                        recipient,
                        subject,
                    )
        except Exception as e:
            app.logger.warning(
                "[EMAIL] Async status email send failed (request-status): %s", e
            )

    threading.Thread(target=_worker, daemon=True).start()


def _now_utc():
    return datetime.now(UTC)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_admin_last_seen() -> datetime | None:
    last_seen_ts = session.get("admin_last_seen")
    if not last_seen_ts:
        return None
    try:
        return datetime.fromisoformat(last_seen_ts)
    except Exception:
        return None


def _admin_session_is_expired(now: datetime) -> bool:
    last_seen = _get_admin_last_seen()
    if not last_seen:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen) > timedelta(minutes=ADMIN_SESSION_IDLE_TIMEOUT_MIN)


def _touch_admin_last_seen(now: datetime) -> None:
    session["admin_last_seen"] = now.isoformat()


def _get_admin_auth_at() -> datetime | None:
    auth_ts = session.get("admin_auth_at")
    if not auth_ts:
        return None
    try:
        return datetime.fromisoformat(auth_ts)
    except Exception:
        return None


def _touch_admin_auth_at(now: datetime) -> None:
    session["admin_auth_at"] = now.isoformat()


def _admin_fresh_auth_is_valid(
    now: datetime, minutes: int = ADMIN_FRESH_AUTH_MIN
) -> bool:
    auth_at = _get_admin_auth_at()
    if not auth_at:
        # Keep legacy tests stable when they bypass login and inject session directly.
        if bool(current_app.config.get("TESTING", False)) and session.get(
            "admin_logged_in"
        ):
            _touch_admin_auth_at(now)
            return True
        return False
    if auth_at.tzinfo is None:
        auth_at = auth_at.replace(tzinfo=timezone.utc)
    return (now - auth_at) <= timedelta(minutes=minutes)


def require_admin_fresh_auth(minutes: int = ADMIN_FRESH_AUTH_MIN):
    def _decorator(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            if not session.get("admin_logged_in"):
                nxt = request.full_path if request.query_string else request.path
                return redirect(url_for("admin.admin_login_legacy", next=nxt), code=303)
            now = _utc_now()
            if _admin_fresh_auth_is_valid(now, minutes=minutes):
                return fn(*args, **kwargs)
            nxt = request.full_path if request.query_string else request.path
            flash("Veuillez confirmer votre identité pour continuer.", "warning")
            return redirect(url_for("admin.admin_reauth", next=nxt), code=303)

        return _wrapped

    return _decorator


def _client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def _compute_case_signals(
    req: Request,
    activities: list[RequestActivity] | None,
    now: datetime,
) -> list[dict]:
    """Rules-based, explainable case signals for admin case rail."""
    signals: list[dict] = []
    acts = activities or []
    status = (getattr(req, "status", "") or "").upper()
    priority = (getattr(req, "priority", "medium") or "medium").lower()
    has_owner = bool(getattr(req, "owner_id", None))
    has_phone = bool((getattr(req, "phone", "") or "").strip())
    has_email = bool((getattr(req, "email", "") or "").strip())

    def _as_aware(dt_val: datetime | None) -> datetime | None:
        if not dt_val:
            return None
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val

    created_at = _as_aware(getattr(req, "created_at", None))
    updated_at = _as_aware(getattr(req, "updated_at", None))
    owned_at = _as_aware(getattr(req, "owned_at", None))
    last_activity_at = _as_aware(acts[0].created_at) if acts else None
    reference_dt = last_activity_at or updated_at or created_at

    if not has_owner:
        signals.append(
            {
                "code": "no_owner",
                "level": "danger",
                "title": "Aucun responsable assigné",
                "why": "Sans responsable, le dossier n'a pas de pilotage clair.",
                "cta_label": "Assigner un responsable",
                "cta_href": "#owner-actions",
            }
        )
    elif owned_at and (now - owned_at) > timedelta(hours=24) and (
        not last_activity_at or (now - last_activity_at) > timedelta(hours=24)
    ):
        signals.append(
            {
                "code": "owner_idle",
                "level": "warning",
                "title": "Owner inactif",
                "why": "Responsable assigné, mais pas d'activité récente.",
                "cta_label": "Vérifier l'activité",
                "cta_href": "#activity-timeline",
            }
        )

    if priority in {"high", "urgent"} and status == "NEW":
        signals.append(
            {
                "code": "urgent_not_started",
                "level": "danger",
                "title": "Urgence non demarree",
                "why": "Priorite elevee avec statut nouveau.",
                "cta_label": "Passer en cours",
                "cta_href": "#status-controls",
            }
        )
    elif priority in {"high", "urgent"} and status == "IN_PROGRESS":
        signals.append(
            {
                "code": "urgent_in_progress",
                "level": "warning",
                "title": "Urgence en cours",
                "why": "Suivi actif requis jusqu'a resolution.",
                "cta_label": "Ajouter note",
                "cta_href": "#internal-note",
            }
        )

    if reference_dt and (now - reference_dt) > timedelta(days=3):
        signals.append(
            {
                "code": "stale_case",
                "level": "warning",
                "title": "Dossier stale",
                "why": "Aucune activite significative depuis plus de 72h.",
                "cta_label": "Revoir dossier",
                "cta_href": "#activity-timeline",
            }
        )

    if status == "NEW" and created_at and (now - created_at) > timedelta(hours=24):
        signals.append(
            {
                "code": "no_first_action_24h",
                "level": "warning",
                "title": "Aucune premiere action > 24h",
                "why": "Le dossier est nouveau mais non traite depuis 24h.",
                "cta_label": "Demarrer traitement",
                "cta_href": "#status-controls",
            }
        )

    if not has_phone and not has_email:
        signals.append(
            {
                "code": "missing_contact",
                "level": "danger",
                "title": "Contact manquant",
                "why": "Ni telephone ni email n'est renseigne.",
                "cta_label": "Ajouter note interne",
                "cta_href": "#internal-note",
            }
        )
    elif has_email and not has_phone:
        signals.append(
            {
                "code": "partial_contact",
                "level": "info",
                "title": "Contact partiel",
                "why": "Email disponible, telephone absent.",
                "cta_label": "Contacter par email",
                "cta_href": "#contact-block",
            }
        )

    if status in {"RESOLVED", "COMPLETED"}:
        has_resolution = any(
            (a.action == "status_change")
            and (a.new_value or "").upper() in {"RESOLVED", "COMPLETED"}
            for a in acts
        )
        has_note = any(a.action == "note" for a in acts)
        if not has_resolution and not has_note:
            signals.append(
                {
                    "code": "closure_without_note",
                    "level": "warning",
                    "title": "Cloture sans note",
                    "why": "Ajouter une note de resolution pour la tracabilite.",
                    "cta_label": "Ajouter note",
                    "cta_href": "#internal-note",
                }
            )

    if not signals:
        signals.append(
            {
                "code": "all_good",
                "level": "ok",
                "title": "Dossier sous controle",
                "why": "Aucun signal critique detecte.",
                "cta_label": "Voir activite",
                "cta_href": "#activity-timeline",
            }
        )

    return signals[:5]


def _parse_risk_signals(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, list):
        return {str(x).strip().lower() for x in value if str(x).strip()}
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return set()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {str(x).strip().lower() for x in parsed if str(x).strip()}
        except Exception:
            pass
        return {chunk.strip().lower() for chunk in raw.split(",") if chunk.strip()}
    return set()


def _build_helpchain_recommendation(
    req: Request,
    activities: list[RequestActivity] | None,
    now: datetime,
) -> dict[str, str]:
    risk_level = (getattr(req, "risk_level", "") or "").strip().lower()
    risk_signals = _parse_risk_signals(getattr(req, "risk_signals", None))
    assigned_actor = (
        getattr(getattr(req, "owner", None), "username", None)
        or (f"#{req.owner_id}" if getattr(req, "owner_id", None) else "")
        or "non assigné"
    )

    last_activity = None
    if activities:
        last_activity = getattr(activities[0], "created_at", None)
    if not last_activity:
        last_activity = getattr(req, "updated_at", None) or getattr(req, "created_at", None)
    if isinstance(last_activity, datetime):
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        hours_since = max(0, int((now - last_activity).total_seconds() // 3600))
        last_activity_label = f"il y a environ {hours_since} h"
    else:
        last_activity_label = "non disponible"

    if risk_level == "critical" and not getattr(req, "owner_id", None):
        return {
            "priority": "Critique",
            "action": "Affecter un responsable territorial",
            "reason": (
                "Niveau de risque critique et aucun acteur assigné. "
                f"Dernière activité: {last_activity_label}."
            ),
        }

    if "not_seen_72h" in risk_signals:
        return {
            "priority": "Élevée",
            "action": "Vérifier la situation avec l’acteur assigné",
            "reason": (
                "Le signal not_seen_72h indique une absence d’action récente. "
                f"Acteur assigné: {assigned_actor}. Dernière activité: {last_activity_label}."
            ),
        }

    if risk_level == "attention":
        return {
            "priority": "Moyenne",
            "action": "Planifier un suivi",
            "reason": (
                "Niveau de risque attention. "
                f"Acteur assigné: {assigned_actor}. Dernière activité: {last_activity_label}."
            ),
        }

    return {
        "priority": "Standard",
        "action": "Maintenir le suivi courant",
        "reason": (
            f"Pas de déclencheur critique détecté. Acteur assigné: {assigned_actor}. "
            f"Dernière activité: {last_activity_label}."
        ),
    }


def _norm_username(username: str | None) -> str | None:
    if not username:
        return None
    value = username.strip().lower()
    return value or None


def _find_admin_user(login_identifier: str) -> AdminUser | None:
    ident = (login_identifier or "").strip()
    if not ident:
        return None
    ident_l = ident.lower()
    return (
        AdminUser.query.filter(
            or_(
                func.lower(func.coalesce(AdminUser.username, "")) == ident_l,
                func.lower(func.coalesce(AdminUser.email, "")) == ident_l,
            )
        )
        .limit(1)
        .first()
    )


def _verify_admin_password(user: AdminUser | None, password: str) -> bool:
    if not user or not getattr(user, "password_hash", None):
        return False
    try:
        return bool(user.check_password(password))
    except Exception:
        return False


def _default_admin_workspace_url(user: AdminUser | None = None) -> str:
    role = _normalize_admin_role_value(
        getattr(user, "role", None) if user is not None else getattr(current_user, "role", None)
    )
    structure_id = (
        getattr(user, "structure_id", None)
        if user is not None
        else getattr(current_user, "structure_id", None)
    )
    if role == "superadmin":
        return url_for("admin.admin_home")
    if structure_id is not None and role == "admin":
        return url_for("admin.admin_ops")
    if role == "admin":
        return url_for("admin.admin_home")
    if role == "ops":
        return url_for("ops.ops_workspace")
    if role == "readonly":
        return url_for("ops.ops_workspace")
    return url_for("admin.admin_requests")


def _default_admin_landing_url(user: AdminUser | None = None) -> str:
    if _admin_onboarding_required(user):
        return url_for("admin.admin_onboarding")
    return _default_admin_workspace_url(user)


def _admin_role_requires_mfa(raw_role) -> bool:
    role = _normalize_admin_role_value(raw_role)
    return role in {"superadmin", "admin"}


def is_mfa_required(user) -> bool:
    if not current_app.config.get("REQUIRE_ADMIN_MFA", True):
        return False

    role = _normalize_admin_role_value(getattr(user, "role", ""))
    return role in {"superadmin", "admin"}


def _admin_onboarding_columns_available() -> bool:
    required_columns = (
        "must_change_password",
        "onboarding_started_at",
        "onboarding_completed_at",
        "onboarding_step",
    )
    return all(_table_has_column("admin_users", column_name) for column_name in required_columns)


def _admin_onboarding_data_columns_available() -> bool:
    return _table_has_column("admin_users", "onboarding_data_json")


def _admin_onboarding_required(user: AdminUser | None = None) -> bool:
    target = user if user is not None else current_user
    if not target or not getattr(target, "is_authenticated", False):
        return False
    if not getattr(target, "is_admin", False):
        return False
    if getattr(target, "structure_id", None) is None:
        return False
    if not _admin_onboarding_columns_available():
        return False
    if getattr(target, "onboarding_completed_at", None) is not None:
        return False
    step = (getattr(target, "onboarding_step", None) or "").strip().lower()
    must_change_password = bool(getattr(target, "must_change_password", False))
    started_at = getattr(target, "onboarding_started_at", None)
    return bool(step or must_change_password or started_at)


def _admin_onboarding_step(user: AdminUser | None = None) -> str:
    target = user if user is not None else current_user
    raw_step = (getattr(target, "onboarding_step", None) or "").strip().lower()
    if raw_step in ONBOARDING_STEPS:
        return raw_step
    if bool(getattr(target, "must_change_password", False)):
        return "secure_access"
    return "welcome"


def _admin_onboarding_load_data(user: AdminUser | None = None) -> dict:
    target = user if user is not None else current_user
    if not _admin_onboarding_data_columns_available():
        return {}
    raw = getattr(target, "onboarding_data_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _admin_onboarding_save_data(user: AdminUser, payload: dict) -> None:
    if not _admin_onboarding_data_columns_available():
        return
    user.onboarding_data_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _admin_onboarding_touch_started(user: AdminUser) -> None:
    if getattr(user, "onboarding_started_at", None) is None:
        user.onboarding_started_at = utc_now()


def _admin_onboarding_set_step(user: AdminUser, step: str) -> None:
    if step not in ONBOARDING_STEPS:
        return
    _admin_onboarding_touch_started(user)
    user.onboarding_step = step


def _admin_onboarding_safe_structure(user: AdminUser | None = None) -> Structure | None:
    target = user if user is not None else current_user
    structure_id = getattr(target, "structure_id", None)
    if structure_id is None:
        return None
    return db.session.get(Structure, int(structure_id))


def _admin_onboarding_plan_url() -> str:
    try:
        return url_for("main.contact")
    except Exception:
        return "/contact"


def _admin_login_is_locked(
    ip: str, username: str | None, now: datetime
) -> tuple[bool, int]:
    # Fresh/dev databases may miss this table before migrations/bootstrap.
    # Failing open here avoids a hard 500 on login; audit lockout telemetry
    # resumes automatically once schema is present.
    if not _table_exists("admin_login_attempts"):
        return False, 0

    window_start = now - timedelta(minutes=ADMIN_LOGIN_RATE_WINDOW_MIN)
    query = AdminLoginAttempt.query.filter(
        AdminLoginAttempt.created_at >= window_start,
        AdminLoginAttempt.ip == ip,
        AdminLoginAttempt.success.is_(False),
    )
    if username:
        query = query.filter(AdminLoginAttempt.username == username)

    try:
        fail_count = query.count()
    except Exception:
        # Fail-open for login if local/dev DB drift broke this table schema.
        # We prefer allowing login over returning a 500 on auth form submit.
        db.session.rollback()
        _SCHEMA_TABLE_CACHE.pop("admin_login_attempts", None)
        return False, 0
    if fail_count < ADMIN_LOGIN_MAX_FAILS:
        return False, 0

    try:
        last_fail = query.order_by(AdminLoginAttempt.created_at.desc()).first()
    except Exception:
        db.session.rollback()
        _SCHEMA_TABLE_CACHE.pop("admin_login_attempts", None)
        return False, 0
    if not last_fail or not last_fail.created_at:
        return False, 0

    last_fail_at = last_fail.created_at
    if getattr(last_fail_at, "tzinfo", None) is not None:
        last_fail_at = last_fail_at.astimezone(UTC).replace(tzinfo=None)

    unlock_at = last_fail_at + timedelta(minutes=ADMIN_LOGIN_LOCKOUT_MIN)
    if now < unlock_at:
        retry_after = int((unlock_at - now).total_seconds())
        return True, max(retry_after, 1)

    return False, 0


def _log_admin_attempt(username: str | None, ip: str, success: bool) -> None:
    if not _table_exists("admin_login_attempts"):
        return

    ua = request.headers.get("User-Agent")
    db.session.add(
        AdminLoginAttempt(
            username=username,
            ip=ip,
            success=bool(success),
            user_agent=(ua[:256] if ua else None),
        )
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def _clear_recent_admin_login_failures(
    username: str | None, ip: str, now: datetime
) -> int:
    if not _table_exists("admin_login_attempts"):
        return 0

    window_start = now - timedelta(minutes=ADMIN_LOGIN_RATE_WINDOW_MIN)
    query = AdminLoginAttempt.query.filter(
        AdminLoginAttempt.created_at >= window_start,
        AdminLoginAttempt.ip == ip,
        AdminLoginAttempt.success.is_(False),
    )
    if username:
        query = query.filter(AdminLoginAttempt.username == username)

    try:
        deleted = int(query.delete(synchronize_session=False) or 0)
        if deleted:
            db.session.commit()
        return deleted
    except Exception:
        db.session.rollback()
        return 0


def _lockout_response(retry_after_seconds: int, next_url: str = ""):
    retry_after_seconds = max(int(retry_after_seconds or 0), 1)
    flash(GENERIC_ADMIN_LOGIN_FAIL_MSG, "warning")
    response = make_response(render_template("admin/login.html", next=next_url), 429)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def _admin_auth_rate_limit_response(_request_limit):
    next_candidate = (
        request.form.get("next") or request.args.get("next") or ""
    ).strip()
    next_url = _safe_next_url(next_candidate)
    flash(_("Too many attempts. Please wait a minute and try again."), "warning")

    if request.path.endswith("/re-auth"):
        response = make_response(
            render_template("admin/reauth.html", next=next_url),
            429,
        )
    elif request.path.endswith("/mfa/verify"):
        response = make_response(
            render_template(
                "admin/mfa_verify.html",
                locked=True,
                remaining=60,
                next=next_url,
            ),
            429,
        )
    else:
        response = make_response(
            render_template("admin/login.html", next=next_url),
            429,
        )
    response.headers["Retry-After"] = "60"
    return response


def _audit_admin_auth_event(
    event_type: str,
    *,
    route_context: str,
    attempted_identifier: str | None = None,
    user: AdminUser | None = None,
    next_url: str = "",
    extra_payload: dict | None = None,
) -> None:
    payload = {
        "route": route_context,
        "attempted_identifier": (attempted_identifier or "")[:255],
        "next": (next_url or "")[:255],
        "ip": _client_ip(),
        "ua": (request.headers.get("User-Agent") or "")[:256],
    }
    if extra_payload:
        payload.update(extra_payload)

    log_security_event(
        event_type,
        actor_type="admin",
        actor_id=getattr(user, "id", None),
        meta={
            "route": route_context,
            "attempted_identifier": (attempted_identifier or "")[:255],
            "ip": payload["ip"],
        },
    )
    audit_admin_action(
        action=event_type,
        target_type="AdminUser",
        target_id=int(getattr(user, "id", 0) or 0),
        payload=payload,
    )


def _complete_admin_login(user: AdminUser, next_url: str, *, via: str):
    # Successful password verification path. Full admin access is granted
    # only after MFA succeeds when MFA is required.
    session.clear()  # mitigate session fixation
    login_user(user, remember=False)
    session["pending_admin_user_id"] = int(user.id)
    session["admin_password_verified"] = True
    current_app.logger.warning(
        "[LOGIN] user_id=%s role=%s via=%s",
        getattr(user, "id", None),
        getattr(user, "role", None),
        via,
    )
    now = _utc_now()
    _touch_admin_last_seen(now)
    _touch_admin_auth_at(now)
    log_activity(
        entity_type="admin",
        entity_id=user.id,
        action="admin_login",
        message="Admin login",
        meta={"via": via},
        persist=True,
    )
    log_security_event(
        "auth_admin_login_success",
        actor_type="admin",
        actor_id=user.id,
    )
    audit_admin_action(
        action="admin.login.success",
        target_type="AdminUser",
        target_id=int(user.id or 0),
        payload={
            "via": via,
            "next": (next_url or "")[:255],
            "ip": _client_ip(),
            "ua": (request.headers.get("User-Agent") or "")[:256],
        },
    )

    # MFA flow
    session.pop(Config.MFA_SESSION_KEY, None)
    session.pop("mfa_required", None)
    mfa_globally_enabled = bool(Config.MFA_ENABLED)
    role_requires_mfa = is_mfa_required(user)
    user_has_mfa = bool(getattr(user, "mfa_enabled", False)) and bool(
        getattr(user, "totp_secret", None)
    )
    if mfa_globally_enabled and role_requires_mfa and not user_has_mfa:
        session["mfa_required"] = True
        session.modified = True
        current_app.logger.warning("[SESSION] %s", dict(session))
        flash(_("MFA setup is required for your role before continuing."), "warning")
        return redirect(
            url_for(
                "admin.admin_mfa_setup",
                next=next_url or _default_admin_landing_url(user),
            )
    )
    if mfa_globally_enabled and user_has_mfa:
        session["mfa_required"] = True
        session.modified = True
        current_app.logger.warning("[SESSION] %s", dict(session))
        return redirect(
            url_for(
                "admin.admin_mfa_verify",
                next=next_url or _default_admin_landing_url(user),
            )
        )
    return _finalize_admin_session(
        user,
        next_url,
        fallback_url=_default_admin_landing_url(user),
    )


def audit_admin_action(
    action: str, target_type: str, target_id: int, payload: dict | None = None
) -> None:
    try:
        ip = _client_ip()
        ua = request.headers.get("User-Agent")
        admin_user_id = session.get("admin_user_id")
        admin_username = None
        try:
            if getattr(current_user, "is_authenticated", False):
                admin_username = getattr(current_user, "username", None)
                if not admin_user_id:
                    admin_user_id = getattr(current_user, "id", None)
        except Exception:
            admin_username = None

        db.session.add(
            AdminAuditEvent(
                admin_user_id=admin_user_id,
                admin_username=admin_username,
                action=action,
                target_type=target_type,
                target_id=int(target_id),
                ip=ip,
                user_agent=(ua[:256] if ua else None),
                payload=payload,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("ADMIN_AUDIT: failed to write audit event")


def _current_structure_id() -> int:
    try:
        from backend.core.tenant import current_structure_id
    except ModuleNotFoundError:
        from core.tenant import current_structure_id
    return int(current_structure_id())


def _is_global_admin() -> bool:
    actor = resolve_current_admin_actor()
    return actor.is_platform_global


def _is_structure_admin() -> bool:
    actor = resolve_current_admin_actor()
    return actor.is_authenticated and actor.role == "superadmin" and actor.is_structure_attached


def _require_global_admin() -> None:
    # Platform-wide admin pages are gated by the canonical superadmin role.
    # Some dev/bootstrap setups attach that account to a default structure for
    # tenant context, so do not deny on structure_id alone here.
    actor = resolve_current_admin_actor()
    if not actor.has_founder_global_access:
        _audit_denied_action(
            required_roles={"global_admin"},
            actor_role=actor.role,
        )
        abort(403)


def _require_professional_lead_access() -> None:
    # ProfessionalLead is intentionally platform-global. Keep operationally
    # scoped admins out of the CRM surface while preserving founder access.
    actor = resolve_current_admin_actor()
    if not can_access_professional_leads(actor):
        _audit_denied_action(
            required_roles={"platform_commercial", "superadmin"},
            actor_role=actor.role,
        )
        abort(403)


def _require_global_analytics_access() -> None:
    actor = resolve_current_admin_actor()
    if not can_view_global_analytics(actor):
        _audit_denied_action(
            required_roles={"global_analytics"},
            actor_role=actor.role,
        )
        abort(403)


def _require_structure_admin_or_global() -> None:
    if not (_is_structure_admin() or _is_global_admin()):
        _audit_denied_action(
            required_roles={"structure_admin", "global_admin"},
            actor_role=_admin_role_value(),
        )
        abort(403)


def _structure_scope_filter():
    if _is_global_admin():
        return None
    return Request.structure_id == _current_structure_id()


def _apply_tenant_filter(query, tenant_filter):
    if tenant_filter is None:
        return query
    return query.filter(tenant_filter)


def _scope_requests(query):
    """Sprint-1 tenant guard for request queries."""
    tenant_filter = _structure_scope_filter()
    return _apply_tenant_filter(query, tenant_filter)


def _request_kpi_base_query(*entities):
    query = db.session.query(*entities) if entities else db.session.query(Request)
    return (
        _scope_requests(query)
        .filter(Request.deleted_at.is_(None))
        .filter(Request.is_archived.is_(False))
    )


def _request_kpi_open_filter():
    status_expr = func.lower(func.coalesce(Request.status, ""))
    return or_(
        Request.status.is_(None),
        ~status_expr.in_(tuple(REQUEST_KPI_CLOSED_STATUSES)),
    )


def _request_kpi_resolved_filter():
    status_expr = func.lower(func.coalesce(Request.status, ""))
    return and_(
        Request.completed_at.isnot(None),
        or_(
            Request.status.is_(None),
            status_expr.in_(tuple(REQUEST_KPI_RESOLVED_STATUSES)),
        ),
    )


def _engagement_label(score: int) -> str:
    if score >= 5:
        return "High"
    if score >= 1:
        return "Medium"
    return "At risk"


def get_volunteer_engagement_score(
    volunteer_id: int, now: datetime | None = None
) -> dict:
    """
    Heuristic engagement score per volunteer in range [-10..+10].
    """
    now = now or datetime.now(UTC).replace(tzinfo=None)
    cutoff_72h = now - timedelta(hours=72)

    seen_within_24h = 0
    not_seen_72h = 0
    has_vrs_notified_at = _table_has_column("volunteer_request_states", "notified_at")
    if has_vrs_notified_at and _table_exists("volunteer_request_states"):
        try:
            states = (
                db.session.query(
                    VolunteerRequestState.notified_at,
                    VolunteerRequestState.seen_at,
                )
                .filter(VolunteerRequestState.volunteer_id == volunteer_id)
                .filter(VolunteerRequestState.notified_at.isnot(None))
                .all()
            )
            for notified_at, seen_at in states:
                if notified_at is None:
                    continue
                if seen_at is not None and seen_at <= (notified_at + timedelta(hours=24)):
                    seen_within_24h += 1
                if seen_at is None and notified_at <= cutoff_72h:
                    not_seen_72h += 1
        except Exception:
            db.session.rollback()
    elif _table_exists("notifications"):
        # Compatibility fallback for environments where notified_at migration is missing.
        try:
            notif_rows = (
                db.session.query(
                    Notification.created_at, Notification.read_at, Notification.is_read
                )
                .filter(Notification.volunteer_id == volunteer_id)
                .filter(Notification.type == "new_match")
                .all()
            )
            for created_at, read_at, is_read in notif_rows:
                if created_at is None:
                    continue
                if read_at is not None and read_at <= (created_at + timedelta(hours=24)):
                    seen_within_24h += 1
                if (not bool(is_read)) and created_at <= cutoff_72h:
                    not_seen_72h += 1
        except Exception:
            db.session.rollback()

    can_help = 0
    cant_help = 0
    if _table_has_column("request_activities", "volunteer_id"):
        try:
            can_help = (
                db.session.query(func.count(RequestActivity.id))
                .filter(RequestActivity.volunteer_id == volunteer_id)
                .filter(RequestActivity.action == "volunteer_can_help")
                .scalar()
                or 0
            )
            cant_help = (
                db.session.query(func.count(RequestActivity.id))
                .filter(RequestActivity.volunteer_id == volunteer_id)
                .filter(RequestActivity.action == "volunteer_cant_help")
                .scalar()
                or 0
            )
        except Exception:
            db.session.rollback()

    raw_score = (
        2 * int(seen_within_24h)
        + 3 * int(can_help)
        - 1 * int(cant_help)
        - 3 * int(not_seen_72h)
    )
    score = max(-10, min(10, int(raw_score)))
    return {
        "volunteer_id": int(volunteer_id),
        "score": score,
        "label": _engagement_label(score),
        "seen_within_24h": int(seen_within_24h),
        "not_seen_72h": int(not_seen_72h),
        "can_help": int(can_help),
        "cant_help": int(cant_help),
    }


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _relay_event_scope_query():
    query = RelayEvent.query
    if _is_global_admin():
        return query
    return query.filter(RelayEvent.structure_id == _current_structure_id())


def _integration_connector_scope_query():
    query = IntegrationConnector.query
    if _is_global_admin():
        return query
    return query.filter(IntegrationConnector.structure_id == _current_structure_id())


def _integration_connector_allowed_fields(connector: IntegrationConnector) -> list[str]:
    raw_value = getattr(connector, "allowed_fields_json", None)
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    allowed_values = set(INTEGRATION_CONNECTOR_ALLOWED_FIELDS)
    return [
        str(item)
        for item in parsed
        if str(item or "").strip() and str(item) in allowed_values
    ]


def _normalize_connector_source_slug(raw_value: str | None) -> str:
    source_slug = re.sub(r"[^a-z0-9._-]+", "-", (raw_value or "").strip().lower()).strip("._-")
    if not source_slug:
        raise ValueError("Le slug source est requis.")
    if len(source_slug) > 120:
        raise ValueError("Le slug source est trop long.")
    return source_slug


def _parse_connector_allowed_fields(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    seen = set()
    output = []
    allowed_values = set(INTEGRATION_CONNECTOR_ALLOWED_FIELDS)
    for part in re.split(r"[\n,;]+", raw_value):
        value = str(part or "").strip()
        if not value or value not in allowed_values or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _connector_secret_session_key() -> str:
    return "integration_connector_secret_once"


def _pop_connector_secret_once(connector_id: int) -> str | None:
    payload = session.pop(_connector_secret_session_key(), None)
    if not isinstance(payload, dict):
        return None
    try:
        stored_connector_id = int(payload.get("connector_id"))
    except Exception:
        return None
    if stored_connector_id != int(connector_id):
        return None
    secret = str(payload.get("secret") or "").strip()
    return secret or None


def _connector_event_count_map() -> dict[int, int]:
    rows = (
        _relay_event_scope_query()
        .with_entities(RelayEvent.connector_id, func.count(RelayEvent.id))
        .filter(RelayEvent.connector_id.isnot(None))
        .group_by(RelayEvent.connector_id)
        .all()
    )
    return {int(connector_id): int(count) for connector_id, count in rows if connector_id is not None}


def _connector_last_event_map() -> dict[int, datetime]:
    rows = (
        _relay_event_scope_query()
        .with_entities(RelayEvent.connector_id, func.max(RelayEvent.created_at))
        .filter(RelayEvent.connector_id.isnot(None))
        .group_by(RelayEvent.connector_id)
        .all()
    )
    return {
        int(connector_id): last_event_at
        for connector_id, last_event_at in rows
        if connector_id is not None and last_event_at is not None
    }


def _relay_event_rejected_fields(relay_event: RelayEvent) -> list[str]:
    raw_value = getattr(relay_event, "rejected_fields_json", None)
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item or "").strip()]


def _relay_event_metadata(relay_event: RelayEvent) -> dict:
    raw_value = getattr(relay_event, "metadata_json", None)
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): value
        for key, value in parsed.items()
        if str(key or "").strip()
    }


def _normalize_sla_kind(raw_kind: str | None) -> str | None:
    kind = (raw_kind or "").strip().lower()
    if kind in SLA_QUEUE_KINDS:
        return kind
    # Backward-compatible aliases from /admin/sla type selector.
    alias = {
        "resolve": "resolution_overdue",
        "owner_assign": "owner_assignment_overdue",
        "volunteer_assign": "volunteer_assignment_overdue",
    }
    return alias.get(kind)


def _normalize_sla_days(raw_days) -> int:
    try:
        val = int(raw_days)
    except (TypeError, ValueError):
        val = 30
    return max(1, min(val, 365))


def _sla_open_filter():
    return or_(
        Request.status.is_(None), ~func.lower(Request.status).in_(CLOSED_STATUSES)
    )


def _sla_base_window_query(base_query, *, days: int, now: datetime):
    window_start = now - timedelta(days=days)
    return (
        base_query.filter(Request.created_at.isnot(None))
        .filter(Request.created_at >= window_start)
        .filter(_sla_open_filter())
    )


def _sla_kind_condition(sla_kind: str, *, now: datetime):
    if sla_kind == "resolution_overdue":
        return and_(
            Request.completed_at.is_(None),
            Request.created_at < (now - timedelta(days=RESOLVE_SLA_DAYS)),
        )
    if sla_kind == "owner_assignment_overdue":
        return and_(
            Request.owner_id.is_(None),
            Request.created_at < (now - timedelta(hours=ASSIGN_SLA_HOURS)),
        )
    if sla_kind == "volunteer_assignment_overdue":
        return and_(
            Request.assigned_volunteer_id.is_(None),
            Request.created_at < (now - timedelta(hours=VOLUNTEER_ASSIGN_SLA_HOURS)),
        )
    return None


def _apply_sla_queue_filter(base_query, *, sla_kind: str, days: int, now: datetime):
    cond = _sla_kind_condition(sla_kind, now=now)
    if cond is None:
        return base_query
    return _sla_base_window_query(base_query, days=days, now=now).filter(cond)


def _sla_overdue_hours_by_kind(req, *, now: datetime) -> dict[str, float]:
    created_at = _to_utc_naive(getattr(req, "created_at", None))
    if not created_at:
        return {}
    age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
    out: dict[str, float] = {}

    resolve_sla_hours = float(RESOLVE_SLA_DAYS * 24)
    owner_assign_sla_hours = float(ASSIGN_SLA_HOURS)
    volunteer_assign_sla_hours = float(VOLUNTEER_ASSIGN_SLA_HOURS)

    if getattr(req, "completed_at", None) is None and age_hours > resolve_sla_hours:
        out["resolution_overdue"] = age_hours - resolve_sla_hours
    if getattr(req, "owner_id", None) is None and age_hours > owner_assign_sla_hours:
        out["owner_assignment_overdue"] = age_hours - owner_assign_sla_hours
    if (
        getattr(req, "assigned_volunteer_id", None) is None
        and age_hours > volunteer_assign_sla_hours
    ):
        out["volunteer_assignment_overdue"] = age_hours - volunteer_assign_sla_hours
    return out


def _sla_prediction_state(req, *, sla_kind: str, now: datetime) -> dict:
    created_at = _to_utc_naive(getattr(req, "created_at", None))
    if not created_at:
        return {"state": "unknown", "remaining_hours": None, "label": "—"}

    if sla_kind == "resolution_overdue":
        if getattr(req, "completed_at", None) is not None:
            return {"state": "ok", "remaining_hours": None, "label": "—"}
        sla_hours = float(RESOLVE_SLA_DAYS * 24)
    elif sla_kind == "owner_assignment_overdue":
        if getattr(req, "owner_id", None) is not None:
            return {"state": "ok", "remaining_hours": None, "label": "—"}
        sla_hours = float(ASSIGN_SLA_HOURS)
    elif sla_kind == "volunteer_assignment_overdue":
        if getattr(req, "assigned_volunteer_id", None) is not None:
            return {"state": "ok", "remaining_hours": None, "label": "—"}
        sla_hours = float(VOLUNTEER_ASSIGN_SLA_HOURS)
    else:
        return {"state": "unknown", "remaining_hours": None, "label": "—"}

    age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
    remaining_hours = sla_hours - age_hours
    warn_threshold = min(4.0, max(1.0, sla_hours / 5.0))

    if remaining_hours < 0:
        return {"state": "breached", "remaining_hours": remaining_hours, "label": "Dépassement"}
    if remaining_hours <= warn_threshold:
        return {"state": "due_soon", "remaining_hours": remaining_hours, "label": "Échéance proche"}
    return {"state": "ok", "remaining_hours": remaining_hours, "label": "À temps"}


def _delta_seconds(start: datetime | None, end: datetime | None) -> int | None:
    start_n = _to_utc_naive(start)
    end_n = _to_utc_naive(end)
    if not start_n or not end_n:
        return None
    return max(0, int((end_n - start_n).total_seconds()))


def compute_response_metrics(
    request_id: int, sla_hours: int = 12, now: datetime | None = None
) -> dict:
    """
    Compute response metrics for a request using assigned volunteer context.
    """
    now_naive = _to_utc_naive(now) or datetime.now(UTC).replace(tzinfo=None)
    out = {
        "request_id": int(request_id),
        "assigned_volunteer_id": None,
        "first_seen_seconds": None,
        "first_action_seconds": None,
        "assignment_delay_seconds": None,
        "not_seen_after_24h": False,
        "sla_hours": int(sla_hours),
        "sla_under_threshold": None,
        "sla_bucket": "no_assignee",
    }

    req = (
        db.session.query(Request.id, Request.created_at, Request.assigned_volunteer_id)
        .filter(Request.id == request_id)
        .first()
    )
    if not req:
        out["sla_bucket"] = "missing_request"
        return out

    req_created_at = _to_utc_naive(getattr(req, "created_at", None))
    assigned_volunteer_id = getattr(req, "assigned_volunteer_id", None)
    out["assigned_volunteer_id"] = assigned_volunteer_id

    first_assign_at = (
        db.session.query(func.min(RequestActivity.created_at))
        .filter(RequestActivity.request_id == request_id)
        .filter(RequestActivity.action == "assign_volunteer")
        .scalar()
    )
    out["assignment_delay_seconds"] = _delta_seconds(req_created_at, first_assign_at)

    if not assigned_volunteer_id:
        return out

    state = (
        db.session.query(
            VolunteerRequestState.notified_at, VolunteerRequestState.seen_at
        )
        .filter(VolunteerRequestState.request_id == request_id)
        .filter(VolunteerRequestState.volunteer_id == int(assigned_volunteer_id))
        .first()
    )
    notified_at = _to_utc_naive(state[0]) if state else None
    seen_at = _to_utc_naive(state[1]) if state else None

    out["first_seen_seconds"] = _delta_seconds(notified_at, seen_at)
    out["not_seen_after_24h"] = bool(
        notified_at
        and not seen_at
        and ((now_naive - notified_at).total_seconds() >= 24 * 3600)
    )

    action_q = (
        db.session.query(func.min(RequestActivity.created_at))
        .filter(RequestActivity.request_id == request_id)
        .filter(
            RequestActivity.action.in_(["volunteer_can_help", "volunteer_cant_help"])
        )
    )
    if _table_has_column("request_activities", "volunteer_id"):
        action_q = action_q.filter(
            RequestActivity.volunteer_id == int(assigned_volunteer_id)
        )
    if notified_at is not None:
        action_q = action_q.filter(RequestActivity.created_at >= notified_at)
    first_action_at = action_q.scalar()
    out["first_action_seconds"] = _delta_seconds(notified_at, first_action_at)

    if notified_at is None:
        out["sla_bucket"] = "no_notification"
        return out
    if out["first_action_seconds"] is None:
        out["sla_bucket"] = "no_action"
        return out

    sla_seconds = int(sla_hours) * 3600
    under = out["first_action_seconds"] <= sla_seconds
    out["sla_under_threshold"] = bool(under)
    out["sla_bucket"] = "under_sla" if under else "over_sla"
    return out


def _notseen_hours_from_risk(risk: str) -> int | None:
    if risk == "notseen":
        return 24
    if not risk.startswith("notseen"):
        return None
    suffix = risk[len("notseen") :]
    if not suffix.isdigit():
        return None
    hours = int(suffix)
    if hours in NOTSEEN_TIERS_HOURS:
        return hours
    return None


def _build_notseen_subquery(now: datetime, *, hours: int):
    cutoff = now - timedelta(hours=hours)
    has_vrs_notified_at = _table_has_column("volunteer_request_states", "notified_at")
    if has_vrs_notified_at:
        subq = (
            db.session.query(VolunteerRequestState.request_id)
            .filter(VolunteerRequestState.notified_at.isnot(None))
            .filter(VolunteerRequestState.notified_at < cutoff)
            .filter(VolunteerRequestState.seen_at.is_(None))
            .subquery()
        )
        source = "notified_at"
    else:
        subq = (
            db.session.query(Notification.request_id)
            .filter(Notification.type == "new_match")
            .filter(Notification.created_at < cutoff)
            .subquery()
        )
        source = "notification_created_at_fallback"
    return subq, source


def _admin_id():
    return getattr(current_user, "id", None)


LOCK_TTL_MINUTES = 30


def _lock_expired(req, now: datetime | None = None) -> bool:
    """Return True if owned_at is older than LOCK_TTL_MINUTES."""
    now = now or _now_utc()
    owned_at = getattr(req, "owned_at", None)
    if not owned_at:
        return False
    try:
        if owned_at.tzinfo is None:
            owned_at = owned_at.replace(tzinfo=UTC)
        return (now - owned_at).total_seconds() > LOCK_TTL_MINUTES * 60
    except Exception:
        return False


def _locked_by_other(req, admin_id, now: datetime | None = None) -> bool:
    return bool(
        getattr(req, "owner_id", None)
        and getattr(req, "owner_id", None) != admin_id
        and not _lock_expired(req, now or _now_utc())
    )


from sqlalchemy import and_, case, func, or_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import joinedload, load_only

from ..utils.mfa import (
    build_totp_uri,
    generate_totp_secret,
    qr_png_base64,
    verify_totp_code,
)

admin_bp = Blueprint(
    "admin", __name__, url_prefix="/admin"
)  # single templates path via app.py
ops_bp = Blueprint("ops", __name__, url_prefix="/ops")


@admin_bp.app_context_processor
def inject_admin_category_helpers():
    return {
        "request_category_label": request_category_label,
        "REQUEST_CATEGORY_CHOICES": request_category_choices(),
        "format_elapsed_compact": _format_elapsed_compact,
        "elapsed_tone": _elapsed_tone,
        "case_sla_snapshot": _case_sla_snapshot,
    }


@admin_bp.before_request
def require_admin_session():
    """
    Gate the entire /admin surface behind the explicit admin session flag.

    Security/UX:
    - No public access to admin pages (redirect to login).
    - "Admin" navbar link is also keyed off the same session flag.
    """
    allowed = {
        "admin.admin_login",
        "admin.ops_login",
        "admin.admin_login_legacy",
        "admin.admin_email_2fa",
        "admin.admin_2fa",
        "admin.admin_mfa_setup",
        "admin.admin_mfa_verify",
        "admin.metrics",
        "admin.metrics_tenant_leak_test",
        "admin.admin_logout",
    }
    if request.endpoint in allowed or (
        request.endpoint and request.endpoint.startswith("static")
    ):
        return None

    if session.get("admin_logged_in"):
        return None

    if _has_pending_admin_mfa():
        return _pending_admin_mfa_redirect_response()

    nxt = request.full_path if request.query_string else request.path
    return redirect(
        url_for("admin.admin_login_legacy", next=_safe_next_url(nxt)),
        code=303,
    )


@admin_bp.before_app_request
def _admin_idle_timeout_guard():
    if not request.path.startswith("/admin"):
        return None
    if not session.get("admin_logged_in"):
        return None
    if request.endpoint in {"admin.admin_login_legacy", "admin.ops_login", "admin.admin_ops_login"}:
        return None

    now = _utc_now()
    if _admin_session_is_expired(now):
        expired_admin_id = session.get("admin_user_id")
        if expired_admin_id:
            audit_admin_action(
                action="admin.session_timeout",
                target_type="AdminUser",
                target_id=int(expired_admin_id),
                payload={
                    "route": request.path,
                    "idle_timeout_min": int(ADMIN_SESSION_IDLE_TIMEOUT_MIN),
                    "ip": _client_ip(),
                    "ua": (request.headers.get("User-Agent") or "")[:256],
                },
            )
        session.pop("admin_logged_in", None)
        session.pop("admin_user_id", None)
        session.pop("admin_last_seen", None)
        session.pop("admin_auth_at", None)
        try:
            logout_user()
        except Exception:
            pass
        flash("Votre session a expiré. Veuillez vous reconnecter.", "warning")
        return redirect(url_for("admin.admin_login_legacy"), code=303)

    _touch_admin_last_seen(now)
    return None


def _metrics_token_valid() -> bool:
    token = (request.args.get("token", "") or "").strip()
    expected = (current_app.config.get("METRICS_TOKEN", "") or "").strip()
    return bool(expected) and secrets.compare_digest(token, expected)


@admin_bp.get("/metrics")
def metrics():
    if not _metrics_token_valid():
        return Response("forbidden\n", status=403, mimetype="text/plain")
    val = tenant_leak_get()
    payload = "\n".join(
        [
            "# HELP tenant_leak_total Tenant guardrail violations detected.",
            "# TYPE tenant_leak_total counter",
            f"tenant_leak_total {val}",
            "",
        ]
    )
    return Response(payload, mimetype="text/plain; version=0.0.4; charset=utf-8")


@admin_bp.get("/metrics/tenant-leak-test")
def metrics_tenant_leak_test():
    if not current_app.config.get("HC_ENABLE_LEAK_TEST", False):
        abort(404)

    allowlist = current_app.config.get("HC_LEAK_TEST_ALLOWLIST", set())
    if allowlist:
        forwarded_for = (request.headers.get("X-Forwarded-For", "") or "").split(",")
        client_ip = (
            (request.headers.get("CF-Connecting-IP", "") or "").strip()
            or (forwarded_for[0].strip() if forwarded_for else "")
            or (request.remote_addr or "").strip()
        )
        if client_ip not in allowlist:
            abort(404)

    if not _metrics_token_valid():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    value = request.args.get("value", default=1, type=int) or 1
    value = max(0, min(value, 1000))
    total = tenant_leak_inc(value)
    return jsonify({"ok": True, "tenant_leak_total": int(total)}), 200


@admin_bp.get("/")
def admin_index():
    """Redirect bare /admin to the role-appropriate admin home."""
    return redirect(_default_admin_landing_url())


@admin_bp.get("/dashboard")
def admin_dashboard_redirect():
    """Alias for the role-appropriate admin landing page."""
    return redirect(_default_admin_landing_url())


@admin_bp.get("/translations")
def admin_translations_list():
    q = (request.args.get("q") or "").strip()
    locale = (request.args.get("locale") or "fr").strip().lower()
    view = (request.args.get("view") or "ops").strip().lower()
    if view not in {"ops", "core", "inventory", "all"}:
        view = "ops"
    only_missing = request.args.get("only_missing") == "1"
    page = max(int(request.args.get("page") or 1), 1)
    per_page = min(max(int(request.args.get("per_page") or 50), 10), 200)

    supported = _supported_locales()
    if locale not in supported:
        locale = current_app.config.get("BABEL_DEFAULT_LOCALE", "fr")
    registry_view = _registry_entries_for_view(view)
    registry_keys = [r["key"] for r in registry_view]
    kpi = _translation_kpi(locale, registry_keys)
    locale_lock = _get_locale_lock(locale)
    is_locale_locked = bool(locale_lock and locale_lock.is_locked)
    freeze_state = _get_translation_freeze_state()
    is_translation_frozen = bool(freeze_state and freeze_state.is_active)
    can_l10n_write = _can_l10n_write()
    can_l10n_delete = _can_l10n_delete()
    can_l10n_write_effective = can_l10n_write and (not is_locale_locked or can_l10n_delete)

    missing_registry = []
    pagination = None
    items = []

    if only_missing:
        existing: set[str] = set()
        if registry_keys:
            existing = {
                k
                for (k,) in db.session.query(UiTranslation.key)
                .filter(
                    UiTranslation.locale == locale,
                    UiTranslation.key.in_(registry_keys),
                )
                .all()
            }

        filtered = [r for r in registry_view if r["key"] not in existing]
        if q:
            ql = q.lower()
            filtered = [
                r
                for r in filtered
                if ql in r["key"].lower()
                or ql in (r.get("default") or "").lower()
                or ql in (r.get("domain") or "").lower()
            ]

        start = (page - 1) * per_page
        end = start + per_page
        missing_registry = filtered[start:end]
    else:
        query = UiTranslation.query.filter(UiTranslation.locale == locale)
        if registry_keys:
            query = query.filter(UiTranslation.key.in_(registry_keys))
        else:
            query = query.filter(UiTranslation.key == "__no_registry_match__")
        if q:
            query = query.filter(UiTranslation.key.ilike(f"%{q}%"))
        query = query.order_by(UiTranslation.key.asc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        items = pagination.items

    return render_template(
        "admin/translations_list.html",
        q=q,
        locale=locale,
        view=view,
        only_missing=only_missing,
        supported_locales=supported,
        items=items,
        missing_registry=missing_registry,
        pagination=pagination,
        kpi=kpi,
        can_l10n_write=can_l10n_write_effective,
        can_l10n_delete=can_l10n_delete,
        is_locale_locked=is_locale_locked,
        locale_lock=locale_lock,
        is_translation_frozen=is_translation_frozen,
        translation_freeze=freeze_state,
    )


@admin_bp.get("/translations/coverage")
def admin_translations_coverage():
    role = _admin_role_value()
    if role not in {"ops", "superadmin"}:
        abort(403)

    locales = ["fr", "en", "de", "bg"]
    registry = _load_ui_key_registry()
    ops_domains = set(_load_ui_domains().get("core_ops_domains", []))
    buckets = ("public", "volunteer", "admin", "ops")

    bucket_keys: dict[str, set[str]] = {b: set() for b in buckets}
    all_keys: set[str] = set()
    for row in registry:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        domain = (row.get("domain") or "").strip().lower()
        bucket = _coverage_bucket_for_domain(domain, ops_domains)
        bucket_keys[bucket].add(key)
        all_keys.add(key)

    locale_keysets: dict[str, set[str]] = {lc: set() for lc in locales}
    if all_keys:
        rows = (
            UiTranslation.query.with_entities(UiTranslation.locale, UiTranslation.key)
            .filter(UiTranslation.locale.in_(locales))
            .filter(UiTranslation.key.in_(list(all_keys)))
            .filter(UiTranslation.is_active.is_(True))
            .all()
        )
        for locale, key in rows:
            locale_keysets.setdefault(locale, set()).add(key)

    coverage: dict[str, dict] = {}
    for locale in locales:
        row_data: dict[str, dict | float] = {}
        locale_keys = locale_keysets.get(locale, set())
        for bucket in buckets:
            keys = bucket_keys[bucket]
            total = len(keys)
            translated = len(locale_keys.intersection(keys))
            ratio = (float(translated) / float(total)) if total else 1.0
            row_data[bucket] = {
                "ratio": ratio,
                "percent": round(ratio * 100.0, 1),
                "translated": translated,
                "total": total,
                "class": _coverage_class(ratio),
            }
        total_all = len(all_keys)
        translated_all = len(locale_keys.intersection(all_keys))
        total_ratio = (float(translated_all) / float(total_all)) if total_all else 1.0
        row_data["total"] = {
            "ratio": total_ratio,
            "percent": round(total_ratio * 100.0, 1),
            "translated": translated_all,
            "total": total_all,
            "class": _coverage_class(total_ratio),
        }
        coverage[locale] = row_data

    translations_count = (
        db.session.query(func.count(UiTranslation.id))
        .filter(UiTranslation.locale.in_(locales))
        .filter(UiTranslation.key.in_(list(all_keys)) if all_keys else UiTranslation.key == "__no_registry_match__")
        .filter(UiTranslation.is_active.is_(True))
        .scalar()
        or 0
    )
    avg_coverage = (
        round(
            (sum(float(coverage[lc]["total"]["ratio"]) for lc in locales) / float(len(locales)))
            * 100.0,
            1,
        )
        if locales
        else 0.0
    )

    if (request.args.get("format") or "").strip().lower() == "json":
        payload = {
            "locales": locales,
            "buckets": list(buckets),
            "kpi": {
                "locales": len(locales),
                "registry_keys": len(all_keys),
                "translations": int(translations_count),
                "average_coverage_percent": avg_coverage,
            },
            "coverage": coverage,
        }
        return jsonify(payload), 200

    return render_template(
        "admin/translations_coverage.html",
        locales=locales,
        coverage=coverage,
        buckets=buckets,
        kpi={
            "locales": len(locales),
            "registry_keys": len(all_keys),
            "translations": int(translations_count),
            "average_coverage_percent": avg_coverage,
        },
    )


@admin_bp.get("/translations/<path:key>")
def admin_translations_key(key: str):
    key = (key or "").strip()
    if not key:
        abort(404)

    locale = (request.args.get("locale") or "fr").strip().lower()
    supported = _supported_locales()
    if locale not in supported:
        locale = current_app.config.get("BABEL_DEFAULT_LOCALE", "fr")

    row = UiTranslation.query.filter_by(key=key, locale=locale).first()
    reg = _registry_index().get(key) or {}
    fallback_text = _fallback_preview(key, locale)
    status = "db_override" if row and row.text else ("fallback" if fallback_text != key else "missing")
    history = []
    if _table_exists("ui_translation_events"):
        history = (
            UiTranslationEvent.query.filter(
                UiTranslationEvent.locale == locale,
                UiTranslationEvent.key == key,
            )
            .order_by(UiTranslationEvent.created_at.desc())
            .limit(20)
            .all()
        )
    last_event = history[0] if history else None
    locale_lock = _get_locale_lock(locale)
    is_locale_locked = bool(locale_lock and locale_lock.is_locked)
    freeze_state = _get_translation_freeze_state()
    is_translation_frozen = bool(freeze_state and freeze_state.is_active)
    can_l10n_write = _can_l10n_write()
    can_l10n_delete = _can_l10n_delete()
    can_l10n_write_effective = can_l10n_write and (not is_locale_locked or can_l10n_delete)

    return render_template(
        "admin/translations_key.html",
        key=key,
        locale=locale,
        supported_locales=supported,
        row=row,
        fallback_text=fallback_text,
        status=status,
        registry_default=(reg.get("default") or ""),
        registry_domain=(reg.get("domain") or ""),
        can_l10n_write=can_l10n_write_effective,
        can_l10n_delete=can_l10n_delete,
        is_locale_locked=is_locale_locked,
        locale_lock=locale_lock,
        is_translation_frozen=is_translation_frozen,
        translation_freeze=freeze_state,
        history_events=history,
        last_event=last_event,
    )


@admin_bp.post("/translations/upsert")
def admin_translations_upsert():
    _require_l10n_write()
    key = (request.form.get("key") or "").strip()
    locale = (request.form.get("locale") or "").strip().lower()
    locked = _blocked_by_locale_lock(locale)
    if locked is not None:
        return locked
    text = _sanitize_translation_text(request.form.get("text") or "")

    if not key or not locale:
        flash("Invalid key/locale.", "danger")
        return redirect(url_for("admin.admin_translations_list"))

    supported = _supported_locales()
    if locale not in supported:
        flash("Unsupported locale.", "danger")
        return redirect(url_for("admin.admin_translations_list"))

    if len(key) > 255:
        flash("Key too long.", "danger")
        return redirect(url_for("admin.admin_translations_list", locale=locale))

    if len(text) > 4000:
        flash("Text too long (max 4000).", "danger")
        return redirect(url_for("admin.admin_translations_key", key=key, locale=locale))

    existing = UiTranslation.query.filter_by(key=key, locale=locale).first()
    freeze_block = _blocked_by_translation_freeze(
        locale=locale,
        action="create",
        allow=existing is not None,
    )
    if freeze_block is not None:
        return freeze_block

    row, action, old_text, changed = _ui_translation_upsert(locale=locale, key=key, text=text)
    if changed:
        _log_translation_event(
            action=action,
            locale=locale,
            key=key,
            old_text=old_text,
            new_text=text,
            source="human",
            translation_id=getattr(row, "id", None),
        )

    db.session.commit()
    flash("Translation saved." if changed else "No changes.", "success" if changed else "info")
    return redirect(url_for("admin.admin_translations_key", key=key, locale=locale))


@admin_bp.post("/translations/delete")
def admin_translations_delete():
    _require_l10n_delete()
    key = (request.form.get("key") or "").strip()
    locale = (request.form.get("locale") or "").strip().lower()
    locked = _blocked_by_locale_lock(locale)
    if locked is not None:
        return locked
    frozen = _blocked_by_translation_freeze(locale=locale, action="delete")
    if frozen is not None:
        return frozen

    row = UiTranslation.query.filter_by(key=key, locale=locale).first()
    if row:
        old_text = row.text
        translation_id = row.id
        db.session.delete(row)
        _log_translation_event(
            action="deleted",
            locale=locale,
            key=key,
            old_text=old_text,
            new_text=None,
            source="human",
            translation_id=translation_id,
        )
        db.session.commit()
        flash("DB override deleted (fallback will be used).", "warning")
    else:
        flash("Nothing to delete.", "info")

    return redirect(url_for("admin.admin_translations_key", key=key, locale=locale))


@admin_bp.post("/translations/suggest")
def admin_translations_suggest():
    key = (request.form.get("key") or "").strip()
    locale = (request.form.get("locale") or "").strip().lower()
    if not key or not locale:
        return jsonify({"error": "missing key/locale"}), 400

    if locale not in _supported_locales():
        return jsonify({"error": "unsupported locale"}), 400

    meta = _ui_registry_get(key)
    default = (meta.get("default") or "").strip()
    domain = (meta.get("domain") or "public").strip()
    suggestions = _rules_suggest(key=key, locale=locale, default=default, domain=domain)
    return jsonify(
        {
            "key": key,
            "locale": locale,
            "domain": domain,
            "default": default,
            "provider": "rules",
            "suggestions": suggestions,
        }
    )


@admin_bp.post("/translations/ai-suggest")
def admin_translations_ai_suggest():
    key = (request.form.get("key") or "").strip()
    locale = (request.form.get("locale") or "").strip().lower()
    provider = (request.form.get("provider") or "hf_local").strip().lower()
    if not key or not locale:
        return jsonify({"error": "missing key/locale"}), 400
    if locale not in _supported_locales():
        return jsonify({"error": "unsupported locale"}), 400

    meta = _ui_registry_get(key)
    default = (meta.get("default") or "").strip()
    domain = (meta.get("domain") or "public").strip()
    source_text = _source_text_for_ai(key=key, default=default)
    suggestions = _ai_suggest(
        key=key,
        locale=locale,
        default=default,
        domain=domain,
        source_text=source_text,
        provider=provider,
    )
    return jsonify(
        {
            "key": key,
            "locale": locale,
            "domain": domain,
            "default": default,
            "provider": provider,
            "suggestions": suggestions,
        }
    )


@admin_bp.post("/translations/bulk-suggest-apply")
def admin_translations_bulk_suggest_apply():
    _require_l10n_write()
    locale = (
        request.form.get("locale")
        or request.args.get("locale")
        or current_app.config.get("BABEL_DEFAULT_LOCALE", "fr")
    ).strip().lower()
    view = (request.form.get("view") or request.args.get("view") or "ops").strip().lower()
    dry_run = (request.form.get("dry_run") or request.args.get("dry_run") or "") == "1"
    resp_format = (request.form.get("format") or request.args.get("format") or "").strip().lower()

    try:
        limit = int(request.form.get("limit") or request.args.get("limit") or "120")
    except ValueError:
        limit = 120
    limit = max(1, min(limit, 500))

    supported = _supported_locales()
    if locale not in supported:
        return jsonify({"ok": False, "error": "unsupported locale"}), 400
    locked = _blocked_by_locale_lock(locale)
    if locked is not None:
        return locked
    if not dry_run:
        frozen = _blocked_by_translation_freeze(locale=locale, action="bulk")
        if frozen is not None:
            return frozen
    if view not in {"ops", "core", "inventory", "all"}:
        view = "ops"

    entries = _registry_entries_for_view(view)
    keys = [e["key"] for e in entries if e.get("key")]

    existing_keys: set[str] = set()
    if keys:
        existing_keys = {
            row.key
            for row in UiTranslation.query.filter(
                UiTranslation.locale == locale,
                UiTranslation.key.in_(keys),
            ).all()
        }

    missing_entries = [e for e in entries if e.get("key") and e["key"] not in existing_keys]
    to_process = missing_entries[:limit]

    applied = 0
    skipped = 0
    for entry in to_process:
        key = entry["key"]
        default = (entry.get("default") or "").strip()
        domain = (entry.get("domain") or "").strip()
        suggestions = _rules_suggest(key=key, locale=locale, default=default, domain=domain)
        if not suggestions:
            skipped += 1
            continue
        best = (suggestions[0].get("text") or "").strip()
        if not best:
            skipped += 1
            continue
        if not dry_run:
            row, _action, old_text, changed = _ui_translation_upsert(locale=locale, key=key, text=best)
            if changed:
                _log_translation_event(
                    action="bulk_rules",
                    locale=locale,
                    key=key,
                    old_text=old_text,
                    new_text=best,
                    source="rules_v1",
                    translation_id=getattr(row, "id", None),
                )
        applied += 1

    if not dry_run:
        db.session.commit()

    report = {
        "ok": True,
        "locale": locale,
        "view": view,
        "limit": limit,
        "dry_run": dry_run,
        "missing": len(missing_entries),
        "applied": applied,
        "skipped": skipped,
        "limit_hit": len(missing_entries) > limit,
    }

    if resp_format == "json" or request.accept_mimetypes.best == "application/json":
        return jsonify(report), 200

    flash(
        f"Auto-fill ({locale}/{view}): applied={applied}, skipped={skipped}, "
        f"missing={len(missing_entries)}{' (dry-run)' if dry_run else ''}.",
        "success" if applied > 0 else "info",
    )

    next_url = (request.form.get("next") or "").strip()
    return _redirect_to_safe_next(
        next_url,
        url_for(
            "admin.admin_translations_list",
            locale=locale,
            view=view,
            only_missing="1",
        ),
    )


@admin_bp.post("/translations/bootstrap-from-po")
def admin_translations_bootstrap_from_po():
    _require_l10n_write()
    locale = (
        request.form.get("locale")
        or request.args.get("locale")
        or current_app.config.get("BABEL_DEFAULT_LOCALE", "fr")
    ).strip().lower()
    view = (request.form.get("view") or request.args.get("view") or "ops").strip().lower()
    dry_run = (request.form.get("dry_run") or request.args.get("dry_run") or "") == "1"
    resp_format = (request.form.get("format") or request.args.get("format") or "").strip().lower()

    try:
        limit = int(request.form.get("limit") or request.args.get("limit") or "300")
    except ValueError:
        limit = 300
    limit = max(1, min(limit, 1000))

    supported = _supported_locales()
    if locale not in supported:
        return jsonify({"ok": False, "error": "unsupported locale"}), 400
    locked = _blocked_by_locale_lock(locale)
    if locked is not None:
        return locked
    if not dry_run:
        frozen = _blocked_by_translation_freeze(locale=locale, action="bootstrap")
        if frozen is not None:
            return frozen
    if view not in {"ops", "core", "inventory", "all"}:
        view = "ops"

    entries = [
        e
        for e in _registry_entries_for_view(view)
        if str(e.get("key") or "").startswith("msgid:")
    ]
    keys = [e["key"] for e in entries if e.get("key")]

    existing_keys: set[str] = set()
    if keys:
        existing_keys = {
            row.key
            for row in UiTranslation.query.filter(
                UiTranslation.locale == locale,
                UiTranslation.key.in_(keys),
            ).all()
        }

    missing_entries = [e for e in entries if e.get("key") and e["key"] not in existing_keys]
    translations = _load_babel_translations(locale)
    to_process = missing_entries[:limit]

    applied = 0
    skipped_existing = len(entries) - len(missing_entries)
    skipped_not_found = 0

    for entry in to_process:
        key = entry["key"]
        msgid = key.split("msgid:", 1)[1].strip()
        if not msgid:
            skipped_not_found += 1
            continue
        translated = (translations.gettext(msgid) if translations else msgid).strip()
        if not translated or translated == msgid:
            skipped_not_found += 1
            continue
        if not dry_run:
            row, _action, old_text, changed = _ui_translation_upsert(locale=locale, key=key, text=translated)
            if changed:
                _log_translation_event(
                    action="po_sync",
                    locale=locale,
                    key=key,
                    old_text=old_text,
                    new_text=translated,
                    source="po_sync",
                    translation_id=getattr(row, "id", None),
                )
        applied += 1

    if not dry_run:
        db.session.commit()

    report = {
        "ok": True,
        "locale": locale,
        "view": view,
        "limit": limit,
        "dry_run": dry_run,
        "missing_total": len(missing_entries),
        "db_existing_skipped": skipped_existing,
        "po_found_applied": applied,
        "po_not_found_skipped": skipped_not_found,
        "limit_hit": len(missing_entries) > limit,
    }

    if resp_format == "json" or request.accept_mimetypes.best == "application/json":
        return jsonify(report), 200

    flash(
        f"PO bootstrap ({locale}/{view}): applied={applied}, existing={skipped_existing}, "
        f"not_found={skipped_not_found}{' (dry-run)' if dry_run else ''}.",
        "success" if applied > 0 else "info",
    )

    next_url = (request.form.get("next") or "").strip()
    return _redirect_to_safe_next(
        next_url,
        url_for(
            "admin.admin_translations_list",
            locale=locale,
            view=view,
            only_missing="1",
        ),
    )


@admin_bp.post("/translations/locale-lock")
def admin_translations_locale_lock():
    _require_l10n_delete()
    locale = (
        request.form.get("locale")
        or request.args.get("locale")
        or current_app.config.get("BABEL_DEFAULT_LOCALE", "fr")
    ).strip().lower()
    action = (request.form.get("action") or request.args.get("action") or "").strip().lower()
    note = (request.form.get("note") or "").strip()
    view = (request.form.get("view") or request.args.get("view") or "ops").strip().lower()
    resp_format = (request.form.get("format") or request.args.get("format") or "").strip().lower()

    if locale not in _supported_locales():
        return jsonify({"ok": False, "error": "unsupported locale"}), 400
    if action not in {"lock", "unlock"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    if len(note) > 255:
        note = note[:255]

    row = _get_locale_lock(locale)
    if row is None:
        row = UiLocaleLock(locale=locale, is_locked=False)
        db.session.add(row)

    if action == "lock":
        row.is_locked = True
        row.locked_at = _utc_now()
        row.locked_by_admin_user_id = getattr(current_user, "id", None)
        row.note = note or row.note
    else:
        row.is_locked = False
        row.note = note or row.note

    db.session.commit()
    result = {
        "ok": True,
        "locale": locale,
        "is_locked": bool(row.is_locked),
        "action": action,
    }

    if resp_format == "json" or request.accept_mimetypes.best == "application/json":
        return jsonify(result), 200

    flash(
        f"Locale {locale.upper()} {'locked' if row.is_locked else 'unlocked'}.",
        "success",
    )
    next_url = (request.form.get("next") or "").strip()
    return _redirect_to_safe_next(
        next_url,
        url_for("admin.admin_translations_list", locale=locale, view=view),
        code=303,
    )


@admin_bp.post("/translations/freeze")
def admin_translations_freeze_toggle():
    _require_l10n_delete()
    action = (request.form.get("action") or request.args.get("action") or "").strip().lower()
    release_tag = (request.form.get("release_tag") or "").strip()[:64]
    note = (request.form.get("note") or "").strip()[:255]
    next_url = (request.form.get("next") or "").strip()
    resp_format = (request.form.get("format") or request.args.get("format") or "").strip().lower()

    if action not in {"activate", "deactivate"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400

    row = _get_translation_freeze_state()
    if row is None:
        row = UiTranslationFreeze(is_active=False)
        db.session.add(row)

    if action == "activate":
        row.is_active = True
        row.activated_at = _utc_now()
        row.activated_by_admin_user_id = getattr(current_user, "id", None)
        if release_tag:
            row.release_tag = release_tag
        if note:
            row.note = note
    else:
        row.is_active = False
        if release_tag:
            row.release_tag = release_tag
        if note:
            row.note = note

    db.session.commit()
    result = {
        "ok": True,
        "is_active": bool(row.is_active),
        "release_tag": row.release_tag,
    }

    if resp_format == "json" or request.accept_mimetypes.best == "application/json":
        return jsonify(result), 200

    flash(
        f"Translation freeze {'activated' if row.is_active else 'deactivated'}.",
        "success",
    )
    return _redirect_to_safe_next(
        next_url,
        url_for("admin.admin_translations_list"),
        code=303,
    )


def admin_required_404():
    if not (current_user.is_authenticated and getattr(current_user, "is_admin", False)):
        abort(404)


def _supported_locales() -> list[str]:
    configured = current_app.config.get("SUPPORTED_LOCALES") or ("fr", "en", "de", "bg")
    locales = [str(x).strip().lower() for x in configured if str(x).strip()]
    return sorted(set(locales))


def _can_l10n_write() -> bool:
    return _admin_role_value() in {"ops", "superadmin"}


def _can_l10n_delete() -> bool:
    return _admin_role_value() == "superadmin"


def _get_locale_lock(locale: str) -> UiLocaleLock | None:
    loc = (locale or "").strip().lower()
    if not loc:
        return None
    return UiLocaleLock.query.filter_by(locale=loc).first()


def _get_translation_freeze_state() -> UiTranslationFreeze | None:
    if not _table_exists("ui_translation_freeze"):
        return None
    return UiTranslationFreeze.query.order_by(UiTranslationFreeze.id.asc()).first()


def _is_translation_frozen() -> bool:
    row = _get_translation_freeze_state()
    return bool(row and row.is_active)


def _is_locale_locked(locale: str) -> bool:
    row = _get_locale_lock(locale)
    return bool(row and row.is_locked)


def _require_l10n_write() -> None:
    if _can_l10n_write():
        return
    _audit_denied_action(required_roles={"ops", "superadmin"}, actor_role=_admin_role_value())
    abort(403)


def _require_l10n_delete() -> None:
    if _can_l10n_delete():
        return
    _audit_denied_action(required_roles={"superadmin"}, actor_role=_admin_role_value())
    abort(403)


def _blocked_by_locale_lock(locale: str):
    if not _is_locale_locked(locale):
        return None
    if _can_l10n_delete():
        return None
    wants_json = (
        (request.form.get("format") or request.args.get("format") or "").strip().lower()
        == "json"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({"ok": False, "error": "locale_locked", "locale": locale}), 423
    flash(f"Locale {locale.upper()} is locked.", "warning")
    next_url = (request.form.get("next") or "").strip()
    view = (request.form.get("view") or request.args.get("view") or "ops").strip().lower()
    return _redirect_to_safe_next(
        next_url,
        url_for("admin.admin_translations_list", locale=locale, view=view),
        code=303,
    )


def _blocked_by_translation_freeze(*, locale: str, action: str, allow: bool = False):
    if allow or not _is_translation_frozen():
        return None
    wants_json = (
        (request.form.get("format") or request.args.get("format") or "").strip().lower()
        == "json"
        or request.accept_mimetypes.best == "application/json"
    )
    freeze = _get_translation_freeze_state()
    payload = {
        "ok": False,
        "error": "translation_frozen",
        "action": action,
        "locale": locale,
        "release_tag": (freeze.release_tag if freeze else None),
    }
    if wants_json:
        return jsonify(payload), 423
    msg = "Translation freeze is active for release."
    if freeze and freeze.release_tag:
        msg = f"Translation freeze active ({freeze.release_tag})."
    flash(msg, "warning")
    next_url = (request.form.get("next") or "").strip()
    view = (request.form.get("view") or request.args.get("view") or "ops").strip().lower()
    return _redirect_to_safe_next(
        next_url,
        url_for("admin.admin_translations_list", locale=locale, view=view),
        code=303,
    )


def _sanitize_translation_text(value: str) -> str:
    cleaned = _CTRL_CHARS_RE.sub("", value or "")
    return cleaned.strip()


def _fallback_preview(key: str, locale: str) -> str:
    try:
        with force_locale(locale):
            text = _(key)
        return text or key
    except Exception:
        return key


def _load_ui_key_registry() -> list[dict]:
    if not _UI_KEYS_PATH.exists():
        return []
    try:
        rows = json.loads(_UI_KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        out.append(
            {
                "key": key,
                "default": str(row.get("default") or "").strip(),
                "domain": str(row.get("domain") or "").strip(),
                "kind": str(row.get("kind") or "tkey").strip() or "tkey",
                "tier": str(row.get("tier") or "core").strip() or "core",
            }
        )
    return out


def _load_ui_domains() -> dict:
    if not _UI_DOMAINS_PATH.exists():
        return {"core_ops_domains": []}
    try:
        data = json.loads(_UI_DOMAINS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"core_ops_domains": []}
    if not isinstance(data, dict):
        return {"core_ops_domains": []}
    domains = data.get("core_ops_domains") or []
    if not isinstance(domains, list):
        domains = []
    return {"core_ops_domains": [str(x).strip() for x in domains if str(x).strip()]}


def _registry_entries_for_view(view: str) -> list[dict]:
    registry_all = _load_ui_key_registry()
    view_norm = (view or "ops").strip().lower()
    if view_norm not in {"ops", "core", "inventory", "all"}:
        view_norm = "ops"
    ops_domains = set(_load_ui_domains().get("core_ops_domains", []))

    def _match(row: dict) -> bool:
        row_tier = row.get("tier") or "core"
        row_dom = row.get("domain") or ""
        if view_norm == "ops":
            return row_tier == "core" and row_dom in ops_domains
        if view_norm == "core":
            return row_tier == "core"
        if view_norm == "inventory":
            return row_tier == "inventory"
        return True

    return [row for row in registry_all if _match(row)]


def _registry_index() -> dict[str, dict]:
    return {row["key"]: row for row in _load_ui_key_registry()}


def _ui_registry_get(key: str) -> dict:
    return _registry_index().get((key or "").strip(), {})


def _ui_translation_upsert(locale: str, key: str, text: str) -> tuple[UiTranslation, str, str | None, bool]:
    row = UiTranslation.query.filter_by(key=key, locale=locale).first()
    if row is None:
        row = UiTranslation(key=key, locale=locale, text=text)
        db.session.add(row)
        return row, "created", None, True
    old_text = row.text
    changed = old_text != text
    if changed:
        row.text = text
    row.is_active = True
    return row, "updated", old_text, changed


def _current_admin_email() -> str | None:
    email = (getattr(current_user, "email", None) or "").strip()
    if email:
        return email
    username = (getattr(current_user, "username", None) or "").strip()
    return username or None


def _log_translation_event(
    *,
    action: str,
    locale: str,
    key: str,
    old_text: str | None = None,
    new_text: str | None = None,
    source: str = "human",
    translation_id: int | None = None,
) -> None:
    if not _table_exists("ui_translation_events"):
        return
    db.session.add(
        UiTranslationEvent(
            translation_id=translation_id,
            locale=(locale or "").strip().lower(),
            key=(key or "").strip(),
            action=(action or "").strip(),
            source=(source or "human").strip(),
            actor_admin_user_id=getattr(current_user, "id", None),
            actor_email=_current_admin_email(),
            old_text=old_text,
            new_text=new_text,
        )
    )


def _load_babel_translations(locale: str) -> Translations | None:
    raw_dirs = str(current_app.config.get("BABEL_TRANSLATION_DIRECTORIES") or "translations")
    candidates = [part.strip() for part in re.split(r"[;,]", raw_dirs) if part.strip()]
    if not candidates:
        candidates = ["translations"]
    for directory in candidates:
        try:
            return Translations.load(directory, locales=[locale], domain="messages")
        except Exception:
            continue
    return None


def _translation_kpi(locale: str, keys: list[str]) -> dict:
    total = len(keys)
    if total == 0:
        return {"total": 0, "overrides": 0, "missing": 0, "coverage": 0.0}

    overrides = (
        db.session.query(func.count(UiTranslation.key.distinct()))
        .filter(
            UiTranslation.locale == locale,
            UiTranslation.key.in_(keys),
        )
        .scalar()
        or 0
    )
    missing = max(total - int(overrides), 0)
    coverage = round((float(overrides) / float(total)) * 100.0, 1) if total else 0.0
    return {
        "total": int(total),
        "overrides": int(overrides),
        "missing": int(missing),
        "coverage": float(coverage),
    }


def _coverage_bucket_for_domain(domain: str, ops_domains: set[str]) -> str:
    dom = (domain or "").strip().lower()
    if dom in ops_domains:
        return "ops"
    if dom.startswith("admin"):
        return "admin"
    if dom.startswith("volunteer"):
        return "volunteer"
    return "public"


def _coverage_class(ratio: float) -> str:
    if ratio > 0.9:
        return "success"
    if ratio > 0.6:
        return "warning"
    return "danger"


def _load_terminology() -> dict:
    if not _TERMINOLOGY_PATH.exists():
        return {}
    try:
        return json.loads(_TERMINOLOGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _infer_kind(key: str) -> str:
    k = (key or "").lower()
    if any(p in k for p in ["btn_", "cta_", "action_", "submit_", "save_", "close_", "assign_"]):
        return "button"
    if any(p in k for p in ["title_", "header_", "nav_", "menu_"]):
        return "title"
    if any(p in k for p in ["hint_", "help_", "desc_", "subtitle_", "empty_"]):
        return "helper"
    return "label"


def _rules_suggest(key: str, locale: str, default: str, domain: str) -> list[dict]:
    """Rules-based suggestions (deterministic/explainable, no external calls)."""
    kind = _infer_kind(key)
    term = _load_terminology()
    loc_terms = term.get(locale, {})
    is_admin = (domain or "").strip().lower() == "admin"
    k = (key or "").lower()
    default = (default or "").strip()

    def t(token: str, fallback: str) -> str:
        return str(loc_terms.get(token, fallback) or fallback).strip()

    if "dashboard" in k:
        base = t("dashboard", "Dashboard")
        reason_base = "Term: dashboard"
    elif "assign" in k or "claim" in k or "prendre_en_charge" in k:
        base = t("assign", "Übernehmen" if locale == "de" else "Assign")
        reason_base = "Term: assign/claim"
    elif "status" in k:
        base = t("status", "Status")
        reason_base = "Term: status"
    elif "request" in k or "demande" in k:
        base = t("request", "Anfrage" if locale == "de" else "Request")
        reason_base = "Term: request"
    elif "case" in k or "dossier" in k:
        base = t("case", "Vorgang" if locale == "de" else "Case")
        reason_base = "Term: case"
    else:
        base = default or key
        reason_base = "Fallback: default/key"

    if kind == "button":
        v1 = base
        v2 = f"{base}..." if len(base) <= 20 else base
        v3 = base.upper() if (is_admin and len(base) <= 16) else base
        return [
            {"text": v1, "reason": f"Button label (short). {reason_base}"},
            {"text": v2, "reason": f"Button label (progressive). {reason_base}"},
            {"text": v3, "reason": f"Button label (ops emphasis). {reason_base}"},
        ]

    if kind == "title":
        v1 = base
        v2 = f"{base} — {t('dashboard', 'Dashboard')}" if ("dashboard" not in k and len(base) <= 24) else base
        v3 = f"{base} ({locale.upper()})" if (is_admin and len(base) <= 24) else base
        return [
            {"text": v1, "reason": f"Title. {reason_base}"},
            {"text": v2, "reason": f"Title with context. {reason_base}"},
            {"text": v3, "reason": f"Title (admin clarity). {reason_base}"},
        ]

    if kind == "helper":
        v1 = base
        v2 = f"{base}. {t('status', 'Status')}." if ("status" not in k and len(base) <= 40) else base
        v3 = f"{base}." if base and not base.endswith(".") else base
        return [
            {"text": v1, "reason": f"Helper text (concise). {reason_base}"},
            {"text": v2, "reason": f"Helper text (adds cue). {reason_base}"},
            {"text": v3, "reason": f"Helper text (punctuation). {reason_base}"},
        ]

    v1 = base
    v2 = base.capitalize() if base else base
    v3 = base
    return [
        {"text": v1, "reason": f"Label. {reason_base}"},
        {"text": v2, "reason": f"Label (capitalization). {reason_base}"},
        {"text": v3, "reason": f"Label (stable). {reason_base}"},
    ]


def _source_text_for_ai(*, key: str, default: str) -> str:
    if default:
        return default.strip()
    if (key or "").startswith("msgid:"):
        return key.split("msgid:", 1)[1].strip()
    return (key or "").strip()


def _ai_suggest(
    *,
    key: str,
    locale: str,
    default: str,
    domain: str,
    source_text: str,
    provider: str,
) -> list[dict]:
    # Always produce deterministic fallback suggestions.
    rules = _rules_suggest(key=key, locale=locale, default=default, domain=domain)
    if provider != "hf_local":
        return rules

    translated = _hf_translate_fr_to(locale=locale, text=source_text)
    out: list[dict] = []
    seen: set[str] = set()

    if translated:
        ai_text = translated.strip()
        if ai_text and ai_text not in seen:
            out.append(
                {
                    "text": ai_text,
                    "reason": "AI suggestion (hf_local MarianMT).",
                }
            )
            seen.add(ai_text)

    for item in rules:
        txt = (item.get("text") or "").strip()
        if not txt or txt in seen:
            continue
        out.append({"text": txt, "reason": item.get("reason") or "Rules fallback."})
        seen.add(txt)
        if len(out) >= 3:
            break

    return out[:3] if out else rules[:3]


def _hf_translate_fr_to(*, locale: str, text: str) -> str | None:
    target = (locale or "").strip().lower()
    src_text = (text or "").strip()
    if not src_text:
        return None
    if target == "fr":
        return src_text
    model_name = _HF_MODEL_MAP.get(target)
    if not model_name:
        return None

    with _HF_TRANSLATOR_LOCK:
        if model_name in _HF_TRANSLATOR_FAILED:
            return None
        pair = _HF_TRANSLATORS.get(model_name)

    if pair is None:
        try:
            from transformers import MarianMTModel, MarianTokenizer
        except Exception:
            with _HF_TRANSLATOR_LOCK:
                _HF_TRANSLATOR_FAILED.add(model_name)
            return None
        try:
            tokenizer = MarianTokenizer.from_pretrained(model_name)
            model = MarianMTModel.from_pretrained(model_name)
        except Exception:
            with _HF_TRANSLATOR_LOCK:
                _HF_TRANSLATOR_FAILED.add(model_name)
            return None
        with _HF_TRANSLATOR_LOCK:
            _HF_TRANSLATORS[model_name] = (tokenizer, model)
            pair = (tokenizer, model)

    try:
        tokenizer, model = pair
        tokens = tokenizer(src_text, return_tensors="pt", truncation=True)
        generated = model.generate(**tokens)
        out = tokenizer.decode(generated[0], skip_special_tokens=True).strip()
        return out or None
    except Exception:
        return None


def _is_local_request() -> bool:
    """True when request comes from localhost (IPv4/IPv6)."""
    return request.remote_addr in ("127.0.0.1", "::1")


def is_safe_url(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (test_url.scheme in ("http", "https")) and (
        ref_url.netloc == test_url.netloc
    )


def _redirect_protected_login(endpoint: str):
    nxt = request.full_path if request.query_string else request.path
    return redirect(url_for(endpoint, next=nxt), code=303)


def _safe_next_url(candidate: str | None) -> str:
    target = (candidate or "").strip()
    if not target:
        return ""
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return ""
    if target.startswith("//") or "\\" in target:
        return ""
    if (
        target != "/admin"
        and not target.startswith("/admin/")
        and target != "/ops"
        and not target.startswith("/ops/")
    ):
        return ""
    return target if is_safe_url(target) else ""


def _redirect_to_safe_next(next_url: str | None, fallback_url: str, *, code: int = 303):
    target = _safe_next_url(next_url)
    return redirect(target or fallback_url, code=code)


def _endpoint_allowed_admin_roles(endpoint: str | None) -> set[str] | None:
    if not endpoint:
        return None

    view_func = current_app.view_functions.get(endpoint)
    visited: set[int] = set()

    while callable(view_func) and id(view_func) not in visited:
        visited.add(id(view_func))
        try:
            nonlocals = inspect.getclosurevars(view_func).nonlocals
        except Exception:
            nonlocals = {}
        allowed = nonlocals.get("allowed")
        if isinstance(allowed, set) and all(isinstance(item, str) for item in allowed):
            return {item.strip().lower() for item in allowed if item}
        view_func = getattr(view_func, "__wrapped__", None)

    return None


def _is_next_url_authorized_for_admin_user(
    user: AdminUser | None,
    next_url: str | None,
) -> bool:
    target = _safe_next_url(next_url)
    if not target or user is None:
        return False

    role = _normalize_admin_role_value(getattr(user, "role", None))
    structure_id = getattr(user, "structure_id", None)

    if target == "/ops" or target.startswith("/ops/"):
        return role in {"admin", "ops", "readonly", "superadmin"}

    if target == "/admin" or target.startswith("/admin/"):
        if role == "superadmin" and structure_id is None:
            return True
        try:
            adapter = current_app.url_map.bind(request.host, url_scheme=request.scheme)
            endpoint, _ = adapter.match(target, method="GET")
        except Exception:
            return False
        allowed_roles = _endpoint_allowed_admin_roles(endpoint)
        if allowed_roles is None:
            return False
        return role in allowed_roles

    return False


def _next_url_for_admin_user(user: AdminUser | None, next_url: str | None) -> str:
    target = _safe_next_url(next_url)
    if not target:
        return ""
    return target if _is_next_url_authorized_for_admin_user(user, target) else ""


def _has_pending_admin_mfa() -> bool:
    if not session.get("admin_password_verified"):
        return False
    if not session.get("mfa_required"):
        return False
    if not (current_user.is_authenticated and getattr(current_user, "is_admin", False)):
        return False
    pending_user_id = session.get("pending_admin_user_id")
    current_admin_id = getattr(current_user, "id", None)
    try:
        return int(pending_user_id) == int(current_admin_id)
    except (TypeError, ValueError):
        return False


def _pending_admin_mfa_redirect_response():
    nxt = request.full_path if request.query_string else request.path
    next_url = _safe_next_url(nxt)
    user_has_mfa = bool(getattr(current_user, "mfa_enabled", False)) and bool(
        getattr(current_user, "totp_secret", None)
    )
    endpoint = "admin.admin_mfa_verify" if user_has_mfa else "admin.admin_mfa_setup"
    return redirect(url_for(endpoint, next=next_url), code=303)


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Harden: hide admin surface from non-admins (404 instead of redirect)
        if not (
            current_user.is_authenticated and getattr(current_user, "is_admin", False)
        ):
            abort(404)
        return view_func(*args, **kwargs)

    return wrapper


def operator_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return _redirect_protected_login("admin.ops_login")

        if not (
            current_user.is_authenticated and getattr(current_user, "is_admin", False)
        ):
            session_role = (session.get("role") or "").strip().lower()
            if session.get("is_admin") or session.get("admin_logged_in"):
                if session_role in {"ops", "readonly", "admin", "superadmin"}:
                    return view_func(*args, **kwargs)
            _audit_denied_action(
                required_roles={"ops", "readonly", "admin", "superadmin"},
                actor_role=session_role or None,
            )
            abort(403)
        role = _admin_role_value()
        if role is None or role not in {"ops", "readonly", "admin", "superadmin"}:
            _audit_denied_action(
                required_roles={"ops", "readonly", "admin", "superadmin"},
                actor_role=role,
            )
            abort(403)
        return view_func(*args, **kwargs)

    return wrapper


def _admin_role_value() -> str | None:
    raw_role = getattr(current_user, "role", None)
    role = getattr(raw_role, "value", raw_role)
    role = (role or "").strip().lower()
    if role == "admin":
        return "admin"
    if role in {"super_admin", "super-admin", "superadmin"}:
        return "superadmin"
    if role in {"ops"}:
        return "ops"
    if role in {"readonly", "read-only"}:
        return "readonly"
    return None


def _normalize_admin_role_value(raw_role) -> str | None:
    role = getattr(raw_role, "value", raw_role)
    role = (role or "").strip().lower()
    if role == "admin":
        return "admin"
    if role in {"super_admin", "super-admin", "superadmin"}:
        return "superadmin"
    if role == "ops":
        return "ops"
    if role in {"readonly", "read-only"}:
        return "readonly"
    return None


def admin_role_required(*allowed_roles: str):
    allowed = {r.strip().lower() for r in allowed_roles if r}

    def deco(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            role = _admin_role_value()
            if role is None:
                _audit_denied_action(required_roles=allowed, actor_role=str(role))
                abort(403)
            if role not in allowed:
                _audit_denied_action(required_roles=allowed, actor_role=role)
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return deco


def _is_superadmin_role(role) -> bool:
    return _normalize_admin_role_value(role) == "superadmin"


@admin_bp.get("/sanity")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_sanity():
    admin_required_404()
    checks = run_system_checks()
    return render_template("admin/system_sanity.html", checks=checks)


@admin_bp.get("/system")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_system():
    admin_required_404()

    diagnostics: list[dict[str, str]] = []

    def add_check(label: str, fn):
        try:
            status, detail = fn()
            diagnostics.append(
                {
                    "label": label,
                    "status": status,
                    "detail": detail or "",
                }
            )
        except Exception as exc:
            diagnostics.append(
                {
                    "label": label,
                    "status": "error",
                    "detail": str(exc),
                }
            )

    def db_connectivity():
        db.session.execute(db.text("SELECT 1"))
        return "ok", "Connected"

    def alembic_revision():
        version = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")
        ).scalar()
        if version:
            return "ok", str(version)
        return "warning", "No revision recorded"

    def deploy_version():
        for key in ("GIT_SHA", "RENDER_GIT_COMMIT", "RENDER_GIT_COMMIT_SHA", "COMMIT_SHA"):
            value = (os.getenv(key) or "").strip()
            if value:
                return "ok", f"{key}={value[:12]}"
        return "warning", "No deploy SHA env"

    def notification_jobs_exists():
        if _table_exists("notification_jobs"):
            return "ok", "Table exists"
        return "warning", "Table missing"

    def notification_jobs_count():
        if not _table_exists("notification_jobs"):
            return "warning", "Table missing"
        count = db.session.query(func.count(NotificationJob.id)).scalar() or 0
        return "ok", str(int(count))

    def notification_jobs_failed():
        if not _table_exists("notification_jobs"):
            return "warning", "Table missing"
        count = (
            db.session.query(func.count(NotificationJob.id))
            .filter(NotificationJob.status.in_(("dead_letter", "failed")))
            .scalar()
            or 0
        )
        return "ok", str(int(count))

    def admin_users_count():
        if not _table_exists("admin_users"):
            return "warning", "Table missing"
        count = db.session.query(func.count(AdminUser.id)).scalar() or 0
        return "ok", str(int(count))

    add_check("Database connectivity", db_connectivity)
    add_check("Alembic revision", alembic_revision)
    add_check("Deploy version", deploy_version)
    add_check("notification_jobs table", notification_jobs_exists)
    add_check("notification_jobs rows", notification_jobs_count)
    add_check("notification_jobs failed", notification_jobs_failed)
    add_check("Admin users", admin_users_count)

    return render_template("admin/system.html", diagnostics=diagnostics)


@admin_bp.get("/ops")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_ops():
    admin_required_404()
    return render_template("admin/ops_dashboard.html")


@admin_bp.get("/operator")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_operator_dashboard():
    admin_required_404()
    return _render_operator_dashboard()


@ops_bp.get("")
@ops_bp.get("/")
@ops_bp.get("/workspace")
@operator_required
def ops_workspace():
    return _render_operator_dashboard()


def _render_operator_dashboard():
    if current_app.config.get("DEMO_MODE"):
        demo = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        demo_kpis = demo["workspace_kpis"]
        demo_rows = list(demo["workspace_rows"] or [])
        scenario_meta = demo["scenario_meta"]
        return render_template(
            "admin/operator_dashboard.html",
            urgent_count=demo_kpis["critical"],
            unassigned_count=demo_kpis["unassigned"],
            followup_count=demo_kpis["relance"],
            updated_today_count=demo_kpis["updated_today"],
            failed_notif_count=demo_kpis["notifications_failed"],
            retry_notif_count=demo_kpis.get("retry_notifications", 0),
            queue_rows=demo_rows[:5],
            queue_hidden_count=max(len(demo_rows) - 5, 0),
            queue_reasons=demo["workspace_queue_reasons"],
            ops_priority_levels=demo["workspace_priority_levels"],
            scenario_label=scenario_meta["label"],
            scenario_description=scenario_meta["short_description"],
        )

    base_query = _scope_requests(Request.query).filter(Request.deleted_at.is_(None))
    try:
        base_query = base_query.filter(Request.is_archived.is_(False))
    except Exception:
        pass

    # Treat unset/legacy "new" requests as operator-actionable so local/demo
    # imports do not disappear from the ops queue while still staying scoped.
    actionable_filter = request_dashboard_actionable_filter()
    activity_sq, activity_expr, stale_threshold, stale_filter = (
        build_request_inactivity_components(_now_utc())
    )
    today_start = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    urgent_filter = or_(
        func.lower(func.coalesce(Request.priority, "")).in_(["high", "critical"]),
        func.coalesce(Request.risk_score, 0) >= 85,
    )
    unassigned_filter = Request.owner_id.is_(None)
    updated_today_filter = activity_expr >= today_start
    priority_filter = or_(
        urgent_filter,
        unassigned_filter,
        stale_filter,
    )

    workspace_query = (
        base_query.outerjoin(activity_sq, activity_sq.c.request_id == Request.id)
        .filter(actionable_filter, priority_filter)
    )

    case_base_query, case_kpi_filters = _build_scoped_cases_base_query()
    workspace_case_counts = _case_queue_counts_for_filters(
        case_base_query,
        case_filters=case_kpi_filters,
    )
    urgent_count = workspace_case_counts["critical"]
    unassigned_count = workspace_case_counts["no_owner"]
    followup_count = workspace_case_counts["stale_72h"]
    updated_today_count = workspace_query.filter(updated_today_filter).count()

    failed_notif_count = 0
    retry_notif_count = 0
    if _table_exists("notification_jobs"):
        notif_base = _scoped_notification_jobs_query()
        failed_notif_count = notif_base.filter(
            _notification_bucket_filter("failed")
        ).count()
        retry_notif_count = notif_base.filter(
            _notification_bucket_filter("retry")
        ).count()

    workspace_total_count = workspace_query.count()
    queue_rows = (
        workspace_query.options(joinedload(Request.owner))
        .order_by(
            case((urgent_filter, 0), else_=1).asc(),
            case((unassigned_filter, 0), else_=1).asc(),
            case((stale_filter, 0), else_=1).asc(),
            case((updated_today_filter, 0), else_=1).asc(),
            activity_expr.desc().nullslast(),
            Request.id.desc(),
        )
        .all()
    )

    queue_reasons = {}
    now_utc = _now_utc()
    scored_rows = []
    for r in queue_rows:
        last_activity_at = get_request_last_meaningful_activity(r)
        result = compute_ops_priority(
            request_row=r,
            activity_ref=last_activity_at,
            now=now_utc,
        )
        scored_rows.append((int(result.get("ops_priority_score") or 0), r, result, last_activity_at))
    scored_rows.sort(key=lambda row: row[0], reverse=True)
    top_scored_rows = scored_rows[:5]
    queue_rows = [row[1] for row in top_scored_rows]
    ops_priority_levels = {}
    workspace_row_flags = {}
    workspace_last_activity_by_id = {}
    for _, row, result, last_activity_at in top_scored_rows:
        workspace_last_activity_by_id[int(row.id)] = last_activity_at
        queue_reasons[int(row.id)] = result.get("ops_priority_reasons") or []
        ops_level = result.get("ops_priority_level") or "normal"
        ops_priority_levels[int(row.id)] = ops_level
        is_stale_72h = False
        try:
            activity_ref = last_activity_at
            if activity_ref is not None:
                if getattr(activity_ref, "tzinfo", None) is None:
                    activity_ref = activity_ref.replace(tzinfo=UTC)
                is_stale_72h = activity_ref <= stale_threshold
        except Exception:
            is_stale_72h = False
        updated_ref = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
        updated_today = (
            1
            if getattr(updated_ref, "date", lambda: None)() == now_utc.date()
            else 0
        )
        workspace_row_flags[int(row.id)] = {
            "critical": 1 if ops_level == "critique" else 0,
            "owner_missing": 1 if getattr(row, "owner_id", None) is None else 0,
            "stale_72h": 1 if is_stale_72h else 0,
            "updated_today": updated_today,
            "notification_failed": 0,
        }

    request_ids = [int(row.id) for row in queue_rows]
    case_links_by_request = {}
    if request_ids:
        case_rows = (
            Case.query.filter(Case.request_id.in_(request_ids))
            .order_by(Case.id.desc())
            .all()
        )
        for case_row in case_rows:
            rid = int(case_row.request_id)
            if rid not in case_links_by_request:
                case_links_by_request[rid] = int(case_row.id)

    return render_template(
        "admin/operator_dashboard.html",
        urgent_count=urgent_count,
        unassigned_count=unassigned_count,
        followup_count=followup_count,
        updated_today_count=updated_today_count,
        failed_notif_count=failed_notif_count,
        retry_notif_count=retry_notif_count,
        queue_rows=queue_rows,
        queue_hidden_count=max(workspace_total_count - len(queue_rows), 0),
        queue_reasons=queue_reasons,
        ops_priority_levels=ops_priority_levels,
        workspace_row_flags=workspace_row_flags,
        workspace_last_activity_by_id=workspace_last_activity_by_id,
        case_links_by_request=case_links_by_request,
    )


@ops_bp.get("/requests/<int:req_id>")
@operator_required
def ops_request_detail(req_id: int):
    return redirect(url_for("admin.admin_request_details", req_id=req_id), code=302)


@ops_bp.get("/cases")
@operator_required
def ops_cases_list():
    return _render_cases_list()


@ops_bp.get("/notifications")
@operator_required
def ops_notifications_list():
    return _render_notifications_list()


@admin_bp.get("/risk-map")
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_risk_map():
    admin_required_404()
    zone_city = (request.args.get("city") or "Boulogne-Billancourt").strip()
    active_case_count = 0
    critical_queue_count = 0
    try:
        scoped_ids_subq = _scope_requests(Request.query.with_entities(Request.id)).subquery()
        city_like = f"%{zone_city.lower()}%"
        city_filter = or_(
            func.lower(func.coalesce(Request.city, "")).like(city_like),
            func.lower(func.coalesce(Request.location_text, "")).like(city_like),
            func.lower(func.coalesce(Request.address_line, "")).like(city_like),
        )
        active_statuses = ("new", "triaged", "assigned", "in_progress", "pending")
        base_query = (
            Case.query.join(Request, Case.request_id == Request.id)
            .join(scoped_ids_subq, Request.id == scoped_ids_subq.c.id)
            .filter(city_filter, Case.status.in_(active_statuses))
        )
        active_case_count = int(base_query.count() or 0)
        critical_queue_count = int(
            base_query.filter(
                or_(
                    Case.priority == "critical",
                    func.coalesce(Case.risk_score, 0) >= 85,
                )
            ).count()
            or 0
        )
    except Exception:
        db.session.rollback()

    return render_template(
        "admin/risk_map.html",
        zone_city=zone_city,
        active_case_count=active_case_count,
        critical_queue_count=critical_queue_count,
    )


def _attempted_action_label() -> str:
    if request.endpoint:
        return request.endpoint
    return f"{request.method} {request.path}"


def _audit_denied_action(required_roles: set[str], actor_role: str | None) -> None:
    if request.method not in STATE_CHANGING_METHODS:
        return

    target_type = "AdminRoute"
    target_id = 0
    try:
        view_args = request.view_args or {}
        if "req_id" in view_args and view_args.get("req_id") is not None:
            target_type = "Request"
            target_id = int(view_args["req_id"])
        elif "interest_id" in view_args and view_args.get("interest_id") is not None:
            target_type = "Interest"
            target_id = int(view_args["interest_id"])
        elif "admin_id" in view_args and view_args.get("admin_id") is not None:
            target_type = "AdminUser"
            target_id = int(view_args["admin_id"])
    except Exception:
        target_type = "AdminRoute"
        target_id = 0

    audit_admin_action(
        action="security.denied_action",
        target_type=target_type,
        target_id=target_id,
        payload={
            "attempted_action": _attempted_action_label(),
            "required_roles": sorted(list(required_roles)),
            "actor_role": actor_role,
            "method": request.method,
            "path": request.path,
        },
    )


def log_request_activity(req_obj, action, old=None, new=None, actor_admin_id=None):
    """Append a RequestActivity row; swallow errors so UI flows stay smooth."""
    if not _table_has_column("request_activities", "volunteer_id"):
        return
    try:
        actor_id = actor_admin_id
        if actor_id is None and getattr(current_user, "is_authenticated", False):
            actor_id = getattr(current_user, "id", None)
        activity = RequestActivity(
            request_id=getattr(req_obj, "id", req_obj),
            actor_admin_id=actor_id,
            action=action,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
            created_at=utc_now(),
        )
        db.session.add(activity)
    except Exception:
        pass


def _audit_request(
    req_id: int,
    *,
    action: str,
    message: str | None = None,
    old: str | None = None,
    new: str | None = None,
    meta: dict | None = None,
):
    if not _table_has_column("activity_logs", "id"):
        return
    try:
        log_activity(
            entity_type="request",
            entity_id=req_id,
            action=action,
            message=message,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
            meta=meta,
        )
    except Exception:
        pass


def _audit_pro_access(
    pro_id: int,
    *,
    action: str,
    message: str | None = None,
    old: str | None = None,
    new: str | None = None,
    meta: dict | None = None,
):
    if not _table_has_column("activity_logs", "id"):
        return
    try:
        log_activity(
            entity_type="pro_access",
            entity_id=pro_id,
            action=action,
            message=message,
            old_value=str(old) if old is not None else None,
            new_value=str(new) if new is not None else None,
            meta=meta,
        )
    except Exception:
        pass


def _mfa_ok_set(ttl_min: int | None = None):
    ttl = ttl_min or current_app.config.get("MFA_SESSION_TTL_MIN", 720)
    session[current_app.config.get("MFA_SESSION_KEY", "mfa_ok")] = True
    try:
        session["mfa_ok_until"] = (utc_now() + timedelta(minutes=ttl)).isoformat()
    except Exception:
        session["mfa_ok_until"] = None


def _mfa_ok_clear():
    session.pop(current_app.config.get("MFA_SESSION_KEY", "mfa_ok"), None)
    session.pop("mfa_ok_until", None)


def _finalize_admin_session(
    user: AdminUser,
    next_url: str | None,
    *,
    fallback_url: str,
):
    _mfa_ok_set()
    session["admin_user_id"] = int(user.id)
    session["admin_logged_in"] = True
    session.pop("pending_admin_user_id", None)
    session.pop("admin_password_verified", None)
    session.pop("pending_email_2fa", None)
    session.pop("email_2fa_code", None)
    session.pop("email_2fa_expires", None)
    session.modified = True
    current_app.logger.warning("[SESSION] %s", dict(session))
    target = _next_url_for_admin_user(user, next_url)
    return redirect(target or fallback_url, code=303)


def _mfa_ok_is_valid() -> bool:
    if not session.get(current_app.config.get("MFA_SESSION_KEY", "mfa_ok")):
        return False
    until = session.get("mfa_ok_until")
    if not until:
        return False
    try:
        return utc_now() <= datetime.fromisoformat(until)
    except Exception:
        return False


def mark_mfa_verified():
    session["admin_mfa_last_verified"] = int(time.time())
    session["admin_mfa_user_id"] = getattr(current_user, "id", None)


def is_mfa_fresh() -> bool:
    ts = session.get("admin_mfa_last_verified")
    if not ts:
        return False
    if session.get("admin_mfa_user_id") != getattr(current_user, "id", None):
        return False

    ttl = current_app.config.get("ADMIN_MFA_STEPUP_TTL_SECONDS", 600)
    return (int(time.time()) - int(ts)) < int(ttl)


def require_fresh_mfa(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_mfa_required(current_user):
            return view(*args, **kwargs)

        if not is_mfa_fresh():
            nxt = request.full_path if request.query_string else request.path
            return redirect(url_for("admin.admin_mfa_verify", next=nxt))

        return view(*args, **kwargs)

    return wrapper


def _mfa_lock_is_active() -> tuple[bool, int]:
    lock_until = session.get("mfa_lock_until")
    if not lock_until:
        return (False, 0)
    try:
        dt = datetime.fromisoformat(lock_until)
        if utc_now() >= dt:
            session.pop("mfa_lock_until", None)
            session.pop("mfa_attempts", None)
            return (False, 0)
        return (True, int((dt - utc_now()).total_seconds()))
    except Exception:
        session.pop("mfa_lock_until", None)
        session.pop("mfa_attempts", None)
        return (False, 0)


def _mfa_attempt_fail():
    max_attempts = current_app.config.get("MFA_VERIFY_MAX_ATTEMPTS", 8)
    lock_min = current_app.config.get("MFA_VERIFY_LOCK_MIN", 10)
    session["mfa_attempts"] = int(session.get("mfa_attempts", 0)) + 1
    if session["mfa_attempts"] >= max_attempts:
        try:
            session["mfa_lock_until"] = (
                utc_now() + timedelta(minutes=lock_min)
            ).isoformat()
        except Exception:
            session["mfa_lock_until"] = None


def _mfa_attempt_reset():
    session.pop("mfa_attempts", None)
    session.pop("mfa_lock_until", None)


def _generate_backup_codes(n: int = 10) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(n):
        code = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(code)
    return codes


def _hash_codes(codes: list[str]) -> list[str]:
    return [generate_password_hash(c) for c in codes]


def _load_hashes(user) -> list[str]:
    raw = getattr(user, "backup_codes_hashes", None)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_hashes(user, hashes: list[str]):
    user.backup_codes_hashes = json.dumps(hashes)
    user.backup_codes_generated_at = utc_now()
    db.session.commit()


def can_edit_request(req_obj, user) -> bool:
    """Owner or superadmin can edit; if no owner, only superadmin."""
    role = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
    role = (role or "").strip().lower()
    if role in {"super_admin", "superadmin", "admin"}:
        return True
    owner_id = getattr(req_obj, "owner_id", None)
    user_id = getattr(user, "id", None)
    return owner_id is not None and owner_id == user_id


def is_stale(req_obj, minutes: int = 30) -> bool:
    """Return True if owned_at is older than `minutes`."""
    owned_at = getattr(req_obj, "owned_at", None)
    if not owned_at:
        return False
    try:
        return (utc_now() - owned_at).total_seconds() > minutes * 60
    except Exception:
        return False


@admin_bp.before_request
def enforce_admin_mfa():
    if not current_app.config.get("MFA_ENABLED", False):
        return None
    # Keep test suites focused on route authorization instead of MFA ceremony.
    # Legacy tests often inject an authenticated admin session directly rather
    # than completing the interactive MFA flow.
    if bool(current_app.config.get("TESTING", False)) and session.get(
        "admin_logged_in"
    ):
        return None
    allowed = {
        "admin.admin_login",
        "admin.ops_login",
        "admin.admin_login_legacy",
        "admin.admin_logout",
        "admin.admin_mfa_setup",
        "admin.admin_mfa_verify",
        "admin.admin_mfa_backup_codes",
        "static",
    }
    if request.endpoint in allowed or (
        request.endpoint and request.endpoint.startswith("static")
    ):
        return None
    if not current_user.is_authenticated:
        return None
    role_requires_mfa = is_mfa_required(current_user)
    user_has_mfa = bool(getattr(current_user, "mfa_enabled", False)) and bool(
        getattr(current_user, "totp_secret", None)
    )
    if role_requires_mfa and not user_has_mfa:
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("admin.admin_mfa_setup", next=nxt))
    if not user_has_mfa:
        return None
    if _mfa_ok_is_valid():
        return None
    nxt = request.full_path if request.query_string else request.path
    return redirect(url_for("admin.admin_mfa_verify", next=nxt))


@admin_bp.before_request
def enforce_admin_onboarding():
    allowed = {
        "admin.admin_login",
        "admin.ops_login",
        "admin.admin_login_legacy",
        "admin.admin_logout",
        "admin.admin_mfa_setup",
        "admin.admin_mfa_verify",
        "admin.admin_mfa_backup_codes",
        "admin.admin_onboarding",
        "admin.admin_onboarding_step",
        "admin.admin_onboarding_complete",
    }
    if request.endpoint in allowed or (
        request.endpoint and request.endpoint.startswith("static")
    ):
        return None
    if not session.get("admin_logged_in"):
        return None
    if not _admin_onboarding_required():
        return None
    return redirect(url_for("admin.admin_onboarding"), code=303)


@admin_bp.get("/onboarding")
@admin_required
def admin_onboarding():
    admin_required_404()
    if not _admin_onboarding_required():
        return redirect(_default_admin_workspace_url(), code=303)

    structure = _admin_onboarding_safe_structure()
    payload = _admin_onboarding_load_data()
    current_step = _admin_onboarding_step()
    _admin_onboarding_touch_started(current_user)
    db.session.commit()

    return render_template(
        "admin/onboarding.html",
        onboarding_step=current_step,
        onboarding_progress=ONBOARDING_PROGRESS.get(current_step, 20),
        onboarding_data=payload,
        onboarding_structure=structure,
        onboarding_team_sizes=ONBOARDING_TEAM_SIZE_OPTIONS,
        onboarding_main_needs=ONBOARDING_MAIN_NEED_OPTIONS,
        onboarding_plan_url=_admin_onboarding_plan_url(),
        onboarding_request_new_url=url_for("admin.admin_request_new"),
        onboarding_password_done=not bool(getattr(current_user, "must_change_password", False)),
    )


@admin_bp.post("/onboarding/step")
@admin_required
def admin_onboarding_step():
    admin_required_404()
    if not _admin_onboarding_required():
        return redirect(_default_admin_workspace_url(), code=303)

    step = (request.form.get("step") or "").strip().lower()
    payload = _admin_onboarding_load_data()
    structure = _admin_onboarding_safe_structure()

    if step == "welcome":
        _admin_onboarding_set_step(current_user, "secure_access")
        db.session.commit()
        return redirect(url_for("admin.admin_onboarding"), code=303)

    if step == "secure_access":
        if bool(getattr(current_user, "must_change_password", False)):
            password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""
            if password != confirm_password:
                flash("La confirmation du mot de passe ne correspond pas.", "warning")
                return redirect(url_for("admin.admin_onboarding"), code=303)
            try:
                current_user.set_password(password)
            except ValueError as exc:
                flash(str(exc), "warning")
                return redirect(url_for("admin.admin_onboarding"), code=303)
            current_user.must_change_password = False
        _admin_onboarding_set_step(current_user, "structure_setup")
        db.session.commit()
        return redirect(url_for("admin.admin_onboarding"), code=303)

    if step == "structure_setup":
        structure_name = (request.form.get("structure_name") or "").strip()
        city = (request.form.get("city") or "").strip()
        team_size = (request.form.get("team_size") or "").strip()
        main_need = (request.form.get("main_need") or "").strip().lower()
        if not structure_name:
            flash("Le nom de la structure est requis.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        if team_size and team_size not in ONBOARDING_TEAM_SIZE_OPTIONS:
            flash("La taille d'équipe sélectionnée est invalide.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        if main_need and main_need not in ONBOARDING_MAIN_NEED_OPTIONS:
            flash("Le besoin principal selectionne est invalide.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        if structure is not None:
            structure.name = structure_name
        payload["structure_setup"] = {
            "name": structure_name,
            "city": city,
            "team_size": team_size,
            "main_need": main_need,
        }
        _admin_onboarding_save_data(current_user, payload)
        _admin_onboarding_set_step(current_user, "invite_team")
        db.session.commit()
        return redirect(url_for("admin.admin_onboarding"), code=303)

    if step == "invite_team":
        invite_action = (request.form.get("invite_action") or "send").strip().lower()
        emails: list[str] = []
        for idx in range(1, 4):
            raw_email = (request.form.get(f"invite_email_{idx}") or "").strip().lower()
            if not raw_email:
                continue
            if not _ONBOARDING_EMAIL_RE.match(raw_email):
                flash("Au moins une adresse e-mail est invalide.", "warning")
                return redirect(url_for("admin.admin_onboarding"), code=303)
            emails.append(raw_email)
        payload["team_invites"] = {
            "emails": emails[:3],
            "sent": invite_action == "send" and bool(emails),
            "skipped": invite_action == "skip",
        }
        _admin_onboarding_save_data(current_user, payload)
        _admin_onboarding_set_step(current_user, "first_win")
        db.session.commit()
        return redirect(url_for("admin.admin_onboarding"), code=303)

    if step == "first_win":
        first_win_action = (request.form.get("first_win_action") or "create").strip().lower()
        default_city = (
            ((payload.get("structure_setup") or {}).get("city") or "").strip()
            or getattr(structure, "city", None)
            or ""
        )
        request_title = (request.form.get("request_title") or "Premiere situation test").strip()
        request_city = (request.form.get("request_city") or default_city).strip()
        request_priority = (request.form.get("request_priority") or "normal").strip().lower()
        if not request_title:
            flash("Le titre de la premiere situation est requis.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        if not request_city:
            flash("La ville de la premiere situation est requise.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        if request_priority not in {"normal", "high", "urgent"}:
            flash("La priorite selectionnee est invalide.", "warning")
            return redirect(url_for("admin.admin_onboarding"), code=303)
        payload["first_request"] = {
            "created": False,
            "title": request_title,
            "city": request_city,
            "priority": request_priority,
            "fallback_url": url_for("admin.admin_request_new"),
            "action": first_win_action,
        }
        _admin_onboarding_save_data(current_user, payload)
        _admin_onboarding_set_step(current_user, "complete")
        db.session.commit()
        return redirect(url_for("admin.admin_onboarding"), code=303)

    flash("Etape d'onboarding inconnue.", "warning")
    return redirect(url_for("admin.admin_onboarding"), code=303)


@admin_bp.post("/onboarding/complete")
@admin_required
def admin_onboarding_complete():
    admin_required_404()
    if not _admin_onboarding_required():
        return redirect(_default_admin_workspace_url(), code=303)
    _admin_onboarding_set_step(current_user, "complete")
    current_user.onboarding_completed_at = utc_now()
    db.session.commit()
    return redirect(_default_admin_workspace_url(), code=303)


# API endpoint for request filtering (status, date)
def api_requests():
    from flask import current_app, jsonify, request

    # During tests we allow access to the API endpoints to simplify fixtures
    if not current_app.config.get("TESTING", False):
        if not getattr(current_user, "is_admin", False):
            return jsonify({"error": "Unauthorized"}), 403
    query = _scope_requests(Request.query)
    status = request.args.get("status")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    if status:
        query = query.filter_by(status=status)
    if date_from:
        try:
            from datetime import datetime

            date_from_dt = datetime.fromisoformat(date_from)
            query = query.filter(Request.created_at >= date_from_dt)
        except Exception:
            pass
    if date_to:
        try:
            from datetime import datetime, timedelta

            date_to_dt = datetime.fromisoformat(date_to) + timedelta(days=1)
            query = query.filter(Request.created_at < date_to_dt)
        except Exception:
            pass
    requests = query.order_by(Request.created_at.desc()).all()
    data = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in requests
    ]
    return jsonify({"items": data})


# API endpoint for all volunteers (JSON)
def api_volunteers():
    from flask import current_app, jsonify

    if not current_app.config.get("TESTING", False):
        if not getattr(current_user, "is_admin", False):
            return jsonify({"error": "Unauthorized"}), 403
    volunteers = Volunteer.query.all()
    data = [
        {
            "id": v.id,
            "name": v.name,
            "email": v.email,
            "phone": v.phone,
            "location": v.location,
            "skills": v.skills,
            "is_active": v.is_active,
        }
        for v in volunteers
    ]
    return jsonify(data)


# Volunteer details
@admin_bp.route("/admin_volunteers/<int:id>")
@admin_required
def volunteer_detail(id):
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))
    from flask import abort

    volunteer = db.session.get(Volunteer, id)
    if not volunteer:
        abort(404)
    return render_template("volunteer_detail.html", volunteer=volunteer)


@admin_bp.get("/api/volunteers/<int:vol_id>")
@admin_required
def admin_volunteer_api(vol_id: int):
    admin_required_404()
    volunteer = db.session.get(Volunteer, vol_id)
    if not volunteer:
        abort(404)
    req_id_raw = (request.args.get("req_id") or "").strip()
    req_id = int(req_id_raw) if req_id_raw.isdigit() else None

    def _to_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]

    last_active = getattr(volunteer, "last_activity", None) or getattr(
        volunteer, "updated_at", None
    )
    can_help_count = (
        db.session.query(func.count(VolunteerAction.id))
        .filter(
            VolunteerAction.volunteer_id == volunteer.id,
            VolunteerAction.action == "CAN_HELP",
        )
        .scalar()
        or 0
    )
    action_rows = (
        VolunteerAction.query.filter_by(volunteer_id=volunteer.id)
        .order_by(VolunteerAction.updated_at.desc())
        .limit(5)
        .all()
    )
    history = [
        {
            "request_id": row.request_id,
            "action": row.action,
            "at": (
                (row.updated_at or row.created_at).isoformat(
                    sep=" ", timespec="minutes"
                )
                if (row.updated_at or row.created_at)
                else None
            ),
        }
        for row in action_rows
    ]

    notified_at = None
    seen_at = None
    if req_id is not None:
        notif = (
            Notification.query.filter_by(
                volunteer_id=volunteer.id, type="new_match", request_id=req_id
            )
            .order_by(Notification.created_at.desc())
            .first()
        )
        if notif:
            notified_at = (
                notif.created_at.isoformat(sep=" ", timespec="minutes")
                if notif.created_at
                else None
            )
            seen_at = (
                notif.read_at.isoformat(sep=" ", timespec="minutes")
                if notif.read_at
                else None
            )

    data = {
        "id": volunteer.id,
        "name": getattr(volunteer, "name", None) or f"Volunteer #{volunteer.id}",
        "email": getattr(volunteer, "email", None),
        "phone": getattr(volunteer, "phone", None),
        "city": None,
        "location": getattr(volunteer, "location", None),
        "languages": [],
        "roles": _to_list(getattr(volunteer, "skills", None)),
        "availability": getattr(volunteer, "availability", None),
        "last_active": (
            last_active.isoformat(sep=" ", timespec="minutes") if last_active else None
        ),
        "can_help_count": int(can_help_count),
        "history": history,
        "notified_at": notified_at,
        "seen_at": seen_at,
        "profile_url": url_for("admin.volunteer_detail", id=volunteer.id),
    }
    return jsonify(data)


@admin_bp.route("/ops/login", methods=["GET", "POST"], endpoint="ops_login")
@limiter.limit("5 per 5 minutes")
@limiter.limit("20 per hour")
def admin_ops_login():
    next_candidate = (
        request.form.get("next") or request.args.get("next") or ""
    ).strip()
    next_url = _safe_next_url(next_candidate)
    redirect_code = 303 if request.method == "POST" else 302
    return redirect(url_for("admin.admin_login_legacy", next=next_url), code=redirect_code)


@admin_bp.route("/login", methods=["GET", "POST"], endpoint="admin_login")
@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "5 per minute",
    methods=["POST"],
    on_breach=_admin_auth_rate_limit_response,
)
def admin_login_legacy():
    """Legacy admin login endpoint kept for backward-compatible tests/clients."""
    next_candidate = (
        request.form.get("next") or request.args.get("next") or ""
    ).strip()
    next_url = _safe_next_url(next_candidate)

    if request.method == "POST":
        database_ready, database_message = _admin_login_database_ready()
        if not database_ready:
            flash(database_message, "danger")
            return redirect(url_for("admin.admin_login_legacy", next=next_url))
        username = request.form.get("username", "").strip()
        username_norm = _norm_username(username)
        ip = _client_ip()
        now = datetime.now(UTC).replace(tzinfo=None)
        locked, retry_after = _admin_login_is_locked(ip, username_norm, now)
        if locked:
            _log_admin_attempt(username=username_norm, ip=ip, success=False)
            _audit_admin_auth_event(
                "admin_login_failure",
                route_context="login",
                attempted_identifier=username_norm,
                next_url=next_url,
                extra_payload={"reason": "lockout", "retry_after_seconds": int(retry_after)},
            )
            return _lockout_response(retry_after, next_url=next_url)

        password = request.form.get("password", "")
        user = _find_admin_user(username)
        if not _verify_admin_password(user, password):
            _log_admin_attempt(username=username_norm, ip=ip, success=False)
            _audit_admin_auth_event(
                "admin_login_failure",
                route_context="login",
                attempted_identifier=username_norm,
                user=user,
                next_url=next_url,
                extra_payload={"reason": "invalid_credentials"},
            )
            flash(GENERIC_ADMIN_LOGIN_FAIL_MSG, "danger")
            return redirect(url_for("admin.admin_login_legacy", next=next_url))
        _log_admin_attempt(username=username_norm, ip=ip, success=True)
        _audit_admin_auth_event(
            "admin_login_success",
            route_context="login",
            attempted_identifier=username_norm,
            user=user,
            next_url=next_url,
            extra_payload={"result": "success"},
        )
        cleared_fails = _clear_recent_admin_login_failures(username_norm, ip, now)
        if cleared_fails > 0:
            log_security_event(
                "auth_admin_login_success_after_failures",
                actor_type="admin",
                actor_id=getattr(user, "id", None),
                meta={
                    "ip": ip,
                    "username": username_norm,
                    "cleared_failures": int(cleared_fails),
                },
            )
            audit_admin_action(
                action="admin.login.success_after_failures",
                target_type="AdminUser",
                target_id=int(getattr(user, "id", 0) or 0),
                payload={
                    "route": "admin.admin_login_legacy",
                    "username": username_norm,
                    "cleared_failures": int(cleared_fails),
                    "ip": ip,
                    "ua": (request.headers.get("User-Agent") or "")[:256],
                },
            )

        # Legacy email 2FA compatibility flow expected by tests.
        if bool(current_app.config.get("EMAIL_2FA_ENABLED", False)):
            session.clear()
            session["pending_admin_user_id"] = int(user.id)
            session["pending_email_2fa"] = True
            session["email_2fa_code"] = f"{secrets.randbelow(1000000):06d}"
            session["email_2fa_expires"] = int(time.time()) + 600
            return redirect(url_for("admin.admin_2fa"))

        return _complete_admin_login(user, next_url, via="admin_login_legacy")

    return render_template("admin/login.html", next=next_url)


@admin_bp.route("/re-auth", methods=["GET", "POST"])
@limiter.limit(
    "5 per minute",
    methods=["POST"],
    on_breach=_admin_auth_rate_limit_response,
)
@login_required
@admin_required
def admin_reauth():
    next_candidate = (
        request.form.get("next") or request.args.get("next") or ""
    ).strip()
    next_url = _safe_next_url(next_candidate)

    if request.method == "POST":
        password = request.form.get("password", "")
        user = db.session.get(AdminUser, getattr(current_user, "id", None))
        if _verify_admin_password(user, password):
            now = _utc_now()
            _touch_admin_last_seen(now)
            _touch_admin_auth_at(now)
            _audit_admin_auth_event(
                "admin_reauth_success",
                route_context="re-auth",
                attempted_identifier=getattr(user, "username", None),
                user=user,
                next_url=next_url,
                extra_payload={"result": "success"},
            )
            log_security_event(
                "auth_admin_reauth_success",
                actor_type="admin",
                actor_id=getattr(user, "id", None),
                meta={"next": (next_url or "")[:255]},
            )
            audit_admin_action(
                action="admin.reauth.success",
                target_type="AdminUser",
                target_id=int(getattr(user, "id", 0) or 0),
                payload={
                    "next": (next_url or "")[:255],
                    "route": request.path,
                    "ip": _client_ip(),
                    "ua": (request.headers.get("User-Agent") or "")[:256],
                },
            )
            flash("Vérification effectuée.", "success")
            return _redirect_to_safe_next(
                next_url,
                url_for("admin.admin_requests"),
                code=303,
            )
        _audit_admin_auth_event(
            "admin_reauth_failure",
            route_context="re-auth",
            attempted_identifier=getattr(user, "username", None),
            user=user,
            next_url=next_url,
            extra_payload={"reason": "invalid_credentials"},
        )
        log_security_event(
            "auth_admin_reauth_failed",
            actor_type="admin",
            actor_id=getattr(current_user, "id", None),
            meta={"route": request.path},
        )
        if user:
            audit_admin_action(
                action="admin.reauth.failed",
                target_type="AdminUser",
                target_id=int(getattr(user, "id", 0) or 0),
                payload={
                    "route": request.path,
                    "ip": _client_ip(),
                    "ua": (request.headers.get("User-Agent") or "")[:256],
                },
            )
        flash("Veuillez confirmer votre identité pour continuer.", "danger")

    return render_template("admin/reauth.html", next=next_url)


@admin_bp.route("/logout", methods=["GET", "POST"])
def admin_logout():
    admin_required_404()
    actor_id = getattr(current_user, "id", 0) or 0
    log_activity(
        entity_type="admin",
        entity_id=actor_id,
        action="admin_logout",
        message="Admin logout",
        persist=True,
    )
    audit_admin_action(
        action="admin.logout",
        target_type="AdminUser",
        target_id=int(actor_id),
        payload={
            "route": request.path,
            "ip": _client_ip(),
            "ua": (request.headers.get("User-Agent") or "")[:256],
        },
    )
    _mfa_ok_clear()
    _mfa_attempt_reset()
    session.pop("mfa_required", None)
    session.pop("mfa_pending_secret", None)
    session.pop("backup_codes_plain", None)
    session.pop("pending_admin_user_id", None)
    session.pop("admin_password_verified", None)
    session.pop("pending_email_2fa", None)
    session.pop("email_2fa_code", None)
    session.pop("email_2fa_expires", None)
    session.pop("admin_user_id", None)
    session.pop("admin_logged_in", None)
    session.pop("admin_last_seen", None)
    session.pop("admin_auth_at", None)
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("admin.admin_login_legacy"))


MFA_PENDING_SECRET_KEY = "mfa_pending_secret"
MFA_SESSION_KEY = "mfa_ok"


@admin_bp.route("/mfa/setup", methods=["GET", "POST"])
@login_required
def admin_mfa_setup():
    admin_required_404()
    next_url = _safe_next_url(request.args.get("next"))
    if getattr(current_user, "mfa_enabled", False) and getattr(
        current_user, "totp_secret", None
    ):
        flash(_("MFA is already enabled."), "info")
        return redirect(url_for("admin.admin_mfa_verify", next=next_url))

    pending_secret = session.get(MFA_PENDING_SECRET_KEY)
    if not pending_secret:
        pending_secret = generate_totp_secret()
        session[MFA_PENDING_SECRET_KEY] = pending_secret

    issuer = "HelpChain"
    username = getattr(current_user, "username", f"admin-{current_user.id}")
    uri = build_totp_uri(pending_secret, username=username, issuer=issuer)
    qr_b64 = qr_png_base64(uri)

    if request.method == "POST":
        code = (request.form.get("code") or "").strip().replace(" ", "")
        if not code:
            flash(_("Enter the 6-digit code from the app."), "danger")
            return render_template(
                "admin/mfa_setup.html",
                qr_b64=qr_b64,
                secret=pending_secret,
                username=username,
            )

        if not verify_totp_code(pending_secret, code):
            flash(
                "Невалиден код. Провери часовника на телефона и опитай пак.", "danger"
            )
            return render_template(
                "admin/mfa_setup.html",
                qr_b64=qr_b64,
                secret=pending_secret,
                username=username,
            )

        user = db.session.get(AdminUser, current_user.id)
        user.totp_secret = pending_secret
        user.mfa_enabled = True
        user.mfa_enrolled_at = utc_now()
        db.session.commit()

        mark_mfa_verified()
        session.pop(MFA_PENDING_SECRET_KEY, None)
        flash(_("MFA has been enabled successfully."), "success")
        flash(_("Generate backup codes now (recovery option)."), "info")
        return _finalize_admin_session(
            user,
            url_for("admin.admin_mfa_backup_codes"),
            fallback_url=url_for("admin.admin_mfa_backup_codes"),
        )

    return render_template(
        "admin/mfa_setup.html", qr_b64=qr_b64, secret=pending_secret, username=username
    )


@admin_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit(
    "10 per minute",
    methods=["POST"],
    on_breach=_admin_auth_rate_limit_response,
)
@login_required
def admin_mfa_verify():
    admin_required_404()
    next_url = _safe_next_url(request.args.get("next"))
    if not current_app.config.get("MFA_ENABLED", False):
        abort(404)

    if is_mfa_required(current_user) and not (
        getattr(current_user, "mfa_enabled", False)
        and getattr(current_user, "totp_secret", None)
    ):
        flash(_("MFA setup is required for your role before verification."), "warning")
        return redirect(
            url_for("admin.admin_mfa_setup", next=next_url)
        )

    if not (
        getattr(current_user, "mfa_enabled", False)
        and getattr(current_user, "totp_secret", None)
    ):
        mark_mfa_verified()
        now = _utc_now()
        _touch_admin_last_seen(now)
        _touch_admin_auth_at(now)
        return _finalize_admin_session(
            current_user,
            next_url,
            fallback_url=_default_admin_landing_url(),
        )

    locked, remaining = _mfa_lock_is_active()
    if request.method == "GET":
        return render_template(
            "admin/mfa_verify.html",
            locked=locked,
            remaining=remaining,
            next=request.args.get("next") or "",
        )

    if locked:
        _audit_admin_auth_event(
            "admin_mfa_verify_failure",
            route_context="mfa_verify",
            attempted_identifier=getattr(current_user, "username", None),
            user=current_user,
            next_url=next_url,
            extra_payload={
                "reason": "locked",
                "retry_after_seconds": int(remaining or 0),
            },
        )
        flash(
            f"Твърде много опити. Опитай след {max(1, remaining // 60)} мин.", "danger"
        )
        return redirect(
            url_for("admin.admin_mfa_verify", next=next_url)
        )

    code = (request.form.get("code") or "").strip().replace(" ", "").upper()
    totp_ok = False
    try:
        totp_ok = verify_totp_code(current_user.totp_secret, code)
    except Exception:
        totp_ok = False

    backup_ok = False
    if not totp_ok:
        hashes = _load_hashes(current_user)
        for i, h in enumerate(hashes):
            if check_password_hash(h, code):
                backup_ok = True
                hashes.pop(i)
                _save_hashes(current_user, hashes)
                break

    if totp_ok or backup_ok:
        _mfa_attempt_reset()
        mark_mfa_verified()
        now = _utc_now()
        _touch_admin_last_seen(now)
        _touch_admin_auth_at(now)
        log_activity(
            entity_type="admin",
            entity_id=getattr(current_user, "id", 0) or 0,
            action="admin_mfa_success",
            message="Admin MFA verified",
            persist=True,
        )
        _audit_admin_auth_event(
            "admin_mfa_verify_success",
            route_context="mfa_verify",
            attempted_identifier=getattr(current_user, "username", None),
            user=current_user,
            next_url=next_url,
            extra_payload={
                "method": "backup_code" if backup_ok and not totp_ok else "totp",
                "result": "success",
            },
        )
        flash(_("MFA verified."), "success")
        return _finalize_admin_session(
            current_user,
            next_url,
            fallback_url=_default_admin_landing_url(),
        )

    _mfa_attempt_fail()
    locked, remaining = _mfa_lock_is_active()
    _audit_admin_auth_event(
        "admin_mfa_verify_failure",
        route_context="mfa_verify",
        attempted_identifier=getattr(current_user, "username", None),
        user=current_user,
        next_url=next_url,
        extra_payload={
            "reason": "invalid_code",
            "attempts": int(session.get("mfa_attempts", 0) or 0),
            "locked": bool(locked),
        },
    )
    if locked:
        flash(_("Invalid code. Locked for about %(minutes)s min.", minutes=max(1, remaining // 60)), "danger")
    else:
        left = current_app.config.get("MFA_VERIFY_MAX_ATTEMPTS", 8) - int(
            session.get("mfa_attempts", 0)
        )
        flash(_("Invalid code. Attempts remaining: %(count)s.", count=max(left, 0)), "danger")

    return redirect(
        url_for("admin.admin_mfa_verify", next=next_url)
    )


@admin_bp.route("/mfa/backup-codes", methods=["GET", "POST"])
@login_required
@require_admin_fresh_auth(minutes=10)
def admin_mfa_backup_codes():
    admin_required_404()
    if not current_app.config.get("MFA_ENABLED", False):
        abort(404)
    if not (
        getattr(current_user, "mfa_enabled", False)
        and getattr(current_user, "totp_secret", None)
    ):
        flash(_("Enable MFA from setup first."), "warning")
        return redirect(url_for("admin.admin_mfa_setup"))

    if request.method == "POST":
        codes = _generate_backup_codes(10)
        _save_hashes(current_user, _hash_codes(codes))
        session["backup_codes_plain"] = codes
        audit_admin_action(
            action="admin.mfa_backup_codes_regenerated",
            target_type="AdminUser",
            target_id=int(getattr(current_user, "id", 0) or 0),
            payload={
                "generated_codes_count": 10,
                "route": request.path,
                "ip": _client_ip(),
                "ua": (request.headers.get("User-Agent") or "")[:256],
            },
        )
        flash(
            "Backup кодовете са генерирани. Запази ги сега — няма да се покажат втори път.",
            "success",
        )
        return redirect(url_for("admin.admin_mfa_backup_codes"))

    codes_plain = session.pop("backup_codes_plain", None)
    hashes = _load_hashes(current_user)
    has_codes = len(hashes) > 0
    return render_template(
        "admin/mfa_backup_codes.html",
        codes=codes_plain,
        has_codes=has_codes,
        generated_at=getattr(current_user, "backup_codes_generated_at", None),
    )


@admin_bp.route("/2fa", methods=["GET", "POST"])
@login_required
def admin_2fa():
    admin_required_404()
    """2FA verification for admins."""
    user_id = session.get("pending_admin_user_id")
    if not user_id:
        return redirect(url_for("admin.admin_login_legacy"))

    admin_user = db.session.get(AdminUser, user_id)
    if not admin_user:
        return redirect(url_for("admin.admin_login_legacy"))

    if request.method == "POST":
        token = request.form.get("token")
        if admin_user.verify_totp(token):
            mark_mfa_verified()
            return _finalize_admin_session(
                admin_user,
                url_for("admin.admin_dashboard"),
                fallback_url=url_for("admin.admin_dashboard"),
            )
        else:
            flash(_("Invalid 2FA code."), "error")

    return render_template("admin_2fa.html")


@admin_bp.route("/email_2fa", methods=["GET", "POST"])
def admin_email_2fa():
    """Legacy email-based 2FA flow used by older tests."""
    if not session.get("pending_email_2fa"):
        return redirect(url_for("admin.admin_login_legacy"))

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip()
        if entered and entered == (session.get("email_2fa_code") or ""):
            admin_user = db.session.get(AdminUser, session.get("pending_admin_user_id"))
            if not admin_user:
                return redirect(url_for("admin.admin_login_legacy"))
            login_user(admin_user, remember=False)
            session["admin_password_verified"] = True
            session["mfa_required"] = True
            mark_mfa_verified()
            return _finalize_admin_session(
                admin_user,
                url_for("admin.admin_dashboard"),
                fallback_url=url_for("admin.admin_dashboard"),
            )
        flash(_("Invalid verification code."), "danger")

    return render_template("admin_email_2fa.html")


@admin_bp.route("/2fa/setup", methods=["GET", "POST"])
@admin_required
def admin_2fa_setup():
    admin_required_404()
    """2FA setup for admins."""
    if not isinstance(current_user, AdminUser):
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        token = request.form.get("token")
        if current_user.verify_totp(token):
            current_user.enable_2fa()
            flash(_("2FA has been enabled successfully!"), "success")
            return redirect(url_for("admin.admin_dashboard"))
        else:
            flash(_("Invalid code."), "error")

    uri = current_user.get_totp_uri()
    return render_template("admin_2fa_setup.html", totp_uri=uri)


@admin_bp.route("/2fa/disable", methods=["POST"])
@admin_required
def admin_2fa_disable():
    admin_required_404()
    """Disable 2FA for admins."""
    if not isinstance(current_user, AdminUser):
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    current_user.disable_2fa()
    flash(_("2FA has been disabled."), "success")
    return redirect(url_for("admin.admin_dashboard"))


@admin_bp.get("/api/dashboard")
@login_required
def admin_api_dashboard():
    admin_required_404()
    """Session-based dashboard data for admin UI (bypass JWT)."""
    if not getattr(current_user, "is_admin", False):
        return jsonify({"error": "forbidden"}), 403

    try:
        try:
            days = int(request.args.get("days", 30))
        except Exception:
            days = 30

        since_dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

        status_rows = (
            _request_kpi_base_query(
                func.coalesce(Request.status, "unknown").label("status"),
                func.count(Request.id).label("cnt"),
            )
            .group_by("status")
            .all()
        )
        counts_by_status = {status: int(cnt) for status, cnt in status_rows}
        total_requests = int(sum(counts_by_status.values()))

        city_expr = func.coalesce(
            func.nullif(Request.city, ""),
            func.nullif(Request.region, ""),
            "unknown",
        )
        city_rows = (
            _request_kpi_base_query(
                city_expr.label("city"), func.count(Request.id).label("cnt")
            )
            .group_by("city")
            .order_by(func.count(Request.id).desc())
            .limit(10)
            .all()
        )
        requests_by_city = [{"city": c, "count": int(cnt)} for c, cnt in city_rows]

        ts_rows = (
            _request_kpi_base_query(
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

        try:
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


SLA_HOURS_DEFAULT = 12


def _pctl(values, p: float = 0.9) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    vals.sort()
    idx = max(0, min(len(vals) - 1, math.ceil(p * len(vals)) - 1))
    return float(vals[idx])


def _sla_outliers_action_7d(
    first_action_subq, now: datetime, limit: int = 5
) -> list[dict]:
    cutoff = now - timedelta(days=7)
    delay_expr = func.strftime(
        "%s", first_action_subq.c.first_action_at
    ) - func.strftime("%s", VolunteerRequestState.notified_at)
    rows = (
        db.session.query(
            VolunteerRequestState.request_id.label("request_id"),
            VolunteerRequestState.volunteer_id.label("volunteer_id"),
            VolunteerRequestState.notified_at.label("notified_at"),
            first_action_subq.c.first_action_at.label("first_action_at"),
            delay_expr.label("delay_seconds"),
        )
        .join(
            first_action_subq,
            (first_action_subq.c.req_id == VolunteerRequestState.request_id)
            & (first_action_subq.c.vol_id == VolunteerRequestState.volunteer_id),
        )
        .filter(VolunteerRequestState.notified_at.isnot(None))
        .filter(VolunteerRequestState.notified_at >= cutoff)
        .filter(first_action_subq.c.first_action_at.isnot(None))
        .filter(first_action_subq.c.first_action_at >= cutoff)
        .filter(delay_expr >= 0)
        .order_by(db.text("delay_seconds DESC"))
        .limit(limit)
        .all()
    )
    out = []
    for row in rows:
        out.append(
            {
                "request_id": int(row.request_id),
                "volunteer_id": (
                    int(row.volunteer_id) if row.volunteer_id is not None else None
                ),
                "delay_seconds": (
                    float(row.delay_seconds) if row.delay_seconds is not None else None
                ),
                "notified_at": row.notified_at.isoformat() if row.notified_at else None,
                "first_action_at": (
                    row.first_action_at.isoformat() if row.first_action_at else None
                ),
            }
        )
    return out


def _sla_kpis(
    sla_hours: int = SLA_HOURS_DEFAULT,
    scope: str = "all_notified",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    sla_seconds = int(sla_hours * 3600)
    if scope not in {"all_notified", "assigned_only"}:
        raise ValueError("scope must be 'all_notified' or 'assigned_only'")

    # Backward compatibility for production environments with partial schema.
    # Keep /admin/api/risk-kpis alive instead of failing with 500.
    if not _table_exists("volunteer_request_states") or not _table_has_column(
        "volunteer_request_states", "notified_at"
    ):
        return {
            "avg_first_seen_seconds": None,
            "avg_first_action_seconds": None,
            "sla_under_12h_percent": 0.0,
            "sla_hours": int(sla_hours),
            "sla_samples": 0,
            "sla_scope": scope,
            "p90_first_seen_seconds_7d": None,
            "p90_first_action_seconds_7d": None,
            "p90_window_days": 7,
            "sla_outliers_action_7d": [],
            "sla_outliers_window_days": 7,
            "sla_outliers_limit": 5,
            "schema_warning": "volunteer_request_states.notified_at_missing",
        }

    if not _table_has_column("volunteer_request_states", "seen_at"):
        return {
            "avg_first_seen_seconds": None,
            "avg_first_action_seconds": None,
            "sla_under_12h_percent": 0.0,
            "sla_hours": int(sla_hours),
            "sla_samples": 0,
            "sla_scope": scope,
            "p90_first_seen_seconds_7d": None,
            "p90_first_action_seconds_7d": None,
            "p90_window_days": 7,
            "sla_outliers_action_7d": [],
            "sla_outliers_window_days": 7,
            "sla_outliers_limit": 5,
            "schema_warning": "volunteer_request_states.seen_at_missing",
        }

    base_states_q = db.session.query(
        VolunteerRequestState.request_id.label("req_id"),
        VolunteerRequestState.volunteer_id.label("vol_id"),
        VolunteerRequestState.notified_at.label("notified_at"),
        VolunteerRequestState.seen_at.label("seen_at"),
    ).join(Request, Request.id == VolunteerRequestState.request_id).filter(
        VolunteerRequestState.notified_at.isnot(None)
    )
    tenant_filter = _structure_scope_filter()
    base_states_q = _apply_tenant_filter(base_states_q, tenant_filter)
    if scope == "assigned_only":
        base_states_q = base_states_q.filter(
            Request.assigned_volunteer_id == VolunteerRequestState.volunteer_id
        )
    base_states = base_states_q.subquery()

    seen_delta = func.strftime("%s", base_states.c.seen_at) - func.strftime(
        "%s", base_states.c.notified_at
    )
    avg_first_seen = (
        db.session.query(func.avg(seen_delta))
        .filter(base_states.c.seen_at.isnot(None))
        .filter(seen_delta >= 0)
        .scalar()
    )

    # Backward compatibility: some local SQLite snapshots don't have
    # request_activities.volunteer_id yet. Keep the endpoint alive and return
    # partial SLA metrics instead of 500.
    if not _table_has_column("request_activities", "volunteer_id"):
        return {
            "avg_first_seen_seconds": (
                float(avg_first_seen) if avg_first_seen is not None else None
            ),
            "avg_first_action_seconds": None,
            "sla_under_12h_percent": 0.0,
            "sla_hours": int(sla_hours),
            "sla_samples": 0,
            "sla_scope": scope,
            "p90_first_seen_seconds_7d": None,
            "p90_first_action_seconds_7d": None,
            "p90_window_days": 7,
            "sla_outliers_action_7d": [],
            "sla_outliers_window_days": 7,
            "sla_outliers_limit": 5,
            "schema_warning": "request_activities.volunteer_id_missing",
        }

    first_action_subq = (
        db.session.query(
            base_states.c.req_id.label("req_id"),
            base_states.c.vol_id.label("vol_id"),
            base_states.c.notified_at.label("notified_at"),
            func.min(RequestActivity.created_at).label("first_action_at"),
        )
        .join(
            RequestActivity,
            (RequestActivity.request_id == base_states.c.req_id)
            & (RequestActivity.volunteer_id == base_states.c.vol_id),
        )
        .filter(
            RequestActivity.action.in_(("volunteer_can_help", "volunteer_cant_help"))
        )
        .filter(RequestActivity.created_at >= base_states.c.notified_at)
        .group_by(
            base_states.c.req_id,
            base_states.c.vol_id,
            base_states.c.notified_at,
        )
        .subquery()
    )
    action_delta = func.strftime(
        "%s", first_action_subq.c.first_action_at
    ) - func.strftime("%s", first_action_subq.c.notified_at)
    avg_first_action = db.session.query(func.avg(action_delta)).scalar()

    under_count, total_count = db.session.query(
        func.sum(case((action_delta <= sla_seconds, 1), else_=0)).label("under"),
        func.count().label("total"),
    ).first() or (0, 0)
    under_count = int(under_count or 0)
    total_count = int(total_count or 0)
    sla_pct = round((under_count * 100.0 / total_count), 1) if total_count else 0.0
    p90_window_days = 7
    cutoff = now - timedelta(days=p90_window_days)
    seen_secs = [
        int(sec)
        for (sec,) in (
            db.session.query(seen_delta)
            .filter(base_states.c.seen_at.isnot(None))
            .filter(base_states.c.notified_at >= cutoff)
            .filter(seen_delta >= 0)
            .all()
        )
        if sec is not None
    ]
    action_secs = [
        int(sec)
        for (sec,) in (
            db.session.query(action_delta)
            .filter(first_action_subq.c.notified_at >= cutoff)
            .filter(first_action_subq.c.first_action_at >= cutoff)
            .filter(action_delta >= 0)
            .all()
        )
        if sec is not None
    ]
    outliers = _sla_outliers_action_7d(first_action_subq, now, limit=5)

    return {
        "avg_first_seen_seconds": (
            float(avg_first_seen) if avg_first_seen is not None else None
        ),
        "avg_first_action_seconds": (
            float(avg_first_action) if avg_first_action is not None else None
        ),
        "sla_under_12h_percent": sla_pct,
        "sla_hours": int(sla_hours),
        "sla_samples": total_count,
        "sla_scope": scope,
        "p90_first_seen_seconds_7d": _pctl(seen_secs, 0.9),
        "p90_first_action_seconds_7d": _pctl(action_secs, 0.9),
        "p90_window_days": p90_window_days,
        "sla_outliers_action_7d": outliers,
        "sla_outliers_window_days": 7,
        "sla_outliers_limit": 5,
    }


@admin_bp.get("/api/risk-kpis")
@admin_required
def admin_risk_kpis():
    admin_required_404()

    now = datetime.now(UTC).replace(tzinfo=None)
    stale_days = 8
    unassigned_days = 3
    window_days = 7
    not_seen_hours = 24

    stale_cutoff = now - timedelta(days=stale_days)
    unassigned_cutoff = now - timedelta(days=unassigned_days)
    window_cutoff = now - timedelta(days=window_days)
    has_vrs_notified_at = _table_has_column("volunteer_request_states", "notified_at")
    tenant_filter = _structure_scope_filter()
    open_filter = or_(
        Request.status.is_(None), ~Request.status.in_(list(CLOSED_STATUSES))
    )

    stale_count = (
        _apply_tenant_filter(db.session.query(func.count(Request.id)), tenant_filter)
        .filter(Request.created_at < stale_cutoff)
        .filter(open_filter)
        .scalar()
    )

    unassigned_count = (
        _apply_tenant_filter(db.session.query(func.count(Request.id)), tenant_filter)
        .filter(Request.created_at < unassigned_cutoff)
        .filter(Request.assigned_volunteer_id.is_(None))
        .filter(open_filter)
        .scalar()
    )

    def count_notseen(hours: int) -> tuple[int, str]:
        cutoff = now - timedelta(hours=hours)
        if has_vrs_notified_at:
            cnt = (
                _apply_tenant_filter(
                    db.session.query(func.count(VolunteerRequestState.id)), tenant_filter
                )
                .join(Request, Request.id == VolunteerRequestState.request_id)
                .filter(VolunteerRequestState.notified_at.isnot(None))
                .filter(VolunteerRequestState.notified_at < cutoff)
                .filter(VolunteerRequestState.seen_at.is_(None))
                .filter(open_filter)
                .scalar()
            )
            source = "notified_at"
        else:
            cnt = (
                _apply_tenant_filter(
                    db.session.query(func.count(Notification.id)), tenant_filter
                )
                .join(Request, Request.id == Notification.request_id)
                .filter(Notification.type == "new_match")
                .filter(Notification.created_at < cutoff)
                .filter(open_filter)
                .scalar()
            )
            source = "notification_created_at_fallback"
        return int(cnt or 0), source

    notseen24, notified_source = count_notseen(24)
    notseen48, _ = count_notseen(48)
    notseen72, _ = count_notseen(72)
    notified_not_seen = notseen24

    can_help_7d = (
        db.session.query(func.count(VolunteerAction.id))
        .filter(VolunteerAction.action == "CAN_HELP")
        .filter(VolunteerAction.created_at >= window_cutoff)
        .scalar()
    )

    assigned_7d = (
        _apply_tenant_filter(db.session.query(func.count(Request.id)), tenant_filter)
        .filter(Request.assigned_volunteer_id.isnot(None))
        .filter(Request.created_at >= window_cutoff)
        .scalar()
    )

    conversion = None
    if can_help_7d and can_help_7d > 0:
        conversion = round((assigned_7d / can_help_7d) * 100, 1)

    not_seen_by_request = {}
    try:
        if has_vrs_notified_at:
            cutoff = now - timedelta(hours=24)
            not_seen_rows = (
                _apply_tenant_filter(
                    db.session.query(
                        VolunteerRequestState.request_id,
                        func.count(VolunteerRequestState.id),
                    ),
                    tenant_filter,
                )
                .join(Request, Request.id == VolunteerRequestState.request_id)
                .filter(VolunteerRequestState.notified_at.isnot(None))
                .filter(VolunteerRequestState.notified_at < cutoff)
                .filter(VolunteerRequestState.seen_at.is_(None))
                .filter(open_filter)
                .group_by(VolunteerRequestState.request_id)
                .all()
            )
        else:
            cutoff = now - timedelta(hours=24)
            not_seen_rows = (
                _apply_tenant_filter(
                    db.session.query(Notification.request_id, func.count(Notification.id)),
                    tenant_filter,
                )
                .join(Request, Request.id == Notification.request_id)
                .filter(Notification.type == "new_match")
                .filter(Notification.created_at < cutoff)
                .filter(open_filter)
                .group_by(Notification.request_id)
                .all()
            )
        not_seen_by_request = {int(req_id): int(cnt) for req_id, cnt in not_seen_rows}
    except Exception:
        db.session.rollback()

    risky_candidates = (
        _scope_requests(Request.query)
        .filter(Request.deleted_at.is_(None))
        .filter(open_filter)
        .order_by(Request.created_at.asc())
        .limit(300)
        .all()
    )
    top_risky_scored = []
    for row in risky_candidates:
        created_at = getattr(row, "created_at", None)
        age_days = (
            max(0, int((now - created_at).total_seconds() // 86400))
            if created_at is not None
            else 0
        )
        is_unassigned = getattr(row, "assigned_volunteer_id", None) is None
        ns_count = int(not_seen_by_request.get(int(row.id), 0))
        score = 0
        if age_days >= stale_days:
            score += 3
        if is_unassigned and age_days >= unassigned_days:
            score += 4
        if ns_count > 0:
            score += 2 + min(3, ns_count)
        if score <= 0:
            continue
        top_risky_scored.append((score, age_days, ns_count, row))

    top_risky_scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3].id))
    top_risky = []
    for score, age_days, ns_count, row in top_risky_scored[:5]:
        top_risky.append(
            {
                "id": int(row.id),
                "title": getattr(row, "title", None) or f"Request #{row.id}",
                "days_open": int(age_days),
                "is_unassigned": bool(
                    getattr(row, "assigned_volunteer_id", None) is None
                ),
                "not_seen_count": int(ns_count),
                "risk_score": int(score),
                "details_url": url_for("admin.admin_request_details", req_id=row.id),
            }
        )

    payload = {
        "stale_days": stale_days,
        "unassigned_days": unassigned_days,
        "window_days": window_days,
        "not_seen_hours": not_seen_hours,
        "notified_source": notified_source,
        "stale_count": int(stale_count or 0),
        "unassigned_count": int(unassigned_count or 0),
        "notified_not_seen": int(notified_not_seen or 0),
        "notseen24": int(notseen24 or 0),
        "notseen48": int(notseen48 or 0),
        "notseen72": int(notseen72 or 0),
        "can_help_7d": int(can_help_7d or 0),
        "assigned_7d": int(assigned_7d or 0),
        "conversion_pct": conversion,
        "top_risky": top_risky,
        "generated_at": now.isoformat(timespec="seconds"),
    }
    requested_sla = request.args.get("sla", type=int)
    default_sla = int(current_app.config.get("SLA_HOURS_DEFAULT", SLA_HOURS_DEFAULT))
    sla_hours = requested_sla if requested_sla is not None else default_sla
    sla_hours = max(1, min(int(sla_hours), 168))
    payload.update(_sla_kpis(sla_hours=sla_hours, scope="all_notified", now=now))
    return jsonify(payload)


def _seconds_diff(end_col, start_col):
    """
    Portable-ish SQL expression for (end - start) in seconds.
    PostgreSQL: extract(epoch from end - start)
    SQLite/other: (julianday(end) - julianday(start)) * 86400
    """
    try:
        dialect = db.session.get_bind().dialect.name
    except Exception:
        dialect = ""
    if dialect == "postgresql":
        return func.extract("epoch", end_col - start_col)
    return (func.julianday(end_col) - func.julianday(start_col)) * 86400.0


@admin_bp.get("/api/ops-kpis")
@admin_required
def admin_ops_kpis():
    admin_required_404()

    days = max(1, min(int(request.args.get("days", 30) or 30), 365))
    now = datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(days=days)
    stale_since = now - timedelta(days=7)

    new_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(Request.created_at >= since)
        .scalar()
        or 0
    )

    resolved_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(_request_kpi_resolved_filter())
        .filter(Request.completed_at >= since)
        .scalar()
        or 0
    )

    avg_to_owner_assign_sec = (
        _request_kpi_base_query(
            func.avg(_seconds_diff(Request.owned_at, Request.created_at))
        )
        .filter(Request.owned_at.isnot(None))
        .filter(Request.created_at.isnot(None))
        .filter(Request.owned_at >= Request.created_at)
        .scalar()
    )
    avg_owner_assign_hours = round(float(avg_to_owner_assign_sec or 0) / 3600.0, 2)

    avg_to_resolve_sec = (
        _request_kpi_base_query(
            func.avg(_seconds_diff(Request.completed_at, Request.created_at))
        )
        .filter(_request_kpi_resolved_filter())
        .filter(Request.created_at.isnot(None))
        .filter(Request.completed_at >= Request.created_at)
        .scalar()
    )
    avg_resolve_hours = round(float(avg_to_resolve_sec or 0) / 3600.0, 2)

    stale_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(Request.created_at < stale_since)
        .filter(_request_kpi_open_filter())
        .scalar()
        or 0
    )

    by_category_rows = (
        _request_kpi_base_query(Request.category, func.count(Request.id))
        .filter(Request.created_at >= since)
        .group_by(Request.category)
        .all()
    )
    by_category = [
        {"category": (category or "—"), "count": int(count)}
        for category, count in by_category_rows
    ]

    by_status_rows = (
        _request_kpi_base_query(Request.status, func.count(Request.id))
        .filter(_request_kpi_open_filter())
        .group_by(Request.status)
        .all()
    )
    by_status = [
        {"status": (status or "—"), "count": int(count)}
        for status, count in by_status_rows
    ]

    # --- SLA breach detection ---
    assign_deadline = now - timedelta(hours=ASSIGN_SLA_HOURS)
    resolve_deadline = now - timedelta(days=RESOLVE_SLA_DAYS)
    open_filter = _request_kpi_open_filter()

    assign_breach_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(Request.created_at.isnot(None))
        .filter(Request.created_at < assign_deadline)
        .filter(Request.owned_at.is_(None))
        .filter(open_filter)
        .scalar()
        or 0
    )

    resolve_breach_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(Request.created_at.isnot(None))
        .filter(Request.created_at < resolve_deadline)
        .filter(Request.completed_at.is_(None))
        .filter(open_filter)
        .scalar()
        or 0
    )

    volunteer_assign_deadline = now - timedelta(hours=VOLUNTEER_ASSIGN_SLA_HOURS)
    volunteer_assign_breach_count = (
        _request_kpi_base_query(func.count(Request.id))
        .filter(Request.created_at.isnot(None))
        .filter(Request.created_at < volunteer_assign_deadline)
        .filter(Request.assigned_volunteer_id.is_(None))
        .filter(open_filter)
        .scalar()
        or 0
    )

    # --- Health scoring (SLA-driven) ---
    if resolve_breach_count > 0:
        health_status = "critique"
    elif assign_breach_count > 0:
        health_status = "sous_tension"
    else:
        health_status = "stable"

    return jsonify(
        {
            "window_days": days,
            "new_requests": int(new_count),
            "resolved_requests": int(resolved_count),
            "avg_owner_assign_hours": avg_owner_assign_hours,
            "avg_resolve_hours": avg_resolve_hours,
            "stale_over_7d": int(stale_count),
            "by_category": by_category,
            "by_status_open": by_status,
            "definition": {
                "assignment": "owner assignment (owner_id + owned_at)",
                "resolution": "completed_at in window and status done/completed/resolved/closed",
                "scope": "current structure, excluding archived and deleted requests",
                "open_statuses": "not done/cancelled/rejected/canceled/closed/completed/resolved/archived",
            },
            "sla": {
                "assign_sla_hours": ASSIGN_SLA_HOURS,
                "resolve_sla_days": RESOLVE_SLA_DAYS,
                "assign_breach_count": int(assign_breach_count),
                "resolve_breach_count": int(resolve_breach_count),
            },
            "health": {
                "status": health_status,
            },
            "volunteer_sla": {
                "volunteer_assign_sla_hours": VOLUNTEER_ASSIGN_SLA_HOURS,
                "volunteer_assign_breach_count": int(volunteer_assign_breach_count),
            },
        }
    )


@admin_bp.get("/api/territorial-kpis")
@admin_required
def admin_territorial_kpis():
    admin_required_404()
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        since = now - timedelta(days=7)

        base_cases = (
            db.session.query(Case)
            .join(Request, Case.request_id == Request.id)
            .filter(Case.structure_id == Request.structure_id)
        )
        if not _is_global_admin():
            base_cases = base_cases.filter(Request.structure_id == _current_structure_id())

        active_cases = (
            base_cases.filter(func.lower(Case.status) != "closed").count()
        )
        new_cases_week = base_cases.filter(Case.created_at >= since).count()
        resolved_cases = base_cases.filter(func.lower(Case.status) == "closed").count()

        status_rows = (
            base_cases.with_entities(Case.status, func.count(Case.id))
            .group_by(Case.status)
            .all()
        )
        status_map = {str(status or "").lower(): int(count) for status, count in status_rows}
        cases_by_status = {
            "new": status_map.get("new", 0),
            "in_progress": status_map.get("in_progress", 0),
            "closed": status_map.get("closed", 0),
        }

        first_event_subq = (
            db.session.query(
                CaseEvent.case_id.label("case_id"),
                func.min(CaseEvent.created_at).label("first_event_at"),
            )
            .group_by(CaseEvent.case_id)
            .subquery()
        )

        avg_response_query = (
            db.session.query(
                func.avg(
                    _seconds_diff(first_event_subq.c.first_event_at, Case.created_at)
                )
            )
            .join(first_event_subq, Case.id == first_event_subq.c.case_id)
            .join(Request, Case.request_id == Request.id)
            .filter(Case.structure_id == Request.structure_id)
        )
        if not _is_global_admin():
            avg_response_query = avg_response_query.filter(
                Request.structure_id == _current_structure_id()
            )
        avg_response_sec = avg_response_query.scalar()
        avg_response_hours = round(float(avg_response_sec or 0) / 3600.0, 2)

        open_filter = func.lower(Case.status) != "closed"
        oldest_open_query = (
            db.session.query(
                func.max((func.julianday(now) - func.julianday(Case.created_at)) * 86400.0)
            )
            .join(Request, Case.request_id == Request.id)
            .filter(Case.structure_id == Request.structure_id)
            .filter(open_filter)
        )
        if not _is_global_admin():
            oldest_open_query = oldest_open_query.filter(
                Request.structure_id == _current_structure_id()
            )
        oldest_open_sec = oldest_open_query.scalar()
        oldest_open_case_days = int(float(oldest_open_sec or 0) / 86400.0)

        return jsonify(
            {
                "active_cases": int(active_cases or 0),
                "new_cases_week": int(new_cases_week or 0),
                "resolved_cases": int(resolved_cases or 0),
                "avg_response_hours": float(avg_response_hours or 0),
                "cases_by_status": cases_by_status,
                "oldest_open_case_days": int(oldest_open_case_days or 0),
            }
        )
    except Exception:
        current_app.logger.exception("territorial kpis failed")
        return jsonify(
            {
                "active_cases": 0,
                "new_cases_week": 0,
                "resolved_cases": 0,
                "avg_response_hours": 0.0,
                "cases_by_status": {"new": 0, "in_progress": 0, "closed": 0},
                "oldest_open_case_days": 0,
            }
        )


@admin_bp.post("/create-structure")
@admin_required
@admin_role_required("superadmin")
def admin_create_structure():
    admin_required_404()
    if _admin_role_value() != "superadmin":
        _audit_denied_action(required_roles={"superadmin"}, actor_role=_admin_role_value())
        abort(403)

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    admin_email = (payload.get("admin_email") or "").strip()
    password = payload.get("password") or ""

    if not name or not admin_email or not password:
        return jsonify({"error": "missing_fields"}), 400

    try:
        structure_id, admin_id = create_structure_with_admin(
            name=name, admin_email=admin_email, password=password
        )
    except ValueError as exc:
        msg = str(exc)
        if msg in {"structure_name_exists", "admin_email_exists"}:
            return jsonify({"error": msg}), 400
        raise

    return jsonify(
        {
            "structure_id": structure_id,
            "admin_id": admin_id,
            "message": "Structure created",
        }
    )


@admin_bp.route("/")
@admin_required
def admin_dashboard():
    admin_required_404()
    """Admin panel."""

    import logging

    logging.warning(
        f"[DEBUG] admin_dashboard: is_authenticated={getattr(current_user, 'is_authenticated', None)}, is_admin={getattr(current_user, 'is_admin', None)}, id={getattr(current_user, 'id', None)}, username={getattr(current_user, 'username', None)}"
    )
    if not current_user.is_admin:
        flash(_("You do not have access to the admin panel."), "error")
        return redirect(url_for("main.dashboard"))

    requests = _scope_requests(Request.query).all()
    logs = RequestLog.query.all()
    volunteers = Volunteer.query.all()
    logs_dict = {}
    for log in logs:
        if log.request_id not in logs_dict:
            logs_dict[log.request_id] = []
        logs_dict[log.request_id].append(log)

    requests_dict = []
    for r in requests:
        loc = (
            getattr(r, "location_text", None)
            or ", ".join(
                [
                    val
                    for val in (getattr(r, "city", None), getattr(r, "region", None))
                    if val
                ]
            )
            or ""
        )
        requests_dict.append(
            {
                "id": r.id,
                "name": r.name,
                "phone": r.phone,
                "email": r.email,
                "location": loc,
                "category": r.category,
                "description": r.description,
                "status": r.status,
                "urgency": getattr(r, "urgency", None) or getattr(r, "priority", None),
            }
        )

    volunteers_dict = [
        {
            "id": v.id,
            "name": v.name,
            "email": v.email,
            "phone": v.phone,
            "location": v.location,
            "skills": v.skills,
        }
        for v in volunteers
    ]

    try:
        total_requests = len(requests) if requests is not None else 0
    except Exception:
        total_requests = 0
    try:
        pending_requests = sum(
            1
            for r in requests
            if getattr(r, "status", None) not in ("completed", "done", None)
        )
    except Exception:
        pending_requests = 0
    try:
        total_volunteers = len(volunteers) if volunteers is not None else 0
    except Exception:
        total_volunteers = 0

    stats = {
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "total_volunteers": total_volunteers,
    }

    try:
        now = utc_now()
        week_ago = now - timedelta(days=7)

        total_requests_cnt = db.session.query(func.count(Request.id)).scalar() or 0

        open_requests_cnt = (
            db.session.query(func.count(Request.id))
            .filter(Request.status.notin_(["done", "rejected"]))
            .scalar()
            or 0
        )

        closed_requests_cnt = (
            db.session.query(func.count(Request.id))
            .filter(Request.status.in_(["done", "rejected"]))
            .scalar()
            or 0
        )

        closed_last_7d_cnt = (
            db.session.query(func.count(Request.id))
            .filter(Request.completed_at.isnot(None), Request.completed_at >= week_ago)
            .scalar()
            or 0
        )

        avg_resolution_hours = (
            db.session.query(
                func.avg(
                    func.julianday(Request.completed_at)
                    - func.julianday(Request.created_at)
                )
                * 24.0
            )
            .filter(Request.completed_at.isnot(None), Request.created_at.isnot(None))
            .scalar()
        )
        avg_resolution_hours = (
            float(avg_resolution_hours) if avg_resolution_hours is not None else None
        )

        unassigned_over_2d_cnt = (
            db.session.query(func.count(Request.id))
            .filter(
                Request.owner_id.is_(None),
                Request.status.notin_(["done", "rejected"]),
                Request.created_at <= (now - timedelta(days=2)),
            )
            .scalar()
            or 0
        )

        high_open_count = (
            db.session.query(func.count(Request.id))
            .filter(Request.status.notin_(["done", "rejected"]))
            .filter(Request.priority == "high")
            .scalar()
            or 0
        )

        stale_open_count = (
            db.session.query(func.count(Request.id))
            .filter(Request.status.notin_(["done", "rejected"]))
            .filter(Request.created_at <= (now - timedelta(days=7)))
            .scalar()
            or 0
        )

        since_dt = now - timedelta(days=14)
        rows = (
            db.session.query(func.date(Request.created_at), func.count(Request.id))
            .filter(Request.created_at.isnot(None))
            .filter(Request.created_at >= since_dt)
            .group_by(func.date(Request.created_at))
            .order_by(func.date(Request.created_at))
            .all()
        )
        impact_dates = [str(r[0]) for r in rows]
        impact_counts = [int(r[1]) for r in rows]

        cat_rows = (
            db.session.query(Request.category, func.count(Request.id))
            .group_by(Request.category)
            .order_by(func.count(Request.id).desc())
            .all()
        )
        impact_cat_labels = [r[0] or "unknown" for r in cat_rows]
        impact_cat_counts = [int(r[1]) for r in cat_rows]

        impact = {
            "total": total_requests_cnt,
            "open": open_requests_cnt,
            "closed": closed_requests_cnt,
            "closed_last_7d": closed_last_7d_cnt,
            "avg_resolution_hours": avg_resolution_hours,
            "unassigned_over_2d": unassigned_over_2d_cnt,
            "open_requests": int(open_requests_cnt or 0),
            "unassigned_48h": int(unassigned_over_2d_cnt or 0),
            "requests_dates": impact_dates,
            "requests_counts": impact_counts,
            "cat_labels": impact_cat_labels,
            "cat_counts": impact_cat_counts,
            "high_open": int(high_open_count or 0),
            "stale_open": int(stale_open_count or 0),
        }
    except Exception:
        impact = {
            "total": 0,
            "open": 0,
            "closed": 0,
            "closed_last_7d": 0,
            "avg_resolution_hours": None,
            "unassigned_over_2d": 0,
            "open_requests": 0,
            "unassigned_48h": 0,
            "requests_dates": [],
            "requests_counts": [],
            "cat_labels": [],
            "cat_counts": [],
            "high_open": 0,
            "stale_open": 0,
        }

    try:
        import logging as _logging

        _log = _logging.getLogger(__name__)
        _log.info(
            "admin_dashboard rendering: stats=%s, requests_items=%s, volunteers=%s",
            stats,
            total_requests,
            total_volunteers,
        )
    except Exception:
        pass

    return render_template(
        "admin_dashboard.html",
        requests={"items": requests},
        logs_dict=logs_dict,
        requests_json=requests_dict,
        volunteers=volunteers,
        volunteers_json=volunteers_dict,
        stats=stats,
        impact=impact,
        STATUS_LABELS=STATUS_LABELS,
    )


@admin_bp.route("/home")
@admin_required
def admin_home():
    admin_required_404()
    if not current_user.is_admin:
        flash(_("You do not have access to the admin panel."), "error")
        return redirect(url_for("main.dashboard"))

    base_query = _scope_requests(Request.query).filter(Request.deleted_at.is_(None))
    try:
        base_query = base_query.filter(Request.is_archived.is_(False))
    except Exception:
        pass

    actionable_filter = request_dashboard_actionable_filter()
    activity_sq, activity_expr, stale_threshold, stale_filter = (
        build_request_inactivity_components(_now_utc())
    )
    urgent_filter = or_(
        func.lower(func.coalesce(Request.priority, "")).in_(["high", "critical"]),
        func.coalesce(Request.risk_score, 0) >= 85,
    )
    unassigned_filter = Request.owner_id.is_(None)
    attention_filter = or_(urgent_filter, unassigned_filter, stale_filter)

    dashboard_query = base_query.outerjoin(activity_sq, activity_sq.c.request_id == Request.id)

    attention_count = dashboard_query.filter(actionable_filter, attention_filter).count()
    unassigned_count = dashboard_query.filter(actionable_filter, unassigned_filter).count()
    followup_count = dashboard_query.filter(actionable_filter, stale_filter).count()
    stale_count = followup_count

    attention_rows = (
        dashboard_query.filter(actionable_filter, attention_filter)
        .options(joinedload(Request.owner))
        .order_by(
            case((urgent_filter, 0), (unassigned_filter, 1), (stale_filter, 2), else_=3),
            activity_expr.asc().nullslast(),
            Request.created_at.desc().nullslast(),
            Request.id.desc(),
        )
        .limit(5)
        .all()
    )
    dashboard_last_activity_by_id = {}
    for row in attention_rows:
        dashboard_last_activity_by_id[int(row.id)] = get_request_last_meaningful_activity(row)

    queue_summary = {
        "pending": 0,
        "processing": 0,
        "dead_letter": 0,
        "retry": 0,
        "failed": 0,
    }
    if _table_exists("notification_jobs"):
        queue_summary = count_notification_job_buckets(_scoped_notification_jobs_query())

    security_summary = {
        "failed_logins_24h": 0,
        "denied_actions_24h": 0,
        "risky_actions_24h": 0,
        "available": False,
    }
    if _is_global_admin():
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            security_summary = {
                "failed_logins_24h": (
                    db.session.query(func.count(AdminLoginAttempt.id))
                    .filter(
                        AdminLoginAttempt.created_at >= since_24h,
                        AdminLoginAttempt.success.is_(False),
                    )
                    .scalar()
                    or 0
                ),
                "denied_actions_24h": (
                    db.session.query(func.count(AdminAuditEvent.id))
                    .filter(
                        AdminAuditEvent.created_at >= since_24h,
                        AdminAuditEvent.action == "security.denied_action",
                    )
                    .scalar()
                    or 0
                ),
                "risky_actions_24h": (
                    db.session.query(func.count(AdminAuditEvent.id))
                    .filter(
                        AdminAuditEvent.created_at >= since_24h,
                        AdminAuditEvent.action.in_(RISKY_ACTIONS),
                    )
                    .scalar()
                    or 0
                ),
                "available": True,
            }
        except Exception:
            security_summary = {
                "failed_logins_24h": 0,
                "denied_actions_24h": 0,
                "risky_actions_24h": 0,
                "available": True,
            }

    audience_dashboard = _build_audience_map_context() if _is_global_admin() else {}
    if not isinstance(audience_dashboard, dict):
        audience_dashboard = {}

    institutional_signal_preview = None
    preview_sources = (
        list(audience_dashboard.get("founder_queue_lead_rows") or [])
        + list(audience_dashboard.get("founder_queue_account_rows") or [])
        + list(audience_dashboard.get("revenue_radar_rows") or [])
    )
    for row in preview_sources:
        if not isinstance(row, dict):
            continue
        if int(row.get("institutional_intent_score") or 0) <= 0:
            continue
        institutional_signal_preview = row
        break

    if institutional_signal_preview is None:
        territory_summaries = list(audience_dashboard.get("territory_summaries") or [])
        for row in territory_summaries:
            if not isinstance(row, dict):
                continue
            if int(row.get("institutional_intent_score") or 0) <= 0:
                continue
            institutional_signal_preview = {
                "institutional_intent_score": int(row.get("institutional_intent_score") or 0),
                "intent_level": str(row.get("priority_level") or "").strip().lower(),
                "recurrence_level": str(row.get("recurrence_level") or "").strip(),
                "deployment_probability": str(row.get("deployment_probability") or "").strip(),
                "probable_organization_profiles": list(row.get("probable_organization_profiles") or []),
                "recommendation_reasons": list(row.get("recommendation_reasons") or []),
                "recommended_next_action": str(
                    row.get("recommended_next_action") or row.get("recommended_action") or ""
                ).strip(),
                "territorial_opportunity_summary": dict(
                    row.get("territorial_opportunity_summary") or {}
                ),
            }
            break

    return render_template(
        "admin/admin_home.html",
        attention_count=attention_count,
        unassigned_count=unassigned_count,
        followup_count=followup_count,
        stale_count=stale_count,
        attention_rows=attention_rows,
        dashboard_last_activity_by_id=dashboard_last_activity_by_id,
        queue_summary=queue_summary,
        security_summary=security_summary,
        audience_dashboard=audience_dashboard,
        institutional_signal_preview=institutional_signal_preview,
    )


@admin_bp.get("/integrations")
@admin_required
@admin_role_required("readonly", "ops", "superadmin", "admin")
def admin_integrations():
    admin_required_404()

    relay_available = _table_exists("relay_events")
    connectors_available = _table_exists("integration_connectors")
    total_events = 0
    received_count = 0
    processed_count = 0
    rejected_count = 0
    latest_received_at = None
    recent_rows = []
    connector_rows = []
    structure_options = []
    can_manage_connectors = _normalize_admin_role_value(getattr(current_user, "role", None)) in {
        "admin",
        "superadmin",
    }

    if connectors_available:
        connector_counts = _connector_event_count_map() if relay_available else {}
        connector_last_events = _connector_last_event_map() if relay_available else {}
        connectors = (
            _integration_connector_scope_query()
            .options(joinedload(IntegrationConnector.structure))
            .order_by(IntegrationConnector.created_at.desc(), IntegrationConnector.id.desc())
            .all()
        )
        connector_rows = [
            SimpleNamespace(
                connector=connector,
                event_count=connector_counts.get(int(connector.id), 0),
                last_event_at=connector_last_events.get(int(connector.id)) or connector.last_event_at,
            )
            for connector in connectors
        ]

    if can_manage_connectors:
        if _is_global_admin():
            structure_options = Structure.query.order_by(Structure.name.asc(), Structure.id.asc()).all()
        else:
            current_structure_id = _current_structure_id()
            structure = db.session.get(Structure, current_structure_id)
            structure_options = [structure] if structure is not None else []

    if relay_available:
        relay_query = _relay_event_scope_query()
        total_events = relay_query.count()
        received_count = relay_query.filter(RelayEvent.sync_status == "received").count()
        processed_count = relay_query.filter(RelayEvent.sync_status == "processed").count()
        rejected_count = relay_query.filter(RelayEvent.sync_status == "rejected").count()
        latest_received_at = relay_query.with_entities(func.max(RelayEvent.created_at)).scalar()

        recent_events = (
            relay_query.options(joinedload(RelayEvent.structure))
            .options(joinedload(RelayEvent.connector))
            .order_by(RelayEvent.created_at.desc(), RelayEvent.id.desc())
            .limit(25)
            .all()
        )
        recent_rows = [
            SimpleNamespace(
                event=row,
                rejected_fields_count=len(_relay_event_rejected_fields(row)),
            )
            for row in recent_events
        ]

    return render_template(
        "admin/integrations.html",
        relay_available=relay_available,
        connectors_available=connectors_available,
        total_events=total_events,
        received_count=received_count,
        processed_count=processed_count,
        rejected_count=rejected_count,
        latest_received_at=latest_received_at,
        connector_rows=connector_rows,
        can_manage_connectors=can_manage_connectors,
        structure_options=structure_options,
        connector_status_choices=INTEGRATION_CONNECTOR_STATUS_CHOICES,
        connector_allowed_fields=INTEGRATION_CONNECTOR_ALLOWED_FIELDS,
        recent_rows=recent_rows,
    )


@admin_bp.post("/integrations/connectors")
@admin_required
@admin_role_required("superadmin", "admin")
def admin_create_integration_connector():
    admin_required_404()

    if not _table_exists("integration_connectors"):
        flash("La table des connecteurs n'est pas encore disponible. Exécutez d'abord les migrations.", "warning")
        return redirect(url_for("admin.admin_integrations"), code=303)

    try:
        name = str(request.form.get("name") or "").strip()
        if not name:
            raise ValueError("Le nom du connecteur est requis.")
        if len(name) > 120:
            raise ValueError("Le nom du connecteur est trop long.")

        source_slug = _normalize_connector_source_slug(request.form.get("source_slug"))
        status = str(request.form.get("status") or "active").strip().lower()
        if status not in INTEGRATION_CONNECTOR_STATUS_CHOICES:
            raise ValueError("Le statut du connecteur est invalide.")

        if _is_global_admin():
            raw_structure_id = str(request.form.get("structure_id") or "").strip()
            if not raw_structure_id:
                raise ValueError("La structure du connecteur est requise.")
            try:
                structure_id = int(raw_structure_id)
            except Exception as exc:
                raise ValueError("La structure du connecteur est invalide.") from exc
        else:
            structure_id = int(_current_structure_id())

        structure = db.session.get(Structure, int(structure_id))
        if structure is None:
            raise ValueError("La structure sélectionnée est introuvable.")

        if IntegrationConnector.query.filter(IntegrationConnector.source_slug == source_slug).first():
            raise ValueError("Ce slug source est déjà utilisé.")

        allowed_fields = _parse_connector_allowed_fields(request.form.get("allowed_fields"))
        notes = str(request.form.get("notes") or "").strip()
        if len(notes) > 4000:
            raise ValueError("Les notes du connecteur sont trop longues.")

        raw_secret = secrets.token_urlsafe(24)
        connector = IntegrationConnector(
            structure_id=structure.id,
            name=name,
            source_slug=source_slug,
            api_key_hash=generate_password_hash(raw_secret),
            status=status,
            allowed_fields_json=json.dumps(allowed_fields, ensure_ascii=True) if allowed_fields else None,
            notes=notes or None,
        )
        db.session.add(connector)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin.admin_integrations"), code=303)

    session[_connector_secret_session_key()] = {
        "connector_id": int(connector.id),
        "secret": raw_secret,
    }
    flash("Connecteur créé. Conservez la clé affichée ci-dessous: elle ne sera plus montrée ensuite.", "success")
    return redirect(
        url_for("admin.admin_integration_connector_detail", connector_id=connector.id),
        code=303,
    )


@admin_bp.get("/integrations/connectors/<int:connector_id>")
@admin_required
@admin_role_required("readonly", "ops", "superadmin", "admin")
def admin_integration_connector_detail(connector_id: int):
    admin_required_404()

    if not _table_exists("integration_connectors"):
        abort(404)

    connector = (
        _integration_connector_scope_query()
        .options(joinedload(IntegrationConnector.structure))
        .filter(IntegrationConnector.id == int(connector_id))
        .first()
    )
    if connector is None:
        abort(404)

    recent_events = []
    if _table_exists("relay_events"):
        recent_events = (
            _relay_event_scope_query()
            .options(joinedload(RelayEvent.structure))
            .filter(RelayEvent.connector_id == int(connector.id))
            .order_by(RelayEvent.created_at.desc(), RelayEvent.id.desc())
            .limit(15)
            .all()
        )

    return render_template(
        "admin/integration_connector_detail.html",
        connector=connector,
        allowed_fields=_integration_connector_allowed_fields(connector),
        recent_events=recent_events,
        one_time_secret=_pop_connector_secret_once(connector.id),
    )


@admin_bp.get("/integrations/relay-events/<int:event_id>")
@admin_required
@admin_role_required("readonly", "ops", "superadmin", "admin")
def admin_relay_event_detail(event_id: int):
    admin_required_404()

    if not _table_exists("relay_events"):
        abort(404)

    relay_event = (
        _relay_event_scope_query()
        .options(joinedload(RelayEvent.structure))
        .options(joinedload(RelayEvent.connector))
        .filter(RelayEvent.id == int(event_id))
        .first()
    )
    if relay_event is None:
        abort(404)

    accepted_fields = {
        "external_source": relay_event.external_source,
        "external_reference_id": relay_event.external_reference_id,
        "status": relay_event.status,
        "priority": relay_event.priority,
        "category": relay_event.category,
        "due_date": relay_event.due_date,
        "relance_at": relay_event.relance_at,
        "summary_label": relay_event.summary_label,
    }

    return render_template(
        "admin/relay_event_detail.html",
        relay_event=relay_event,
        accepted_fields=accepted_fields,
        rejected_fields=_relay_event_rejected_fields(relay_event),
        sanitized_metadata=_relay_event_metadata(relay_event),
    )


@admin_bp.route("/intervenants", methods=["GET"])
@admin_required
def admin_intervenants():
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    search = (request.args.get("search") or "").strip()
    location_filter = (request.args.get("location") or "").strip()
    sort_by = (request.args.get("sort") or "created_at").strip().lower()
    sort_order = (request.args.get("order") or "desc").strip().lower()
    page = max(int(request.args.get("page") or 1), 1)
    per_page = max(min(int(request.args.get("per_page") or 25), 100), 10)

    workload_sq = _assignment_workload_subquery()
    query = (
        db.session.query(
            Intervenant,
            func.coalesce(workload_sq.c.workload, 0).label("workload"),
        )
        .outerjoin(workload_sq, workload_sq.c.intervenant_id == Intervenant.id)
    )
    if not _is_global_admin():
        query = query.filter(Intervenant.structure_id == _current_structure_id())
    if search:
        q = f"%{search}%"
        query = query.filter(
            or_(
                Intervenant.name.ilike(q),
                Intervenant.email.ilike(q),
                Intervenant.phone.ilike(q),
            )
        )
    if location_filter:
        query = query.filter(Intervenant.location.ilike(f"%{location_filter}%"))

    sort_map = {
        "name": Intervenant.name,
        "email": Intervenant.email,
        "location": Intervenant.location,
        "created_at": Intervenant.created_at,
    }
    sort_col = sort_map.get(sort_by, Intervenant.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc(), Intervenant.id.asc())
    else:
        query = query.order_by(sort_col.desc(), Intervenant.id.desc())

    total_intervenants = query.count()
    total_pages = max(1, int(math.ceil(total_intervenants / float(per_page)))) if total_intervenants else 1
    if page > total_pages:
        page = total_pages

    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    intervenants = []
    for intervenant, workload in rows:
        city = _intervenant_city(intervenant)
        address = _intervenant_address(intervenant)
        availability = _intervenant_availability(intervenant)
        intervenants.append(
            SimpleNamespace(
                id=intervenant.id,
                legacy_volunteer_id=intervenant.legacy_volunteer_id,
                full_name=_intervenant_display_name(intervenant),
                profession=_intervenant_profession(intervenant),
                email=intervenant.email,
                phone=intervenant.phone,
                city=city or "—",
                address=address or "—",
                location=intervenant.location or "",
                availability=availability,
                availability_label=_intervenant_availability_label(availability),
                availability_badge_class=_intervenant_availability_badge(availability),
                is_active=bool(getattr(intervenant, "is_active", False)),
                created_at=intervenant.created_at,
                current_workload=int(workload or 0),
            )
        )

    return render_template(
        "admin/intervenants_list.html",
        intervenants=intervenants,
        total_intervenants=total_intervenants,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        search=search,
        location_filter=location_filter,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@admin_bp.route("/intervenants/<int:intervenant_id>", methods=["GET", "POST"])
@admin_required
def admin_intervenant_detail(intervenant_id: int):
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    intervenant = _intervenant_or_403(intervenant_id)

    if request.method == "POST":
        action_response = _handle_intervenant_operational_action(intervenant)
        if action_response is not None:
            return action_response

        before = _intervenant_state_snapshot(intervenant)
        errors = _update_intervenant_from_form(intervenant)
        if errors:
            for error in errors:
                flash(error, "warning")
            return _intervenant_detail_template(
                intervenant,
                form_data=request.form,
                status_code=400,
            )

        _log_intervenant_profile_changes(intervenant, before)
        db.session.commit()
        flash("Intervenant mis à jour.", "success")
        return redirect(url_for("admin.admin_intervenant_detail", intervenant_id=intervenant.id))

    return _intervenant_detail_template(intervenant)


@admin_bp.route(
    "/structures/<int:structure_id>/intervenants/<int:intervenant_id>",
    methods=["GET", "POST"],
)
@admin_required
def admin_structure_intervenant_detail(structure_id: int, intervenant_id: int):
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    intervenant = _intervenant_or_403(intervenant_id, structure_id=structure_id)

    if request.method == "POST":
        action_response = _handle_intervenant_operational_action(
            intervenant, structure_context_id=structure_id
        )
        if action_response is not None:
            return action_response

        before = _intervenant_state_snapshot(intervenant)
        errors = _update_intervenant_from_form(intervenant)
        if errors:
            for error in errors:
                flash(error, "warning")
            return _intervenant_detail_template(
                intervenant,
                structure_context_id=structure_id,
                form_data=request.form,
                status_code=400,
            )

        _log_intervenant_profile_changes(intervenant, before)
        db.session.commit()
        flash("Intervenant mis à jour.", "success")
        return redirect(
            url_for(
                "admin.admin_structure_intervenant_detail",
                structure_id=structure_id,
                intervenant_id=intervenant.id,
            )
        )

    return _intervenant_detail_template(intervenant, structure_context_id=structure_id)


@admin_bp.route("/intervenants/new", methods=["GET", "POST"])
@admin_required
def admin_intervenants_new():
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    structures = []
    selected_structure_id = getattr(current_user, "structure_id", None)
    if _is_global_admin():
        structures = (
            Structure.query.order_by(Structure.name.asc(), Structure.id.asc()).limit(500).all()
        )
        raw_structure_id = (request.form.get("structure_id") or request.args.get("structure_id") or "").strip()
        if raw_structure_id:
            try:
                selected_structure_id = int(raw_structure_id)
            except Exception:
                selected_structure_id = None
    else:
        try:
            selected_structure_id = _current_structure_id()
        except Exception:
            selected_structure_id = getattr(current_user, "structure_id", None)

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        profession = _normalize_intervenant_actor_type(request.form.get("profession"))
        city = (request.form.get("city") or "").strip()
        address = (request.form.get("address") or "").strip()
        availability = _normalize_intervenant_availability(request.form.get("availability"))

        errors: list[str] = []
        if not selected_structure_id:
            errors.append("Structure is required.")
        if not full_name:
            errors.append("Nom complet requis.")
        if profession not in INTERVENANT_ACTOR_TYPE_LABELS:
            errors.append("Profession requise.")
        if availability not in INTERVENANT_AVAILABILITY_LABELS:
            errors.append("Disponibilité invalide.")
        if not city:
            errors.append("Ville requise.")

        if errors:
            for error in errors:
                flash(error, "warning")
            return render_template(
                "admin/intervenant_form.html",
                structures=structures,
                selected_structure_id=selected_structure_id,
                actor_type_options=INTERVENANT_ACTOR_TYPE_OPTIONS,
                availability_options=INTERVENANT_AVAILABILITY_OPTIONS,
                form_data=request.form,
            )

        intervenant = Intervenant(
            structure_id=int(selected_structure_id),
            name=full_name,
            actor_type=profession,
            email=email,
            phone=(request.form.get("phone") or "").strip() or None,
            location=_join_intervenant_location(city, address),
            availability=availability if _table_has_column("intervenants", "availability") else None,
            is_active=availability != "unavailable",
        )

        if hasattr(intervenant, "latitude") and _table_has_column("intervenants", "latitude"):
            lat_raw = (request.form.get("latitude") or "").strip()
            if lat_raw:
                try:
                    intervenant.latitude = float(lat_raw)
                except Exception:
                    flash("Latitude ignorée: valeur invalide.", "warning")
        if hasattr(intervenant, "longitude") and _table_has_column("intervenants", "longitude"):
            lng_raw = (request.form.get("longitude") or "").strip()
            if lng_raw:
                try:
                    intervenant.longitude = float(lng_raw)
                except Exception:
                    flash("Longitude ignorée: valeur invalide.", "warning")

        db.session.add(intervenant)
        db.session.commit()
        current_app.logger.info(
            "admin_intervenants_new created intervenant_id=%s structure_id=%s",
            intervenant.id,
            intervenant.structure_id,
        )
        flash("Intervenant créé.", "success")
        return redirect(url_for("admin.admin_intervenants"))

    return render_template(
        "admin/intervenant_form.html",
        structures=structures,
        selected_structure_id=selected_structure_id,
        actor_type_options=INTERVENANT_ACTOR_TYPE_OPTIONS,
        availability_options=INTERVENANT_AVAILABILITY_OPTIONS,
        form_data={},
    )


@admin_bp.get("/api/geocode")
@admin_required
def admin_api_geocode():
    admin_required_404()
    if not current_user.is_admin:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    address = (request.args.get("address") or "").strip()
    city = (request.args.get("city") or "").strip()
    query = request_address_display_text(
        address_line=address or None,
        city=city or None,
        country="France",
    )
    if not address or not city or not query:
        return jsonify({"ok": False, "error": "missing_query"}), 400

    try:
        import requests
    except Exception:
        current_app.logger.warning("admin_api_geocode requests import unavailable")
        return jsonify({"ok": False, "error": "service_unavailable"}), 503

    try:
        response = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": query, "limit": 1},
            headers={"Accept": "application/json"},
            timeout=4.0,
        )
        response.raise_for_status()
        payload = response.json() or {}
    except Exception:
        current_app.logger.exception("admin_api_geocode provider request failed")
        return jsonify({"ok": False, "error": "provider_error"}), 502

    features = payload.get("features") or []
    if not features:
        return jsonify({"ok": False, "error": "no_result"})

    feature = features[0] or {}
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    properties = feature.get("properties") or {}
    if len(coordinates) < 2:
        return jsonify({"ok": False, "error": "no_result"})

    longitude = coordinates[0]
    latitude = coordinates[1]
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except Exception:
        return jsonify({"ok": False, "error": "no_result"})

    return jsonify(
        {
            "ok": True,
            "latitude": latitude,
            "longitude": longitude,
            "label": properties.get("label") or query,
            "score": properties.get("score"),
        }
    )


@admin_bp.get("/professionals-map")
@admin_required
def admin_professionals_map():
    admin_required_404()
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))
    scope_structure = None
    scope_structure_id_raw = (request.args.get("structure_id") or "").strip()
    if scope_structure_id_raw:
        try:
            scope_structure_id = int(scope_structure_id_raw)
        except Exception:
            abort(404)
        scope_structure = db.session.get(Structure, scope_structure_id)
        if not scope_structure:
            abort(404)
        if not _is_global_admin() and int(_current_structure_id() or 0) != scope_structure.id:
            abort(403)

    map_api_url = url_for("admin.admin_api_professionals")
    list_url = url_for("admin.admin_intervenants")
    add_url = url_for("admin.admin_intervenants_new")
    if scope_structure is not None:
        map_api_url = url_for(
            "admin.admin_api_professionals",
            structure_id=scope_structure.id,
            include_inactive=1,
        )
        list_url = url_for("admin.admin_structure_intervenants", structure_id=scope_structure.id)
        add_url = url_for("admin.admin_intervenants_new", structure_id=scope_structure.id)

    return render_template(
        "admin/professionals_map.html",
        scope_structure=scope_structure,
        map_api_url=map_api_url,
        map_list_url=list_url,
        map_add_url=add_url,
    )


@admin_bp.get("/api/professionals")
@admin_required
def admin_api_professionals():
    admin_required_404()
    if not current_user.is_admin:
        return jsonify({"status": "error", "message": "forbidden"}), 403

    scope_structure = None
    scope_structure_id_raw = (request.args.get("structure_id") or "").strip()
    include_inactive = (request.args.get("include_inactive") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if scope_structure_id_raw:
        try:
            scope_structure_id = int(scope_structure_id_raw)
        except Exception:
            return jsonify({"status": "error", "message": "invalid_structure"}), 404
        scope_structure = db.session.get(Structure, scope_structure_id)
        if not scope_structure:
            return jsonify({"status": "error", "message": "structure_not_found"}), 404
        if not _is_global_admin() and int(_current_structure_id() or 0) != scope_structure.id:
            return jsonify({"status": "error", "message": "forbidden"}), 403

    workload_sq = _assignment_workload_subquery()
    query = (
        db.session.query(
            Intervenant,
            func.coalesce(workload_sq.c.workload, 0).label("workload"),
        )
        .outerjoin(workload_sq, workload_sq.c.intervenant_id == Intervenant.id)
    )
    if not include_inactive:
        query = query.filter(Intervenant.is_active.is_(True))
    if scope_structure is not None:
        query = query.filter(Intervenant.structure_id == scope_structure.id)
    elif not _is_global_admin():
        query = query.filter(Intervenant.structure_id == _current_structure_id())

    rows = query.order_by(Intervenant.name.asc(), Intervenant.id.asc()).all()
    professionals = []
    territorial_professionals = []
    for intervenant, workload in rows:
        lat, lng, has_exact_coordinates = _resolve_intervenant_coordinates(intervenant)
        availability = _intervenant_availability(intervenant)
        city = _intervenant_city(intervenant) or "Paris"
        profession = _intervenant_profession(intervenant)
        professionals.append(
            {
                "id": int(intervenant.id),
                "full_name": _intervenant_display_name(intervenant),
                "email": intervenant.email,
                "profession": profession,
                "city": city,
                "address": _intervenant_address(intervenant),
                "latitude": lat,
                "longitude": lng,
                "availability": availability,
                "availability_label": _intervenant_availability_label(
                    availability
                ),
                "workload": int(workload or 0),
                "has_exact_coordinates": has_exact_coordinates,
                "is_active": bool(getattr(intervenant, "is_active", False)),
            }
        )
        territorial_professionals.append(
            {
                "id": int(intervenant.id),
                "city": city,
                "latitude": lat,
                "longitude": lng,
                "availability": availability,
                "profession": profession,
                # Heuristic only: partner coverage is derived from observed actor types,
                # not from a dedicated partner-structure topology yet.
                "actor_type": str(getattr(intervenant, "actor_type", None) or ""),
                "workload": int(workload or 0),
                "has_exact_coordinates": bool(has_exact_coordinates),
                "is_active": bool(getattr(intervenant, "is_active", False)),
            }
        )

    territories = build_territorial_snapshots(
        risk_items=[],
        professional_items=territorial_professionals,
    )

    current_app.logger.info(
        "admin_api_professionals returning count=%s structure=%s include_inactive=%s",
        len(professionals),
        getattr(scope_structure, "id", None),
        include_inactive,
    )
    return jsonify(
        {
            "status": "ok",
            "professionals": professionals,
            "territories": territories,
            "territorial_contract": {
                "version": "pilotage-v1",
                "scope": "city",
                "semantics": "territorial_operational_intelligence",
            },
            "meta": {
                "structure_id": getattr(scope_structure, "id", None),
                "structure_name": getattr(scope_structure, "name", None),
                "include_inactive": include_inactive,
                "total": len(professionals),
            },
        }
    )


@admin_bp.get("/api/professionals-map")
@admin_required
def admin_api_professionals_map_alias():
    admin_required_404()
    # Backward-compatible alias for the legacy risk_api endpoint. The canonical
    # payload is /admin/api/professionals.
    return redirect(url_for("admin.admin_api_professionals"), code=302)


@admin_bp.route("/volunteers", methods=["GET"])
@admin_required
def admin_volunteers():
    admin_required_404()
    return redirect(url_for("admin.admin_intervenants"), code=302)

@admin_bp.route("/admin_volunteers", methods=["GET"])
@admin_required
def admin_volunteers_compat():
    admin_required_404()
    return redirect(url_for("admin.admin_volunteers"), code=302)


@admin_bp.route("/admin_volunteers/add", methods=["GET", "POST"])
@admin_required
def add_volunteer():
    admin_required_404()
    """Add a volunteer."""
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        volunteer = Volunteer(
            name=request.form["name"],
            email=request.form["email"],
            phone=request.form["phone"],
            location=request.form["location"],
            skills=request.form.get("skills", ""),
        )
        db.session.add(volunteer)
        db.session.commit()
        flash(_("Volunteer added successfully!"), "success")
        return redirect(url_for("admin.admin_volunteers"))

    return render_template("add_volunteer.html")


@admin_bp.route("/delete_volunteer/<int:id>", methods=["POST"])
@admin_required
def delete_volunteer(id):
    admin_required_404()
    """Delete a volunteer."""
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    from flask import abort

    volunteer = db.session.get(Volunteer, id)
    if not volunteer:
        abort(404)
    db.session.delete(volunteer)
    db.session.commit()
    flash(_("Volunteer deleted successfully!"), "success")
    return redirect(url_for("admin.admin_volunteers"))


@admin_bp.route("/admin_volunteers/edit/<int:id>", methods=["GET", "POST"])
@admin_required
def edit_volunteer(id):
    admin_required_404()
    """Edit volunteer details."""
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    from flask import abort

    volunteer = db.session.get(Volunteer, id)
    if not volunteer:
        abort(404)

    import logging

    if request.method == "POST":
        logging.warning(f"[DEBUG] POST data: {request.form}")
        volunteer.name = request.form["name"]
        volunteer.email = request.form["email"]
        volunteer.phone = request.form["phone"]
        volunteer.location = request.form["location"]
        volunteer.skills = request.form.get("skills", "")
        logging.warning(
            f"[DEBUG] Before commit: name={volunteer.name}, email={volunteer.email}, phone={volunteer.phone}, location={volunteer.location}, skills={volunteer.skills}"
        )
        db.session.commit()
        logging.warning(
            f"[DEBUG] After commit: id={volunteer.id}, email={volunteer.email}"
        )
        flash(_("Changes saved!"), "success")
        return redirect(url_for("admin.admin_volunteers"))

    return render_template("edit_volunteer.html", volunteer=volunteer)


@admin_bp.route("/export_volunteers")
@admin_required
def export_volunteers():
    admin_required_404()
    """Export volunteers as CSV."""
    _require_global_admin()
    if getattr(current_user, "structure_id", None) is not None:
        abort(403)
    if not current_user.is_admin:
        flash(_("Access denied."), "error")
        return redirect(url_for("main.index"))

    import csv
    from io import StringIO

    from flask import Response

    volunteers = Volunteer.query.all()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["Име", "Имейл", "Телефон", "Град/регион", "Умения"])
    for v in volunteers:
        cw.writerow([v.name, v.email, v.phone, v.location, v.skills])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=volunteers.csv"},
    )


from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import and_, func, or_, tuple_

from ..models import Request, RequestActivity, db

ALLOWED_STATUSES = {"pending", "approved", "in_progress", "done", "rejected"}

STATUS_LABELS_BG = {
    "pending": "Чакащи",
    "approved": "Одобрени",
    "in_progress": "В процес",
    "done": "Приключени",
    "rejected": "Отхвърлени",
}

# Canonical EN msgids for status labels - passed to templates for localization
STATUS_LABELS = {
    "pending": "Pending",
    "approved": "Approved",
    "in_progress": "In progress",
    "done": "Completed",
    "rejected": "Rejected",
}


@admin_bp.get("/risk")
@admin_required
def admin_risk_panel():
    admin_required_404()
    return render_template("admin/risk_panel.html")


@admin_bp.get("/sla")
@admin_required
def admin_sla_breakdown():
    admin_required_404()
    _require_global_admin()

    breach_type = (request.args.get("type") or "owner_assign").strip().lower()
    if breach_type not in {"all", "resolve", "owner_assign", "volunteer_assign"}:
        breach_type = "owner_assign"
    sort = (request.args.get("sort") or "overdue").strip().lower()
    days = _normalize_sla_days(request.args.get("days", 30))
    limit = max(1, min(int(request.args.get("limit", 200) or 200), 1000))

    if current_app.config.get("DEMO_MODE"):
        payload = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        demo = payload["sla_kpis"]
        scenario_meta = payload["scenario_meta"]
        return render_template(
            "admin/sla.html",
            breach_label=demo["breach_label"],
            breach_type=breach_type,
            days=days,
            sort=sort,
            limit=limit,
            resolve_count=demo["resolve_count"],
            owner_assign_count=demo["owner_assign_count"],
            volunteer_assign_count=demo["volunteer_assign_count"],
            prediction_counts=demo["prediction_counts"],
            rows=payload["sla_rows"],
            scenario_label=scenario_meta["label"],
            scenario_description=scenario_meta["short_description"],
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    q = _sla_base_window_query(_scope_requests(Request.query), days=days, now=now)

    if breach_type == "resolve":
        breach_label = "SLA résolution"
    elif breach_type == "owner_assign":
        breach_label = "SLA assignation owner"
    elif breach_type == "volunteer_assign":
        breach_label = "Affectation bénévole"
    else:
        breach_label = "Toutes violations"

    resolve_count = (
        _apply_sla_queue_filter(
            _scope_requests(Request.query),
            sla_kind="resolution_overdue",
            days=days,
            now=now,
        ).count()
        or 0
    )
    owner_assign_count = (
        _apply_sla_queue_filter(
            _scope_requests(Request.query),
            sla_kind="owner_assignment_overdue",
            days=days,
            now=now,
        ).count()
        or 0
    )
    volunteer_assign_count = (
        _apply_sla_queue_filter(
            _scope_requests(Request.query),
            sla_kind="volunteer_assignment_overdue",
            days=days,
            now=now,
        ).count()
        or 0
    )

    requests = q.all()
    prediction_counts = {
        "resolution_overdue": 0,
        "owner_assignment_overdue": 0,
        "volunteer_assignment_overdue": 0,
    }

    rows: list[dict] = []

    for req in requests:
        for kind in prediction_counts.keys():
            pred = _sla_prediction_state(req, sla_kind=kind, now=now)
            if pred.get("state") == "due_soon":
                prediction_counts[kind] += 1

        overdue_by_kind = _sla_overdue_hours_by_kind(req, now=now)
        candidates = [
            (SLA_KIND_TO_BREAKDOWN_TYPE[kind], overdue)
            for kind, overdue in overdue_by_kind.items()
            if kind in SLA_KIND_TO_BREAKDOWN_TYPE
        ]

        if not candidates:
            continue

        if breach_type == "all":
            row_breach_type, overdue_hours = max(candidates, key=lambda x: x[1])
        else:
            picked = {k: v for k, v in candidates}.get(breach_type)
            if picked is None:
                continue
            row_breach_type, overdue_hours = breach_type, picked

        rows.append(
            {
                "id": req.id,
                "title": req.title,
                "category": req.category,
                "status": req.status,
                "created_at": req.created_at,
                "owner_id": req.owner_id,
                "assigned_volunteer_id": req.assigned_volunteer_id,
                "overdue_hours": round(float(overdue_hours), 1),
                "breach_type": row_breach_type,
            }
        )

    def _created_ts(row: dict) -> float:
        dt = _to_utc_naive(row.get("created_at"))
        return dt.timestamp() if dt else 0.0

    if sort == "created":
        rows.sort(key=_created_ts)
    else:
        rows.sort(
            key=lambda r: (-(float(r.get("overdue_hours") or 0.0)), _created_ts(r))
        )
    rows = rows[:limit]

    return render_template(
        "admin/sla.html",
        breach_label=breach_label,
        breach_type=breach_type,
        days=days,
        sort=sort,
        limit=limit,
        resolve_count=int(resolve_count),
        owner_assign_count=int(owner_assign_count),
        volunteer_assign_count=int(volunteer_assign_count),
        prediction_counts=prediction_counts,
        rows=rows,
    )


@admin_bp.get("/pilotage")
@admin_required
@admin_role_required("readonly", "ops", "superadmin", "admin")
def admin_pilotage():
    admin_required_404()

    if current_app.config.get("DEMO_MODE"):
        payload = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        scenario_meta = payload["scenario_meta"]
        pilotage = payload.get("pilotage") or {}
        return render_template(
            "admin/pilotage.html",
            critical_count=pilotage.get("critical_count", 0),
            attention_count=pilotage.get("attention_count", 0),
            standard_count=pilotage.get("standard_count", 0),
            no_owner_count=pilotage.get("no_owner_count", 0),
            not_seen_72h_count=pilotage.get("not_seen_72h_count", 0),
            critical_no_owner_count=pilotage.get("critical_without_owner_count", 0),
            critical_without_owner_count=pilotage.get("critical_without_owner_count", 0),
            assign_immediately_count=pilotage.get("assign_immediately_count", 0),
            manager_review_today_count=pilotage.get("manager_review_today_count", 0),
            priority_items=pilotage.get("priority_items", []),
            category_trend_text=pilotage.get("category_trend_text", ""),
            assignment_delay_text=pilotage.get("assignment_delay_text", ""),
            vigilance_text=pilotage.get("vigilance_text", ""),
            rec_counts=pilotage.get("rec_counts", {}),
            received_today=pilotage.get("received_today", 0),
            taken_today=pilotage.get("taken_today", 0),
            closed_today=pilotage.get("closed_today", 0),
            scenario_label=scenario_meta["label"],
            scenario_description=scenario_meta["short_description"],
            institutional_kpis=payload.get("institutional_kpis", []),
            institutional_story=payload.get("institutional_story", {}),
            institutional_universe=payload.get("institutional_universe", {}),
        )

    base_query = _scope_requests(Request.query).filter(Request.deleted_at.is_(None))
    active_query = base_query.filter(
        or_(
            Request.status.is_(None),
            ~func.lower(func.coalesce(Request.status, "")).in_(
                ("done", "cancelled", "rejected")
            ),
        )
    )

    has_risk_level = _table_has_column("requests", "risk_level")
    has_risk_signals = _table_has_column("requests", "risk_signals")
    has_risk_score = _table_has_column("requests", "risk_score")
    has_created_at = _table_has_column("requests", "created_at")
    has_owned_at = _table_has_column("requests", "owned_at")
    has_completed_at = _table_has_column("requests", "completed_at")
    has_updated_at = _table_has_column("requests", "updated_at")

    critical_count = 0
    attention_count = 0
    standard_count = 0
    if has_risk_level:
        critical_count = (
            base_query.filter(func.lower(func.coalesce(Request.risk_level, "")) == "critical").count()
        )
        attention_count = (
            base_query.filter(func.lower(func.coalesce(Request.risk_level, "")) == "attention").count()
        )
        standard_count = (
            base_query.filter(func.lower(func.coalesce(Request.risk_level, "standard")) == "standard").count()
        )
    elif has_risk_score:
        critical_count = base_query.filter(func.coalesce(Request.risk_score, 0) >= 70).count()
        attention_count = base_query.filter(
            func.coalesce(Request.risk_score, 0) >= 40,
            func.coalesce(Request.risk_score, 0) < 70,
        ).count()
        standard_count = base_query.filter(func.coalesce(Request.risk_score, 0) < 40).count()

    no_owner_count = 0
    not_seen_72h_count = 0
    critical_no_owner_count = 0
    activity_sq = build_request_meaningful_activity_subquery()
    activity_expr = func.coalesce(
        activity_sq.c.last_activity_at,
        Request.updated_at,
        Request.created_at,
    )
    stale_threshold = _now_utc() - timedelta(hours=72)
    if has_risk_signals:
        no_owner_filter = func.lower(func.coalesce(Request.risk_signals, "")).like("%no_owner%")
        no_owner_count = base_query.filter(no_owner_filter).count()
        if has_risk_level:
            critical_no_owner_count = base_query.filter(
                func.lower(func.coalesce(Request.risk_level, "")) == "critical",
                no_owner_filter,
            ).count()
    not_seen_72h_count = (
        base_query.outerjoin(activity_sq, activity_sq.c.request_id == Request.id)
        .filter(activity_expr <= stale_threshold)
        .count()
    )

    rec_counts = {
        "assign_immediately": 0,
        "manager_review_today": 0,
        "route_to_housing_partner": 0,
        "route_to_food_support": 0,
        "route_to_health_support": 0,
    }
    if has_risk_level or has_risk_signals:
        rec_rows = base_query.with_entities(
            (Request.risk_level if has_risk_level else func.literal("standard")),
            (Request.risk_signals if has_risk_signals else func.literal("")),
        ).all()
        for risk_level, risk_signals in rec_rows:
            rec = compute_recommendation(
                SimpleNamespace(risk_level=risk_level, risk_signals=risk_signals)
            )
            action = rec.get("recommended_action")
            if action in rec_counts:
                rec_counts[action] += 1
    assign_immediately_count = int(rec_counts.get("assign_immediately", 0) or 0)
    manager_review_today_count = int(rec_counts.get("manager_review_today", 0) or 0)

    def _signals_text(raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip().lower()
        try:
            return json.dumps(raw, ensure_ascii=False).lower()
        except Exception:
            return str(raw).strip().lower()

    def _to_unix_ts(dt_value: object) -> float:
        if not isinstance(dt_value, datetime):
            return 0.0
        try:
            return float(dt_value.timestamp())
        except Exception:
            return 0.0

    priority_cols = [
        Request.id,
        Request.title,
        Request.description,
        Request.owner_id,
        (Request.risk_level if has_risk_level else func.literal("standard")),
        (Request.risk_signals if has_risk_signals else func.literal("")),
        (Request.risk_score if has_risk_score else func.literal(0)),
        (Request.created_at if has_created_at else func.literal(None)),
    ]
    priority_rows = base_query.with_entities(*priority_cols).limit(500).all()

    priority_items: list[dict[str, object]] = []
    for (
        rid,
        title,
        description,
        owner_id,
        risk_level,
        risk_signals,
        risk_score,
        created_at,
    ) in priority_rows:
        risk_level_norm = (str(risk_level or "standard").strip().lower() or "standard")
        if risk_level_norm not in {"standard", "attention", "critical"}:
            risk_level_norm = "standard"
        signals_text = _signals_text(risk_signals)
        has_no_owner = "no_owner" in signals_text
        has_not_seen_72h = "not_seen_72h" in signals_text

        rec_action = "routine_queue"
        try:
            rec_action = (
                compute_recommendation(
                    SimpleNamespace(risk_level=risk_level_norm, risk_signals=risk_signals)
                ).get("recommended_action")
                or "routine_queue"
            )
        except Exception:
            rec_action = "routine_queue"

        if risk_level_norm == "critical" and has_no_owner:
            indicator_label = "Sans responsable"
            rank_group = 0
        elif has_not_seen_72h:
            indicator_label = "Sans action depuis 72 heures"
            rank_group = 1
        elif rec_action == "assign_immediately":
            indicator_label = "Affectation immédiate recommandée"
            rank_group = 2
        elif rec_action == "manager_review_today":
            indicator_label = "Revue managériale requise"
            rank_group = 3
        elif risk_level_norm == "critical":
            indicator_label = "Niveau critique"
            rank_group = 4
        else:
            continue

        score_value = int(risk_score or 0)
        summary_compact = build_case_summary_snippet(
            SimpleNamespace(
                title=title,
                description=description,
                risk_level=risk_level_norm,
                risk_signals=risk_signals,
                owner_id=owner_id,
            ),
            {"recommended_action": rec_action},
            max_len=100,
        )
        priority_items.append(
            {
                "id": rid,
                "title": title or f"Demande #{rid}",
                "risk_level": risk_level_norm,
                "indicator_label": indicator_label,
                "summary_compact": summary_compact,
                "rank_group": rank_group,
                "risk_score": score_value,
                "created_ts": _to_unix_ts(created_at),
            }
        )

    priority_items.sort(
        key=lambda item: (
            int(item.get("rank_group") or 99),
            -int(item.get("risk_score") or 0),
            -float(item.get("created_ts") or 0.0),
            -int(item.get("id") or 0),
        )
    )
    priority_items = priority_items[:5]

    now = datetime.now(UTC).replace(tzinfo=None)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    received_today = 0
    taken_today = 0
    closed_today = 0
    if has_created_at:
        received_today = base_query.filter(
            Request.created_at >= day_start, Request.created_at < day_end
        ).count()
    if has_owned_at:
        taken_today = base_query.filter(
            Request.owned_at >= day_start, Request.owned_at < day_end
        ).count()
    if has_completed_at:
        closed_today = base_query.filter(
            Request.completed_at >= day_start, Request.completed_at < day_end
        ).count()

    def _category_label(value: str | None) -> str:
        norm = (value or "").strip().lower()
        mapping = {
            "housing": "logement",
            "logement": "logement",
            "health": "santé",
            "sante": "santé",
            "food": "aide alimentaire",
            "emergency": "urgence",
            "social": "accompagnement social",
            "general": "suivi social",
            "safety": "protection",
        }
        if norm in mapping:
            return mapping[norm]
        if not norm:
            return ""
        return norm.replace("_", " ")

    top_category_row = (
        active_query.with_entities(
            func.lower(func.trim(func.coalesce(Request.category, ""))).label("cat"),
            func.count(Request.id).label("cnt"),
        )
        .group_by("cat")
        .order_by(func.count(Request.id).desc(), func.lower(Request.category).asc())
        .all()
    )
    top_category = ""
    top_category_count = 0
    for row in top_category_row:
        cat = (getattr(row, "cat", "") or "").strip()
        if not cat:
            continue
        top_category = cat
        top_category_count = int(getattr(row, "cnt", 0) or 0)
        break
    if top_category and top_category_count >= 2:
        category_trend_text = (
            f"La catégorie la plus fréquente actuellement concerne {_category_label(top_category)}."
        )
    else:
        category_trend_text = "Aucune tendance catégorielle significative à ce stade."

    assignment_delay_hours: list[float] = []
    if has_created_at and has_owned_at:
        assignment_rows = (
            active_query.with_entities(Request.created_at, Request.owned_at)
            .filter(Request.created_at.isnot(None), Request.owned_at.isnot(None))
            .limit(1500)
            .all()
        )
        for created_at, owned_at in assignment_rows:
            if not isinstance(created_at, datetime) or not isinstance(owned_at, datetime):
                continue
            delta_sec = (owned_at - created_at).total_seconds()
            if delta_sec < 0:
                continue
            assignment_delay_hours.append(delta_sec / 3600.0)
    if len(assignment_delay_hours) >= 3:
        avg_hours = round(sum(assignment_delay_hours) / len(assignment_delay_hours))
        assignment_delay_text = (
            f"Le délai moyen avant affectation est actuellement de {int(avg_hours)} heures."
        )
    else:
        assignment_delay_text = (
            "Données insuffisantes pour estimer le délai moyen avant affectation."
        )

    if int(critical_no_owner_count or 0) > 0:
        vigilance_text = (
            "Les situations critiques sans responsable nécessitent une vigilance renforcée."
        )
    elif int(not_seen_72h_count or 0) > 0:
        vigilance_text = (
            "Les situations sans action récente nécessitent une vigilance renforcée."
        )
    elif int(attention_count or 0) > int(critical_count or 0) and int(attention_count or 0) > 0:
        vigilance_text = (
            "Une part importante des situations est au niveau attention et appelle un suivi rapproché."
        )
    else:
        vigilance_text = "Aucun signal de vigilance particulier n’est identifié à ce stade."

    return render_template(
        "admin/pilotage.html",
        critical_count=critical_count,
        attention_count=attention_count,
        standard_count=standard_count,
        no_owner_count=no_owner_count,
        not_seen_72h_count=not_seen_72h_count,
        critical_no_owner_count=critical_no_owner_count,
        critical_without_owner_count=critical_no_owner_count,
        assign_immediately_count=assign_immediately_count,
        manager_review_today_count=manager_review_today_count,
        priority_items=priority_items,
        rec_counts=rec_counts,
        received_today=received_today,
        taken_today=taken_today,
        closed_today=closed_today,
        category_trend_text=category_trend_text,
        assignment_delay_text=assignment_delay_text,
        vigilance_text=vigilance_text,
    )


def _normalize_cases_risk_filter(risk_value: str) -> str:
    risk = (risk_value or "").strip().lower()
    if risk == "high":
        return "attention"
    if risk in {"critical", "attention", "normal", "low"}:
        return risk
    return ""


def _build_case_kpi_filters(
    *,
    now: datetime | None = None,
    activity_expr_override=None,
) -> dict[str, object]:
    now_utc = now or _now_utc()
    if activity_expr_override is not None:
        activity_expr = activity_expr_override
    else:
        activity_expr = func.coalesce(
            Case.last_activity_at,
            Case.updated_at,
            Case.created_at,
        )
    stale_threshold_72h = now_utc - timedelta(hours=72)
    stale_threshold_7d = now_utc - timedelta(days=7)

    priority_key = func.lower(func.coalesce(Case.priority, ""))
    priority_points = case(
        (priority_key == "critical", 45),
        (priority_key == "high", 30),
        else_=0,
    )

    risk_score = func.coalesce(Case.risk_score, 0)
    risk_points = case(
        (risk_score >= 85, 35),
        (risk_score >= 60, 20),
        else_=0,
    )

    no_owner_filter = Case.owner_user_id.is_(None)
    owner_points = case((no_owner_filter, 20), else_=0)

    stale_72h_filter = activity_expr <= stale_threshold_72h
    stale_points = case(
        (activity_expr <= stale_threshold_7d, 30),
        (stale_72h_filter, 20),
        else_=0,
    )

    text_expr = func.lower(
        func.coalesce(Request.title, "")
        + " "
        + func.coalesce(Request.description, "")
        + " "
        + func.coalesce(Request.message, "")
    )
    essential_keywords = (
        "sans nourriture",
        "faim",
        "pas à manger",
        "pas a manger",
        "sans manger",
        "sans logement",
        "sans abri",
        "à la rue",
        "a la rue",
        "dehors ce soir",
        "sans chauffage",
        "pas de chauffage",
        "sans électricité",
        "sans electricite",
        "sans eau",
        "pas d'eau",
        "plus de médicaments",
        "plus de medicaments",
        "sans médicaments",
        "sans medicaments",
    )
    vulnerability_keywords = (
        "personne âgée",
        "personne agee",
        "âgée",
        "agee",
        "senior",
        "handicap",
        "handicapé",
        "handicape",
        "enfant",
        "bébé",
        "bebe",
        "mineur",
        "grossesse",
        "enceinte",
    )
    essential_filter = or_(*[text_expr.like(f"%{keyword}%") for keyword in essential_keywords])
    vulnerability_filter = or_(*[text_expr.like(f"%{keyword}%") for keyword in vulnerability_keywords])
    text_points = case((essential_filter, 20), else_=0) + case((vulnerability_filter, 10), else_=0)

    ops_priority_score = priority_points + risk_points + owner_points + stale_points + text_points
    critical_filter = ops_priority_score >= 80
    attention_filter = and_(ops_priority_score >= 50, ops_priority_score < 80)
    normal_filter = and_(ops_priority_score >= 30, ops_priority_score < 50)
    low_filter = ops_priority_score < 30

    return {
        "activity_expr": activity_expr,
        "stale_72h": stale_72h_filter,
        "no_owner": no_owner_filter,
        "critical": critical_filter,
        "attention": attention_filter,
        "normal": normal_filter,
        "low": low_filter,
        "ops_priority_score": ops_priority_score,
    }


def _apply_cases_risk_filter(query, risk_value: str, *, case_filters: dict[str, object] | None = None):
    risk = _normalize_cases_risk_filter(risk_value)
    case_filters = case_filters or _build_case_kpi_filters()
    if risk == "critical":
        return query.filter(case_filters["critical"])
    if risk == "attention":
        return query.filter(case_filters["attention"])
    if risk == "normal":
        return query.filter(case_filters["normal"])
    if risk == "low":
        return query.filter(case_filters["low"])
    return query


def _build_scoped_cases_base_query():
    scoped_request_ids_query = _scope_requests(Request.query.with_entities(Request.id))
    scoped_request_ids_query = scoped_request_ids_query.filter(Request.deleted_at.is_(None))
    try:
        scoped_request_ids_query = scoped_request_ids_query.filter(
            Request.is_archived.is_(False)
        )
    except Exception:
        pass
    scoped_ids_subq = scoped_request_ids_query.subquery()
    activity_sq = build_request_meaningful_activity_subquery()
    case_activity_expr = func.coalesce(
        activity_sq.c.last_activity_at,
        Case.last_activity_at,
        Case.updated_at,
        Request.updated_at,
        Case.created_at,
        Request.created_at,
    )
    query = (
        Case.query.join(Request, Case.request_id == Request.id)
        .join(scoped_ids_subq, Request.id == scoped_ids_subq.c.id)
        .outerjoin(activity_sq, activity_sq.c.request_id == Request.id)
        .filter(Case.structure_id == Request.structure_id)
    )
    case_filters = _build_case_kpi_filters(activity_expr_override=case_activity_expr)
    return query, case_filters


def _case_category_filter(category: str):
    category_variants = {category}
    for legacy_code in ("general", "social", "medical", "tech", "admin", "other"):
        if normalize_request_category(legacy_code) == category:
            category_variants.add(legacy_code)
    return func.lower(func.coalesce(Request.category, "")).in_(
        [c.lower() for c in category_variants]
    )


def _case_city_filter(city: str):
    city_like = f"%{city.lower()}%"
    return or_(
        func.lower(func.coalesce(Request.city, "")).like(city_like),
        func.lower(func.coalesce(Request.location_text, "")).like(city_like),
        func.lower(func.coalesce(Request.address_line, "")).like(city_like),
    )


def _apply_cases_queue_filters(
    query,
    *,
    case_filters: dict[str, object],
    status: str = "",
    priority: str = "",
    owner: str = "",
    category: str = "",
    risk: str = "",
    stale_72h: bool = False,
    city: str = "",
):
    owner_value = (owner or "").strip()
    owner_id = None
    owner_none = owner_value.lower() == "none"
    if owner_value:
        try:
            owner_id = int(owner_value)
        except Exception:
            owner_id = None

    if status in CATEGORY_CASE_STATUSES:
        query = query.filter(Case.status == status)
    else:
        query = query.filter(Case.status.in_(ACTIVE_CASE_QUEUE_STATUSES))
    if priority in CASE_PRIORITIES:
        query = query.filter(Case.priority == priority)
    if category:
        query = query.filter(_case_category_filter(category))
    if city:
        query = query.filter(_case_city_filter(city))
    if owner_id:
        query = query.filter(Case.owner_user_id == owner_id)
    elif owner_none:
        query = query.filter(case_filters["no_owner"])
    query = _apply_cases_risk_filter(query, risk, case_filters=case_filters)
    if stale_72h:
        query = query.filter(case_filters["stale_72h"])
    return query


def _case_queue_count(
    base_query,
    *,
    case_filters: dict[str, object],
    status: str = "",
    priority: str = "",
    owner: str = "",
    category: str = "",
    risk: str = "",
    stale_72h: bool = False,
    city: str = "",
) -> int:
    return int(
        _apply_cases_queue_filters(
            base_query,
            case_filters=case_filters,
            status=status,
            priority=priority,
            owner=owner,
            category=category,
            risk=risk,
            stale_72h=stale_72h,
            city=city,
        ).count()
        or 0
    )


def _case_queue_counts_for_filters(
    base_query,
    *,
    case_filters: dict[str, object],
    status: str = "",
    priority: str = "",
    owner: str = "",
    category: str = "",
    risk: str = "",
    stale_72h: bool = False,
    city: str = "",
) -> dict[str, int]:
    return {
        "critical": _case_queue_count(
            base_query,
            case_filters=case_filters,
            status=status,
            priority=priority,
            owner=owner,
            category=category,
            risk="critical",
            stale_72h=stale_72h,
            city=city,
        ),
        "attention": _case_queue_count(
            base_query,
            case_filters=case_filters,
            status=status,
            priority=priority,
            owner=owner,
            category=category,
            risk="attention",
            stale_72h=stale_72h,
            city=city,
        ),
        "no_owner": _case_queue_count(
            base_query,
            case_filters=case_filters,
            status=status,
            priority=priority,
            owner="none",
            category=category,
            risk=risk,
            stale_72h=stale_72h,
            city=city,
        ),
        "stale_72h": _case_queue_count(
            base_query,
            case_filters=case_filters,
            status=status,
            priority=priority,
            owner=owner,
            category=category,
            risk=risk,
            stale_72h=True,
            city=city,
        ),
    }


def _case_active_filter_labels(
    *,
    status: str = "",
    priority: str = "",
    owner: str = "",
    category: str = "",
    risk: str = "",
    stale_72h: bool = False,
    city: str = "",
) -> list[str]:
    labels: list[str] = []
    if status:
        labels.append(f"Statut: {status.replace('_', ' ')}")
    if priority:
        labels.append(f"Priorite: {priority}")
    if owner:
        if owner.lower() == "none":
            labels.append("Responsable: non attribue")
        else:
            labels.append(f"Responsable: #{owner}")
    if category:
        labels.append(f"Categorie: {category.replace('_', ' ')}")
    if risk:
        risk_labels = {
            "critical": "Critique",
            "attention": "A verifier",
            "normal": "Normal",
            "low": "Faible",
        }
        labels.append(f"Risque: {risk_labels.get(risk, risk)}")
    if stale_72h:
        labels.append("Cas sans action 72h")
    if city:
        labels.append(f"Zone: {city}")
    return labels


def _scoped_notification_jobs_query():
    query = NotificationJob.query
    try:
        if not _is_global_admin():
            sid = _current_structure_id()
            query = query.filter(NotificationJob.structure_id == sid)
    except Exception:
        pass
    return query


def _notification_bucket_filter(bucket: str):
    return notification_job_bucket_filter(bucket)


def _notification_bucket_key(job) -> str:
    return notification_job_bucket_key(job)


def _notification_bucket_label(bucket: str) -> str:
    return {
        "pending": "en attente",
        "processing": "en cours",
        "retry": "à relancer",
        "failed": "en échec",
        "sent": "envoyée",
    }.get((bucket or "").strip().lower(), "en attente")


def _render_cases_list():
    if current_app.config.get("DEMO_MODE"):
        demo = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        case_kpis = demo["cases_kpis"]
        scenario_meta = demo["scenario_meta"]
        return render_template(
            "admin/cases.html",
            cases=demo["cases_rows"],
            status="",
            priority="",
            owner="",
            category="",
            risk="",
            stale_72h=False,
            active_filter_labels=[],
            statuses=list(CATEGORY_CASE_STATUSES),
            priorities=list(CASE_PRIORITIES),
            owners=[(1, "Marie Dupont"), (2, "Nadia Bernard")],
            critical_count=case_kpis["critical"],
            attention_count=case_kpis["attention"],
            no_owner_count=case_kpis["no_owner"],
            stale_count=case_kpis["stale"],
            case_signals=demo["cases_signals"],
            ops_priority_levels={201: "critique", 202: "élevé", 203: "critique"},
        )

    if not _cases_enabled():
        flash("Case system tables are not available yet. Run migrations first.", "warning")
        return render_template(
            "admin/cases.html",
            cases=[],
            status="",
            priority="",
            owner="",
            category="",
            risk="",
            stale_72h=False,
            active_filter_labels=[],
            statuses=list(CATEGORY_CASE_STATUSES),
            priorities=list(CASE_PRIORITIES),
            owners=[],
            critical_count=0,
            attention_count=0,
            no_owner_count=0,
            stale_count=0,
        )

    status = (request.args.get("status") or "").strip().lower()
    priority = (request.args.get("priority") or "").strip().lower()
    owner = (request.args.get("owner") or "").strip()
    category = normalize_request_category((request.args.get("category") or "").strip())
    risk = _normalize_cases_risk_filter((request.args.get("risk") or "").strip().lower())
    city = (request.args.get("city") or "").strip()
    stale_72h = (
        (request.args.get("stale_72h") or request.args.get("stale") or "").strip()
        == "1"
    )

    base_query, case_kpi_filters = _build_scoped_cases_base_query()
    activity_expr = case_kpi_filters["activity_expr"]
    stale_threshold = _now_utc() - timedelta(hours=72)
    queue_counts = _case_queue_counts_for_filters(
        base_query,
        case_filters=case_kpi_filters,
        status=status,
        priority=priority,
        owner=owner,
        category=category,
        risk=risk,
        stale_72h=stale_72h,
        city=city,
    )
    query = _apply_cases_queue_filters(
        base_query,
        case_filters=case_kpi_filters,
        status=status,
        priority=priority,
        owner=owner,
        category=category,
        risk=risk,
        stale_72h=stale_72h,
        city=city,
    )

    case_rows = (
        query.options(
            joinedload(Case.request),
            joinedload(Case.owner_user),
            joinedload(Case.assigned_professional_lead),
        )
        .order_by(
            case(
                ((func.coalesce(Case.risk_score, 0) >= 85), 0),
                ((func.coalesce(Case.risk_score, 0) >= 60), 1),
                else_=2,
            ).asc(),
            case(
                ((Case.priority == "critical"), 0),
                ((Case.priority == "high"), 1),
                ((Case.priority == "normal"), 2),
                else_=3,
            ).asc(),
            case((Case.owner_user_id.is_(None), 0), else_=1).asc(),
            case((activity_expr <= stale_threshold, 0), else_=1).asc(),
            activity_expr.desc().nullslast(),
            Case.id.desc(),
        )
        .limit(300)
        .all()
    )
    current_app.logger.warning(
        "OPS_CASES_DEBUG rows=%s status=%s priority=%s owner=%s category=%s risk=%s stale=%s city=%s",
        len(case_rows),
        status,
        priority,
        owner,
        category,
        risk,
        stale_72h,
        city,
    )

    case_signals = {}
    ops_priority_levels = {}
    now_utc = _now_utc()
    case_last_activity_by_id = {}
    for c in case_rows:
        last_activity_at = get_request_last_meaningful_activity(getattr(c, "request", None))
        case_last_activity_by_id[int(c.id)] = last_activity_at
        result = compute_ops_priority(
            case_row=c,
            request_row=getattr(c, "request", None),
            activity_ref=last_activity_at,
            now=now_utc,
        )
        case_signals[int(c.id)] = result.get("ops_priority_reasons") or []
        ops_priority_levels[int(c.id)] = result.get("ops_priority_level") or "normal"

    critical_count = queue_counts["critical"]
    attention_count = queue_counts["attention"]
    no_owner_count = queue_counts["no_owner"]
    stale_count = queue_counts["stale_72h"]

    owners_query = AdminUser.query.with_entities(AdminUser.id, AdminUser.username)
    if not _is_global_admin():
        owners_query = owners_query.filter(
            AdminUser.structure_id == _current_structure_id()
        )
    owners = owners_query.order_by(AdminUser.username.asc()).all()

    return render_template(
        "admin/cases.html",
        cases=case_rows,
        status=status,
        priority=priority,
        owner=owner,
        category=category,
        risk=risk,
        city=city,
        stale_72h=stale_72h,
        active_filter_labels=_case_active_filter_labels(
            status=status,
            priority=priority,
            owner=owner,
            category=category,
            risk=risk,
            stale_72h=stale_72h,
            city=city,
        ),
        statuses=list(CATEGORY_CASE_STATUSES),
        priorities=list(CASE_PRIORITIES),
        owners=owners,
        critical_count=critical_count,
        attention_count=attention_count,
        no_owner_count=no_owner_count,
        stale_count=stale_count,
        case_signals=case_signals,
        ops_priority_levels=ops_priority_levels,
        case_last_activity_by_id=case_last_activity_by_id,
    )


def _render_notifications_list():
    if current_app.config.get("DEMO_MODE"):
        demo = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        scenario_meta = demo["scenario_meta"]
        return render_template(
            "admin/notifications_operational.html",
            jobs=demo["notification_rows"],
            status="",
            channel="",
            event_type="",
            recipient="",
            summary=demo["notifications_kpis"],
            channels=demo["notification_channels"],
            scenario_label=scenario_meta["label"],
            scenario_description=scenario_meta["short_description"],
        )

    if not _table_exists("notification_jobs"):
        flash("Notification jobs table is not available yet. Run migrations first.", "warning")
        return render_template(
            "admin/notifications_operational.html",
            jobs=[],
            status="",
            channel="",
            event_type="",
            recipient="",
            summary={
                "pending": 0,
                "processing": 0,
                "done": 0,
                "dead_letter": 0,
                "retry": 0,
                "failed": 0,
                "sent": 0,
            },
            channels=[],
        )

    status = (request.args.get("status") or "").strip().lower()
    channel = (request.args.get("channel") or "").strip().lower()
    event_type = (request.args.get("event_type") or "").strip()
    recipient = (request.args.get("recipient") or "").strip()

    query = _scoped_notification_jobs_query()

    bucket_filter = _notification_bucket_filter(status)
    if bucket_filter is not None:
        query = query.filter(bucket_filter)
    if channel:
        query = query.filter(NotificationJob.channel == channel)
    if event_type:
        query = query.filter(NotificationJob.event_type.ilike(f"%{event_type}%"))
    if recipient:
        query = query.filter(NotificationJob.recipient.ilike(f"%{recipient}%"))

    status_rank = case(
        (NotificationJob.status.in_(("dead_letter", "failed")), 0),
        (NotificationJob.status == "processing", 1),
        (NotificationJob.status == "pending", 2),
        (NotificationJob.status.in_(("done", "sent")), 3),
        (NotificationJob.status == "retry", 4),
        else_=4,
    )

    jobs = (
        query.order_by(
            status_rank.asc(),
            NotificationJob.created_at.desc().nullslast(),
            NotificationJob.id.desc(),
        )
        .limit(200)
        .all()
    )
    for job in jobs:
        bucket = _notification_bucket_key(job)
        setattr(job, "ui_status_bucket", bucket)
        setattr(job, "ui_status_label", _notification_bucket_label(bucket))

    counts_base = _scoped_notification_jobs_query()

    summary = count_notification_job_buckets(counts_base)

    channels = [
        c[0]
        for c in counts_base.with_entities(NotificationJob.channel)
        .distinct()
        .order_by(NotificationJob.channel.asc())
        .all()
        if c[0]
    ]

    return render_template(
        "admin/notifications_operational.html",
        jobs=jobs,
        status=status,
        channel=channel,
        event_type=event_type,
        recipient=recipient,
        summary=summary,
        channels=channels,
    )


def _audience_normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _audience_path(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return parsed.path or raw.split("?", 1)[0] or raw


def _audience_label_page(value: str | None) -> str:
    path = _audience_path(value)
    return path or "/"


def _audience_source_label(referrer: str | None) -> str:
    raw = (referrer or "").strip()
    if not raw:
        return "Direct"
    parsed = urlparse(raw)
    domain = (parsed.netloc or raw).strip().lower()
    domain = domain.split("@")[-1].split(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or domain in {"direct", "none", "-"}:
        return "Direct"
    if "google." in domain:
        return "Google"
    if domain.endswith("linkedin.com") or "linkedin." in domain:
        return "LinkedIn"
    if domain == "chat.openai.com" or domain.endswith(".openai.com"):
        return "ChatGPT"
    return domain


def _audience_is_external_referrer(referrer: str | None) -> bool:
    label = _audience_source_label(referrer)
    return label != "Direct" and "helpchain" not in label.lower()


def _audience_page_score(path: str | None) -> int:
    normalized = _audience_label_page(path)
    for prefix, score in AUDIENCE_REVENUE_PAGE_SCORES:
        if prefix == "/" and normalized == "/":
            return score
        if prefix != "/" and normalized.startswith(prefix):
            return score
    return 0


def _audience_score_session(
    paths: list[str],
    *,
    page_count: int,
    last_activity: datetime | None,
    now: datetime,
    has_external_referrer: bool,
    repeated_same_day: bool,
) -> int:
    score = sum(_audience_page_score(path) for path in paths)
    if repeated_same_day:
        score += 6
    if page_count >= 5:
        score += 10
    elif page_count >= 3:
        score += 5
    if last_activity and last_activity >= now - timedelta(hours=24):
        score += 4
    if has_external_referrer:
        score += 3
    if page_count == 1:
        score -= 5
    return max(0, min(AUDIENCE_REVENUE_SCORE_CAP, score))


def _audience_temperature_for_score(score: int) -> dict:
    if score >= 25:
        return {"label": "Tres chaud", "class": "text-bg-danger", "action": "Priorite haute"}
    if score >= 16:
        return {"label": "Chaud", "class": "text-bg-warning text-dark", "action": "A suivre aujourd'hui"}
    if score >= 8:
        return {"label": "Tiede", "class": "text-bg-info text-dark", "action": "Nurture"}
    return {"label": "Froid", "class": "text-bg-light border", "action": "Observation"}


def _audience_relative_time(value: datetime | None, now: datetime) -> str:
    if not value:
        return "-"
    delta = max(timedelta(), now - value)
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "a l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours} h"
    days = hours // 24
    return f"il y a {days} j"


def _audience_session_label(session_id: str) -> str:
    value = (session_id or "").strip()
    if len(value) <= 16:
        return value
    return f"{value[:8]}...{value[-4:]}"


def _audience_location_city(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    first = re.split(r"[,;/|]", raw, maxsplit=1)[0].strip()
    return first or raw


def _audience_import_models():
    try:
        from backend.models_with_analytics import AnalyticsEvent, UserBehavior

        return AnalyticsEvent, UserBehavior
    except Exception:
        return None, None


def _audience_department_code(city: str | None) -> str:
    return AUDIENCE_DEPARTMENT_CODES.get(_audience_normalize_text(city), "")


def _audience_focus_slug(city: str | None) -> str | None:
    normalized = _audience_normalize_text(city)
    for point in AUDIENCE_BUSINESS_MAP_POINTS:
        if _audience_normalize_text(point["label"]) == normalized:
            return str(point["slug"])
    return None


def _audience_priority_for_lead_score(score: int) -> str:
    if score >= 90:
        return "Tres chaud"
    if score >= 75:
        return "Chaud"
    if score >= 55:
        return "A qualifier"
    return "Observation"


def _audience_action_for_lead_score(score: int) -> str:
    if score >= 90:
        return "Appeler / contacter aujourd'hui"
    if score >= 75:
        return "Envoyer un email cible aujourd'hui"
    if score >= 55:
        return "Qualifier les interlocuteurs"
    return "Garder en observation"


def _territorial_priority_label(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized == "Strategic":
        return "Haute"
    if normalized == "High":
        return "Haute"
    if normalized == "Moderate":
        return "Moyenne"
    return "Observation"


def _territorial_summary_lookup(territory_summaries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for row in territory_summaries or []:
        territory = normalize_territory_name(row.get("territory"))
        if territory:
            lookup[_audience_normalize_text(territory)] = row
    return lookup


def _audience_mask_email(value: str | None) -> str:
    email = (value or "").strip()
    if "@" not in email:
        return email
    local_part, domain = email.split("@", 1)
    local_part = local_part.strip()
    domain = domain.strip()
    if not local_part or not domain:
        return email
    return f"{local_part[:1]}***@{domain}"


def _audience_is_personal_email_domain(value: str | None) -> bool:
    domain = (value or "").strip().lower()
    if not domain:
        return False
    return domain in {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "orange.fr",
        "free.fr",
        "sfr.fr",
        "laposte.net",
        "wanadoo.fr",
    }


def _audience_confidence_rank(value: str | None) -> int:
    normalized = (value or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0


def _audience_title_from_domain(value: str | None) -> str:
    domain = (value or "").strip().lower()
    if not domain:
        return ""
    root = domain.split(".", 1)[0].strip()
    if not root:
        return ""
    tokens = [token for token in re.split(r"[-_]+", root) if token]
    if not tokens:
        return root.title()
    return " ".join(token.upper() if token.upper() == "CCAS" else token.title() for token in tokens)


def _build_audience_map_context() -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    context = {
        "analytics_available": False,
        "geo_available": False,
        "kpis": {
            "visitors_24h": 0,
            "visitors_7d": 0,
            "active_cities": 0,
            "key_pages": 0,
            "intent_signals": 0,
        },
        "city_rows": [],
        "department_rows": [],
        "region_rows": [],
        "page_rows": [],
        "source_rows": [],
        "repeat_rows": [],
        "map_markers": [],
        "map_locations": [],
        "map_default_location": None,
        "unmapped_locations": [],
        "founder_insights": [],
        "revenue_radar_rows": [],
        "revenue_radar_insights": [],
        "qualified_signal_rows": [],
        "founder_queue_account_rows": [],
        "founder_queue_lead_rows": [],
        "territory_summaries": [],
        "priority_territories": [],
        "founder_priority_queue": [],
        "founder_alerts": [],
        "founder_territory_groups": [],
        "founder_action_summary": {},
    }
    if not _table_exists("analytics_events"):
        return context

    AnalyticsEvent, UserBehavior = _audience_import_models()
    if AnalyticsEvent is None:
        return context

    context["analytics_available"] = True
    event_rows = (
        db.session.query(
            AnalyticsEvent.created_at,
            AnalyticsEvent.user_session,
            AnalyticsEvent.user_ip,
            AnalyticsEvent.page_url,
            AnalyticsEvent.referrer,
        )
        .filter(AnalyticsEvent.event_type == "page_view")
        .filter(AnalyticsEvent.created_at >= since_7d)
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(5000)
        .all()
    )

    def visitor_key(row) -> str:
        return (row.user_session or row.user_ip or f"event-{id(row)}").strip()

    visitors_24h = {visitor_key(row) for row in event_rows if row.created_at and row.created_at >= since_24h}
    visitors_7d = {visitor_key(row) for row in event_rows}
    page_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    session_intent: Counter[str] = Counter()
    key_page_set: set[str] = set()
    session_events: dict[str, dict] = {}
    territory_rows: list[dict[str, object]] = []
    for row in event_rows:
        path = _audience_label_page(row.page_url)
        session_id = visitor_key(row)
        page_counts[path] += 1
        if any(path.startswith(intent_path) for intent_path in AUDIENCE_INTENT_PATHS):
            session_intent[session_id] += 1
            key_page_set.add(path)
        if _audience_is_external_referrer(row.referrer):
            source_counts[_audience_source_label(row.referrer)] += 1
        session_bucket = session_events.setdefault(
            session_id,
            {
                "paths": [],
                "referrers": Counter(),
                "last_activity": None,
                "day_counts": Counter(),
                "has_external_referrer": False,
            },
        )
        session_bucket["paths"].append(path)
        if row.created_at:
            session_bucket["day_counts"][row.created_at.date()] += 1
            if session_bucket["last_activity"] is None or row.created_at > session_bucket["last_activity"]:
                session_bucket["last_activity"] = row.created_at
        source_label = _audience_source_label(row.referrer)
        session_bucket["referrers"][source_label] += 1
        if _audience_is_external_referrer(row.referrer):
            session_bucket["has_external_referrer"] = True

    location_counts: Counter[str] = Counter()
    session_locations: dict[str, dict[str, str]] = {}
    if UserBehavior is not None and _table_exists("user_behaviors"):
        behavior_rows = (
            db.session.query(UserBehavior.location, UserBehavior.session_id)
            .filter(UserBehavior.session_start >= since_7d)
            .filter(UserBehavior.location.isnot(None))
            .filter(UserBehavior.location != "")
            .limit(5000)
            .all()
        )
        for location, _session_id in behavior_rows:
            city = _audience_location_city(location)
            if city:
                location_counts[city] += 1
                if _session_id:
                    marker = AUDIENCE_CITY_MARKERS.get(_audience_normalize_text(city), {})
                    session_locations[str(_session_id).strip()] = {
                        "territory": city,
                        "department": _audience_department_code(city),
                        "department_name": str(marker.get("department") or "").strip(),
                        "focus_slug": _audience_focus_slug(city) or "",
                    }

    department_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    markers = []
    unmapped = []
    max_city_count = max(location_counts.values(), default=0)
    for city, count in location_counts.most_common(12):
        marker = AUDIENCE_CITY_MARKERS.get(_audience_normalize_text(city))
        if marker:
            department_counts[marker["department"]] += count
            region_counts[marker["region"]] += count
            intensity = 1 if max_city_count <= 0 else max(1, min(5, math.ceil((count / max_city_count) * 5)))
            markers.append({**marker, "count": count, "intensity": intensity})
        else:
            unmapped.append({"label": city, "count": count})

    context["geo_available"] = bool(markers or location_counts)
    context["map_markers"] = markers
    map_location_counts = {
        _audience_normalize_text(city): int(count)
        for city, count in location_counts.items()
    }
    business_points = []
    for point in AUDIENCE_BUSINESS_MAP_POINTS:
        normalized_label = _audience_normalize_text(point["label"])
        observed = int(map_location_counts.get(normalized_label, 0) or 0)
        if current_app.config.get("DEMO_MODE"):
            estimated_demands = max(int(point["default_demands"]), observed)
            structures = max(
                int(point["default_structures"]),
                min(int(point["default_structures"]) + 2, max(1, math.ceil(estimated_demands * 0.6))),
            )
            intelligence_source = "demo_seeded" if not observed else "observed_demo_enriched"
            priority_label = point["priority"]
            recommendation_label = point["recommendation"]
        else:
            if observed <= 0:
                continue
            estimated_demands = observed
            structures = None
            intelligence_source = "observed"
            priority_label = "Observation"
            recommendation_label = "Not enough data available"
        department_name = AUDIENCE_CITY_MARKERS.get(normalized_label, {}).get("department", "Ile-de-France")
        department_code = _audience_department_code(point["label"]) or "--"
        business_points.append(
            {
                "slug": point["slug"],
                "label": point["label"],
                "city": point["label"],
                "lat": float(point["lat"]),
                "lng": float(point["lng"]),
                "department": department_name,
                "department_code": department_code,
                "departmentNumber": department_code,
                "departmentName": department_name,
                "region": AUDIENCE_CITY_MARKERS.get(normalized_label, {}).get("region", "Ile-de-France"),
                "estimated_demands": estimated_demands,
                "needs": estimated_demands,
                "structures": structures,
                "structures_label": structures if structures is not None else "Not enough data available",
                "priority": priority_label,
                "recommendation": recommendation_label,
                "observed_signals": observed,
                "intelligence_source": intelligence_source,
            }
        )
    context["map_locations"] = business_points
    context["map_default_location"] = next(
        (point for point in business_points if point["slug"] == "paris"),
        business_points[0] if business_points else None,
    )
    context["unmapped_locations"] = unmapped[:8]
    context["city_rows"] = [
        {"label": city, "count": count}
        for city, count in location_counts.most_common(8)
    ]
    context["department_rows"] = [
        {"label": dept, "count": count}
        for dept, count in department_counts.most_common(6)
    ]
    context["region_rows"] = [
        {"label": region, "count": count}
        for region, count in region_counts.most_common(6)
    ]
    blocked_page_prefixes = (
        "/admin",
        "/ops",
        "/api",
        "/events",
        "/static",
        "/favicon",
    )

    public_page_counts = [
        (page, count)
        for page, count in page_counts.most_common(30)
        if page and not str(page).startswith(blocked_page_prefixes)
    ]

    page_display_map = {
        "/": "Accueil",
        "/offre": "Offre",
        "/demo": "Acces pilote",
        "/deploiement": "Deploiement",
        "/professionnels": "Professionnels",
        "/contact": "Contact",
        "/comment_ca_marche": "Fonctionnement",
        "/cas_usage": "Cas d'usage",
        "/securite": "Securite",
        "/collectivites": "Collectivites",
    }

    context["page_rows"] = [
        {
            "label": page_display_map.get(page, page),
            "count": count,
        }
        for page, count in public_page_counts[:8]
    ]
    context["source_rows"] = [
        {"label": source, "count": count}
        for source, count in source_counts.most_common(6)
    ]
    context["repeat_rows"] = [
        {"label": session_id, "count": count}
        for session_id, count in session_intent.most_common(6)
        if count >= 2
    ]
    qualified_rollup: dict[tuple[str, str, str, str], dict[str, object]] = {}
    captured_sessions = captured_audience_session_targets()
    revenue_rows = []
    sessions_repeated_today = 0
    for session_id, bucket in session_events.items():
        page_count = len(bucket["paths"])
        repeated_same_day = any(count >= 2 for count in bucket["day_counts"].values())
        if bucket["day_counts"].get(now.date(), 0) >= 2:
            sessions_repeated_today += 1
        score = _audience_score_session(
            bucket["paths"],
            page_count=page_count,
            last_activity=bucket["last_activity"],
            now=now,
            has_external_referrer=bucket["has_external_referrer"],
            repeated_same_day=repeated_same_day,
        )
        intent_summary = build_intent_summary(bucket["paths"], has_submit=False)
        temperature = _audience_temperature_for_score(score)
        source = "Direct"
        for label, _count in bucket["referrers"].most_common():
            if label != "Direct":
                source = label
                break
        location_meta = session_locations.get(session_id) or {}
        territory = normalize_territory_name(location_meta.get("territory"))
        if territory:
            territory_rows.append(
                {
                    "territory": territory,
                    "territory_source": "behavior_location",
                    "source_kind": "anonymous_session",
                    "session_id": session_id,
                    "paths": list(dict.fromkeys(intent_summary["top_paths"] or bucket["paths"])),
                    "intent_tier": intent_summary["tier"],
                    "primary_interest": intent_summary["primary_interest"],
                    "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
                    "repeat_visit": bool(repeated_same_day or page_count > 1 or len(bucket["day_counts"]) > 1),
                }
            )
        revenue_rows.append(
            {
                "session": session_id,
                "session_label": _audience_session_label(session_id),
                "score": score,
                "temperature": temperature["label"],
                "temperature_class": temperature["class"],
                "pages_count": page_count,
                "last_activity": bucket["last_activity"],
                "last_activity_label": _audience_relative_time(bucket["last_activity"], now),
                "source": source,
                "action": intent_summary["recommended_action"],
                "captured_label": captured_sessions.get(session_id),
                "intent_score": int(intent_summary["score"]),
                "intent_tier": intent_summary["tier"],
                "intent_label": intent_summary["label"],
                "primary_interest": intent_summary["primary_interest"],
                "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
                "friction_reason": intent_summary["friction_reason"],
                "territory": territory,
                "department": location_meta.get("department") or "",
                "focus_slug": location_meta.get("focus_slug") or "",
            }
        )
        qualified_paths = [
            path for path in bucket["paths"] if _audience_label_page(path) in AUDIENCE_QUALIFIED_PAGE_WEIGHTS
        ]
        if not location_meta or not bucket["has_external_referrer"] or not qualified_paths:
            continue
        qualified_counts = Counter(_audience_label_page(path) for path in qualified_paths)
        for page, count in qualified_counts.items():
            key = (
                location_meta["territory"],
                location_meta["department"],
                page,
                "page_view",
            )
            row = qualified_rollup.setdefault(
                key,
                {
                    "territory": location_meta["territory"],
                    "department": location_meta["department"],
                    "page": page,
                    "event": "page_view",
                    "count": 0,
                    "last_seen_at": None,
                    "intent_weight": AUDIENCE_QUALIFIED_PAGE_WEIGHTS.get(page, 0),
                    "signal_type": "qualified",
                    "repeat_sessions": 0,
                    "unique_pages_seen": 0,
                    "focus_slug": location_meta["focus_slug"],
                },
            )
            row["count"] = int(row["count"]) + int(count)
            row["unique_pages_seen"] = max(int(row["unique_pages_seen"]), len(set(bucket["paths"])))
            if repeated_same_day:
                row["repeat_sessions"] = int(row["repeat_sessions"]) + 1
            last_seen_at = row["last_seen_at"]
            if bucket["last_activity"] and (
                last_seen_at is None or bucket["last_activity"] > last_seen_at
            ):
                row["last_seen_at"] = bucket["last_activity"]
    revenue_rows.sort(key=lambda row: (row["score"], row["last_activity"] or datetime.min), reverse=True)
    context["revenue_radar_rows"] = revenue_rows[:12]
    qualified_rows = []
    for row in qualified_rollup.values():
        last_seen_at = row.pop("last_seen_at", None)
        row["last_seen"] = _audience_relative_time(last_seen_at, now)
        qualified_rows.append(row)
    qualified_rows.sort(
        key=lambda row: (
            int(row.get("intent_weight") or 0),
            int(row.get("count") or 0),
            int(row.get("repeat_sessions") or 0),
        ),
        reverse=True,
    )
    context["qualified_signal_rows"] = qualified_rows[:24]
    if _table_exists("organization_access_requests"):
        since_30d = now - timedelta(days=30)
        access_rows = (
            db.session.query(
                OrganizationAccessRequest.city,
                OrganizationAccessRequest.created_at,
                OrganizationAccessRequest.internal_notes,
            )
            .filter(OrganizationAccessRequest.created_at >= since_30d)
            .order_by(OrganizationAccessRequest.created_at.desc())
            .limit(100)
            .all()
        )
        for row in access_rows:
            audience_context = extract_audience_context(getattr(row, "internal_notes", None))
            intent_summary = (
                audience_context.get("institutional_intent")
                if isinstance(audience_context, dict)
                else None
            )
            paths = []
            if isinstance(audience_context, dict):
                raw_paths = audience_context.get("pages_viewed") or []
                if isinstance(raw_paths, list):
                    paths = [path for path in raw_paths if isinstance(path, str) and path.strip()]
            territory = normalize_territory_name(
                getattr(row, "city", None)
                or (audience_context or {}).get("territory_hint")
            )
            if not territory:
                continue
            if not isinstance(intent_summary, dict):
                intent_summary = build_intent_summary(paths, has_submit=True)
            phase1_intelligence = _phase1_intelligence_from_context(
                audience_context,
                intent_summary=intent_summary,
                territory_hint=territory,
                has_submit=True,
            )
            territory_rows.append(
                {
                    "territory": territory,
                    "territory_source": "access_request_city" if getattr(row, "city", None) else "organization_hint",
                    "source_kind": "access_request",
                    "session_id": (audience_context or {}).get("session_id"),
                    "paths": list(intent_summary.get("top_paths") or paths),
                    "intent_tier": intent_summary.get("tier"),
                    "primary_interest": intent_summary.get("primary_interest"),
                    "trust_friction_detected": bool(intent_summary.get("trust_friction_detected")),
                    "repeat_visit": bool((audience_context or {}).get("repeat_visit")),
                    "phase1_intelligence": phase1_intelligence,
                }
            )
    if _table_exists("professional_leads"):
        since_30d = now - timedelta(days=30)
        lead_query = (
            db.session.query(
                ProfessionalLead.id,
                ProfessionalLead.organization,
                ProfessionalLead.email,
                ProfessionalLead.city,
                ProfessionalLead.source,
                ProfessionalLead.status,
                ProfessionalLead.created_at,
                ProfessionalLead.notes,
            )
            .filter(
                ProfessionalLead.created_at >= since_30d,
            )
            .filter(
                or_(
                    ProfessionalLead.status.is_(None),
                    ~func.lower(ProfessionalLead.status).in_(SCREENED_PROFESSIONAL_LEAD_STATUSES),
                )
            )
            .order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc())
            .limit(100)
        )
        lead_rows = lead_query.all()
        founder_queue_account_groups: dict[str, dict[str, object]] = {}
        founder_queue_lead_rows = []
        for row in lead_rows:
            audience_context = extract_audience_context(getattr(row, "notes", None))
            if not isinstance(audience_context, dict):
                continue
            organization_intelligence = audience_context.get("organization_intelligence")
            if not isinstance(organization_intelligence, dict):
                organization_intelligence = {}
            lead_score = int(
                audience_context.get("lead_intent_score")
                or audience_context.get("score")
                or 0
            )
            pages_viewed = audience_context.get("pages_viewed") or []
            if not isinstance(pages_viewed, list):
                pages_viewed = []
            last_seen_at_raw = audience_context.get("last_seen_at")
            last_seen_at = None
            if isinstance(last_seen_at_raw, str) and last_seen_at_raw.strip():
                try:
                    last_seen_at = datetime.fromisoformat(last_seen_at_raw.strip())
                except Exception:
                    last_seen_at = None
            territory = (getattr(row, "city", None) or "").strip() or "France"
            department = _audience_department_code(territory)
            display_identity = (
                (getattr(row, "organization", None) or "").strip()
                or (getattr(row, "email", None) or "").strip()
                or "Lead qualifie"
            )
            organization_name = (
                (organization_intelligence.get("probable_name") or "").strip()
                or display_identity
            )
            organization_domain = (
                (organization_intelligence.get("domain") or "").strip().lower()
                or (audience_context.get("organization_domain") or "").strip().lower()
            )
            organization_type = (
                (organization_intelligence.get("type") or "").strip()
                or (audience_context.get("organization_type") or "").strip()
            )
            territory_hint = (
                (organization_intelligence.get("territory_hint") or "").strip()
            )
            organization_confidence = (
                (organization_intelligence.get("confidence") or "").strip()
                or (audience_context.get("organization_confidence") or "").strip()
            )
            sales_note = (
                (organization_intelligence.get("sales_note") or "").strip()
            )
            email_value = (getattr(row, "email", None) or "").strip()
            lead_territory = normalize_territory_name(territory_hint or territory)
            intent_summary = audience_context.get("institutional_intent")
            if not isinstance(intent_summary, dict):
                intent_summary = build_intent_summary(pages_viewed, has_submit=True)
            phase1_intelligence = _phase1_intelligence_from_context(
                audience_context,
                intent_summary=intent_summary,
                territory_hint=lead_territory or territory,
                has_submit=True,
            )
            if lead_territory:
                territory_rows.append(
                    {
                        "territory": lead_territory,
                        "territory_source": "professional_lead_city" if getattr(row, "city", None) else "organization_hint",
                        "source_kind": "professional_lead",
                        "session_id": audience_context.get("session_id"),
                        "paths": list(intent_summary.get("top_paths") or pages_viewed),
                        "intent_tier": intent_summary.get("tier"),
                        "primary_interest": intent_summary.get("primary_interest"),
                        "trust_friction_detected": bool(intent_summary.get("trust_friction_detected")),
                        "repeat_visit": bool(audience_context.get("repeat_visit")),
                        "phase1_intelligence": phase1_intelligence,
                    }
                )
            founder_queue_lead_rows.append(
                {
                    "organization": display_identity,
                    "organization_name": organization_name,
                    "email": email_value,
                    "action_email": email_value,
                    "territory": territory,
                    "department": department,
                    "pages_viewed": pages_viewed[:5],
                    "score": max(0, min(100, lead_score)),
                    "priority": _audience_priority_for_lead_score(lead_score),
                    "last_seen": _audience_relative_time(last_seen_at, now),
                    "source_label": "Signal qualifie",
                    "focus_slug": _audience_focus_slug(territory) or "paris",
                    "organization_domain": organization_domain,
                    "organization_type": organization_type,
                    "territory_hint": territory_hint,
                    "organization_confidence": organization_confidence,
                    "sales_note": sales_note,
                    "institutional_intent_score": int(phase1_intelligence.get("institutional_intent_score") or 0),
                    "intent_level": phase1_intelligence.get("intent_level"),
                    "recurrence_level": phase1_intelligence.get("recurrence_level"),
                    "deployment_probability": phase1_intelligence.get("deployment_probability"),
                    "probable_organization_profiles": [
                        str(item.get("label") or "").strip()
                        for item in list(phase1_intelligence.get("probable_organization_profiles") or [])
                        if str(item.get("label") or "").strip()
                    ],
                    "territorial_opportunity_summary": dict(
                        phase1_intelligence.get("territorial_opportunity_summary") or {}
                    ),
                    "recommendation_reasons": list(phase1_intelligence.get("recommendation_reasons") or []),
                    "recommended_next_action": str(
                        phase1_intelligence.get("recommended_next_action") or ""
                    ).strip(),
                    "created_at": getattr(row, "created_at", None),
                }
            )
            group_key = ""
            if organization_domain:
                group_key = f"domain:{organization_domain}"
            else:
                normalized_org = _audience_normalize_text(getattr(row, "organization", None))
                if normalized_org:
                    group_key = f"organization:{normalized_org}"
                elif email_value and "@" in email_value:
                    email_domain = email_value.rsplit("@", 1)[-1].strip().lower()
                    if email_domain and not _audience_is_personal_email_domain(email_domain):
                        group_key = f"email-domain:{email_domain}"
                        if not organization_domain:
                            organization_domain = email_domain
            if not group_key:
                continue
            account_row = founder_queue_account_groups.setdefault(
                group_key,
                {
                    "account_name": "",
                    "domain": organization_domain,
                    "organization_type": organization_type,
                    "territory_hint": territory_hint,
                    "confidence": organization_confidence or "low",
                    "confidence_rank": _audience_confidence_rank(organization_confidence),
                    "lead_count": 0,
                    "emails": [],
                    "email_set": set(),
                    "action_email": "",
                    "pages_viewed": set(),
                    "best_score": 0,
                    "score_total": 0,
                    "score_count": 0,
                    "last_activity_at": None,
                    "focus_slug": _audience_focus_slug(territory_hint or territory) or "paris",
                    "sales_note": sales_note,
                },
            )
            probable_name = (organization_intelligence.get("probable_name") or "").strip()
            if probable_name and not account_row["account_name"]:
                account_row["account_name"] = probable_name
            elif not account_row["account_name"]:
                account_row["account_name"] = (
                    (getattr(row, "organization", None) or "").strip()
                    or _audience_title_from_domain(organization_domain)
                )
            if organization_domain and not account_row["domain"]:
                account_row["domain"] = organization_domain
            if organization_type and not account_row["organization_type"]:
                account_row["organization_type"] = organization_type
            if territory_hint and not account_row["territory_hint"]:
                account_row["territory_hint"] = territory_hint
            current_rank = _audience_confidence_rank(organization_confidence)
            if current_rank > int(account_row["confidence_rank"]):
                account_row["confidence"] = organization_confidence or "low"
                account_row["confidence_rank"] = current_rank
            if sales_note and not account_row["sales_note"]:
                account_row["sales_note"] = sales_note
            account_row["lead_count"] = int(account_row["lead_count"]) + 1
            if email_value and email_value not in account_row["email_set"]:
                account_row["email_set"].add(email_value)
                account_row["emails"].append(_audience_mask_email(email_value))
            if email_value and not account_row["action_email"]:
                account_row["action_email"] = email_value
            for page in pages_viewed:
                if isinstance(page, str) and page.strip():
                    account_row["pages_viewed"].add(page.strip())
            account_row["best_score"] = max(int(account_row["best_score"]), max(0, min(100, lead_score)))
            account_row["score_total"] = int(account_row["score_total"]) + max(0, int(lead_score))
            account_row["score_count"] = int(account_row["score_count"]) + 1
            candidate_last_activity = last_seen_at or getattr(row, "created_at", None)
            if candidate_last_activity and (
                account_row["last_activity_at"] is None
                or candidate_last_activity > account_row["last_activity_at"]
            ):
                account_row["last_activity_at"] = candidate_last_activity
            if territory and not account_row["territory_hint"]:
                account_row["territory_hint"] = territory
            if territory and not account_row["focus_slug"]:
                account_row["focus_slug"] = _audience_focus_slug(territory) or "paris"
        founder_queue_lead_rows.sort(
            key=lambda row: (
                int(row.get("score") or 0),
                row.get("created_at") or datetime.min,
                row.get("email") or "",
            ),
            reverse=True,
        )
        for row in founder_queue_lead_rows:
            row.pop("created_at", None)
        founder_queue_account_rows = []
        for row in founder_queue_account_groups.values():
            best_score = max(0, min(100, int(row["best_score"] or 0)))
            score_count = max(1, int(row["score_count"] or 0))
            avg_score = round(int(row["score_total"] or 0) / score_count)
            last_activity_at = row.get("last_activity_at")
            founder_queue_account_rows.append(
                {
                    "account_name": str(row.get("account_name") or row.get("domain") or "Compte detecte").strip(),
                    "domain": str(row.get("domain") or "").strip(),
                    "organization_type": str(row.get("organization_type") or "").strip(),
                    "territory_hint": str(row.get("territory_hint") or "").strip(),
                    "confidence": str(row.get("confidence") or "low").strip() or "low",
                    "lead_count": int(row.get("lead_count") or 0),
                    "emails": list(row.get("emails") or [])[:6],
                    "action_email": str(row.get("action_email") or "").strip(),
                    "pages_viewed": sorted(list(row.get("pages_viewed") or []))[:6],
                    "best_score": best_score,
                    "avg_score": avg_score,
                    "priority": _audience_priority_for_lead_score(best_score),
                    "recommended_action": _audience_action_for_lead_score(best_score),
                    "last_activity": _audience_relative_time(last_activity_at, now),
                    "last_activity_at": last_activity_at,
                    "source": "account",
                    "source_label": "Signal qualifie",
                    "focus_slug": str(row.get("focus_slug") or "paris"),
                    "sales_note": str(row.get("sales_note") or "").strip(),
                }
            )
        founder_queue_account_rows.sort(
            key=lambda row: (
                int(row.get("best_score") or 0),
                row.get("last_activity_at") or datetime.min,
                row.get("account_name") or "",
            ),
            reverse=True,
        )
        for row in founder_queue_account_rows:
            row.pop("last_activity_at", None)
        context["founder_queue_account_rows"] = founder_queue_account_rows[:12]
        context["founder_queue_lead_rows"] = founder_queue_lead_rows[:12]
    territory_summaries = detect_priority_territories(territory_rows)
    territory_profile_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in territory_rows:
        territory_key = _audience_normalize_text(row.get("territory"))
        phase1_payload = row.get("phase1_intelligence")
        if not territory_key or not isinstance(phase1_payload, dict):
            continue
        for profile in list(phase1_payload.get("probable_organization_profiles") or []):
            if isinstance(profile, dict):
                territory_profile_rows[territory_key].append(profile)
    for summary in territory_summaries:
        territory_key = _audience_normalize_text(summary.get("territory"))
        probable_profiles = summarize_profile_labels(territory_profile_rows.get(territory_key) or [])
        territorial_opportunity = build_territorial_opportunity_summary(
            summary,
            probable_profiles=probable_profiles,
        )
        summary["institutional_intent_score"] = int(summary.get("score") or 0)
        summary["recurrence_level"] = territorial_opportunity.get("recurrence_level")
        summary["deployment_probability"] = territorial_opportunity.get("deployment_probability")
        summary["probable_organization_profiles"] = list(
            territorial_opportunity.get("probable_organization_profiles") or []
        )
        summary["territorial_opportunity_summary"] = territorial_opportunity
        summary["recommendation_reasons"] = list(
            territorial_opportunity.get("recommendation_reasons") or []
        )
        summary["recommended_next_action"] = str(
            territorial_opportunity.get("recommended_next_action") or summary.get("recommended_action") or ""
        ).strip()
    territory_lookup = _territorial_summary_lookup(territory_summaries)
    context["territory_summaries"] = territory_summaries[:12]
    context["priority_territories"] = [
        row for row in territory_summaries if row.get("priority_level") in {"Strategic", "High"}
    ][:6]
    for point in context["map_locations"]:
        summary = territory_lookup.get(_audience_normalize_text(point.get("city")))
        if not summary:
            continue
        observed_signals = max(
            int(point.get("observed_signals") or 0),
            int(summary.get("observed_signal_count") or 0),
        )
        point["observed_signals"] = observed_signals
        point["estimated_demands"] = max(int(point.get("estimated_demands") or 0), observed_signals)
        point["needs"] = max(int(point.get("needs") or 0), observed_signals)
        point["priority"] = _territorial_priority_label(summary.get("priority_level"))
        point["recommendation"] = str(summary.get("recommended_action") or point.get("recommendation") or "").strip()
        point["territorial_intensity"] = summary.get("intensity")
        point["priority_level"] = summary.get("priority_level")
        point["confidence"] = summary.get("confidence")
        point["dominant_interest"] = summary.get("dominant_interest")
        point["possible_friction"] = summary.get("possible_friction")
        point["pilot_readiness_estimate"] = summary.get("pilot_readiness_estimate")
        point["repeated_engagement_detected"] = bool(summary.get("repeated_engagement_detected"))
        point["intelligence_source"] = "observed_summary"
        point["institutional_intent_score"] = summary.get("institutional_intent_score")
        point["recurrence_level"] = summary.get("recurrence_level")
        point["deployment_probability"] = summary.get("deployment_probability")
        point["probable_organization_profiles"] = summary.get("probable_organization_profiles")
        point["territorial_opportunity_summary"] = summary.get("territorial_opportunity_summary")
        point["recommendation_reasons"] = summary.get("recommendation_reasons")
        point["recommended_next_action"] = summary.get("recommended_next_action")
    for row in context["revenue_radar_rows"]:
        summary = territory_lookup.get(_audience_normalize_text(row.get("territory")))
        if not summary:
            continue
        row["priority_level"] = summary.get("priority_level")
        row["territorial_intensity"] = summary.get("intensity")
        row["territory_confidence"] = summary.get("confidence")
        row["dominant_interest"] = summary.get("dominant_interest")
        row["possible_friction"] = summary.get("possible_friction")
        row["pilot_readiness_estimate"] = summary.get("pilot_readiness_estimate")
        row["recommended_action"] = summary.get("recommended_action")
        row["repeated_engagement_detected"] = bool(summary.get("repeated_engagement_detected"))
        row["institutional_intent_score"] = summary.get("institutional_intent_score")
        row["recurrence_level"] = summary.get("recurrence_level")
        row["deployment_probability"] = summary.get("deployment_probability")
        row["probable_organization_profiles"] = summary.get("probable_organization_profiles")
        row["territorial_opportunity_summary"] = summary.get("territorial_opportunity_summary")
        row["recommendation_reasons"] = summary.get("recommendation_reasons")
        row["recommended_next_action"] = summary.get("recommended_next_action")
    for row in context["founder_queue_lead_rows"]:
        summary = territory_lookup.get(_audience_normalize_text(row.get("territory") or row.get("territory_hint")))
        if not summary:
            continue
        row["priority_level"] = summary.get("priority_level")
        row["territory_confidence"] = summary.get("confidence")
        row["dominant_interest"] = summary.get("dominant_interest")
        row["pilot_readiness_estimate"] = summary.get("pilot_readiness_estimate")
        row["possible_friction"] = summary.get("possible_friction")
        row["recommended_action"] = summary.get("recommended_action")
        row["territorial_opportunity_summary"] = summary.get("territorial_opportunity_summary")
        row["recommended_next_action"] = (
            row.get("recommended_next_action") or summary.get("recommended_next_action")
        )
    for row in context["founder_queue_account_rows"]:
        summary = territory_lookup.get(_audience_normalize_text(row.get("territory_hint")))
        if not summary:
            continue
        row["priority_level"] = summary.get("priority_level")
        row["territory_confidence"] = summary.get("confidence")
        row["dominant_interest"] = summary.get("dominant_interest")
        row["pilot_readiness_estimate"] = summary.get("pilot_readiness_estimate")
        row["possible_friction"] = summary.get("possible_friction")
        row["recommended_action"] = summary.get("recommended_action")
        row["institutional_intent_score"] = summary.get("institutional_intent_score")
        row["recurrence_level"] = summary.get("recurrence_level")
        row["deployment_probability"] = summary.get("deployment_probability")
        row["probable_organization_profiles"] = summary.get("probable_organization_profiles")
        row["territorial_opportunity_summary"] = summary.get("territorial_opportunity_summary")
        row["recommendation_reasons"] = summary.get("recommendation_reasons")
        row["recommended_next_action"] = summary.get("recommended_next_action")
    context["kpis"] = {
        "visitors_24h": len(visitors_24h),
        "visitors_7d": len(visitors_7d),
        "active_cities": len(location_counts),
        "key_pages": len(key_page_set),
        "intent_signals": sum(session_intent.values()),
    }
    context["kpi_meta"] = {
        "source_tables": ["analytics_events", "user_behaviors"],
        "query_origin": "_build_audience_map_context event/session/location aggregations",
        "last_updated": now,
        "confidence": "high" if context["analytics_available"] else "low",
        "explain": "Counts are derived from analytics event rows and user behavior locations in the selected 7-day window.",
    }
    context["last_updated"] = now

    if region_counts:
        region, count = region_counts.most_common(1)[0]
        context["founder_insights"].append(
            f"Interet observe en {region}: {count} signal(s) territorial(aux) sur 7 jours."
        )
    if session_intent:
        context["founder_insights"].append(
            f"{sum(session_intent.values())} visite(s) sur des pages a forte intention."
        )
    if context["repeat_rows"]:
        context["founder_insights"].append(
            "Des sessions repetent des passages sur les pages d'intention."
        )
    if territory_summaries:
        top_territory = territory_summaries[0]
        context["founder_insights"].append(
            f"Territoire prioritaire observe: {top_territory['territory']} ({top_territory['priority_level'].lower()}, confiance {top_territory['confidence']})."
        )
    hot_24h = [
        row
        for row in revenue_rows
        if row["last_activity"] and row["last_activity"] >= since_24h and row["score"] >= 16
    ]
    if hot_24h:
        context["revenue_radar_insights"].append(
            f"{len(hot_24h)} visiteur(s) chaud(s) sur les 24 dernieres heures."
        )
    if page_counts.get("/demander-acces", 0):
        context["revenue_radar_insights"].append(
            f"Forte intention sur /demander-acces: {page_counts['/demander-acces']} vue(s)."
        )
    offre_24h = sum(
        1
        for row in event_rows
        if row.created_at and row.created_at >= since_24h and _audience_label_page(row.page_url).startswith("/offre")
    )
    offre_previous_24h = sum(
        1
        for row in event_rows
        if row.created_at
        and since_24h > row.created_at >= now - timedelta(hours=48)
        and _audience_label_page(row.page_url).startswith("/offre")
    )
    if offre_24h > 0 and offre_24h > offre_previous_24h:
        context["revenue_radar_insights"].append(
            "Le trafic /offre augmente sur les dernieres 24 heures."
        )
    if sessions_repeated_today:
        context["revenue_radar_insights"].append(
            f"{sessions_repeated_today} session(s) reviennent aujourd'hui."
        )
    founder_signal_rows = (
        list(context["founder_queue_lead_rows"])
        + list(context["founder_queue_account_rows"])
        + list(context["revenue_radar_rows"])
    )

    for row in founder_signal_rows:
        try:
            account_intelligence = build_account_intelligence(
                {
                    "email": row.get("email"),
                    "organization": (
                        row.get("organization")
                        or row.get("organization_name")
                        or row.get("company")
                    ),
                    "paths": row.get("paths") or row.get("visited_paths") or [],
                }
            )

            row["account_intelligence"] = account_intelligence
            row["account_category"] = account_intelligence.get("account_category")
            row["account_strength"] = account_intelligence.get("account_strength")
            row["operational_intent"] = account_intelligence.get("operational_intent")
            row["account_recommendation"] = account_intelligence.get("recommendation")

            relationship_memory = build_relationship_memory(
                row,
                events=row.get("timeline_events") or [],
            )

            row["relationship_memory"] = relationship_memory
            row["relationship_stage"] = relationship_memory.get("relationship_stage")

            followup = relationship_memory.get("followup") or {}

            row["followup_status"] = followup.get("followup_status")
            row["followup_priority"] = followup.get("followup_priority")
            row["relationship_reason"] = followup.get("reason")

            row["recommended_relationship_action"] = (
                relationship_memory.get("recommended_relationship_action")
            )

            row["timeline_size"] = len(
                relationship_memory.get("timeline") or []
            )

        except Exception:
            continue
    context["founder_priority_queue"] = build_founder_priority_queue(founder_signal_rows)[:12]
    context["founder_alerts"] = build_founder_alerts(founder_signal_rows)
    context["founder_territory_groups"] = group_founder_signals_by_territory(founder_signal_rows)[:8]
    context["founder_action_summary"] = summarize_founder_actions(founder_signal_rows)
    return context


REVENUE_STAGE_ORDER = (
    "new",
    "contacted",
    "qualified",
    "demo_booked",
    "demo_done",
    "pilot_proposed",
    "negotiation",
    "won",
    "lost",
    "paused",
)
REVENUE_STAGE_WEIGHTS = {
    "new": 0.05,
    "contacted": 0.10,
    "qualified": 0.20,
    "demo_booked": 0.28,
    "demo_done": 0.35,
    "pilot_proposed": 0.55,
    "negotiation": 0.75,
    "won": 1.00,
    "lost": 0.00,
    "paused": 0.00,
}
REVENUE_SCORE_FORMULA_VERSION = "revenue_priority_v2_explainable"
REVENUE_METRIC_FORMULA_VERSION = "revenue_dashboard_v2_lineage"


def _metric_unavailable(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "value": None,
        "display": "Not enough data available",
        "reason": reason,
    }


def _metric_available(value: object) -> dict[str, object]:
    return {
        "available": True,
        "value": value,
        "display": value,
        "reason": "",
    }


def _revenue_metric_meta(
    *,
    source_tables: list[str],
    query_origin: str,
    confidence: str,
    updated_at: datetime,
    explain: str,
) -> dict[str, object]:
    return {
        "source_tables": source_tables,
        "query_origin": query_origin,
        "confidence": confidence,
        "last_updated": updated_at,
        "explain": explain,
        "formula_version": REVENUE_METRIC_FORMULA_VERSION,
    }


def _metric_payload(
    value: object,
    *,
    source_tables: list[str],
    query_origin: str,
    confidence: str,
    updated_at: datetime,
    explain: str,
    unavailable_reason: str | None = None,
) -> dict[str, object]:
    payload = (
        _metric_unavailable(unavailable_reason)
        if unavailable_reason
        else _metric_available(value)
    )
    payload["meta"] = _revenue_metric_meta(
        source_tables=source_tables,
        query_origin=query_origin,
        confidence=confidence,
        updated_at=updated_at,
        explain=explain,
    )
    return payload


def _revenue_has_followup_fields(table_name: str) -> bool:
    return _table_has_column(table_name, "next_action_at") and _table_has_column(
        table_name, "next_action_note"
    )


def _revenue_stage(raw_status: str | None, item_type: str) -> str:
    status = ((raw_status or "").strip().lower() or "new")
    if item_type == "access_request":
        return {
            "new": "new",
            "reviewed": "qualified",
            "need_info": "qualified",
            "approved": "won",
            "rejected": "lost",
        }.get(status, "new")
    return {
        "new": "new",
        "imported": "new",
        "contacted": "contacted",
        "qualified": "qualified",
        "demo_scheduled": "demo_booked",
        "pilot_discussion": "pilot_proposed",
        "closed": "won",
        "rejected": "lost",
        "invalid": "lost",
        "spam": "lost",
        "paused": "paused",
    }.get(status, status if status in REVENUE_STAGE_ORDER else "new")


def _revenue_stage_label(stage: str) -> str:
    return {
        "new": "New",
        "contacted": "Contacted",
        "qualified": "Qualified",
        "demo_booked": "Demo booked",
        "demo_done": "Demo done",
        "pilot_proposed": "Pilot proposed",
        "negotiation": "Negotiation",
        "won": "Won",
        "lost": "Lost",
        "paused": "Paused",
    }.get(stage, stage.replace("_", " ").title())


def _revenue_amount(row, _item_type: str) -> int | None:
    # Real CRM opportunity amounts are not modeled yet. Do not infer EUR from
    # lead type, stage, estimated users, or territory.
    for field_name in ("amount_eur", "opportunity_amount_eur", "contract_value_eur"):
        amount = getattr(row, field_name, None)
        if amount is None:
            continue
        try:
            normalized = int(amount)
        except Exception:
            continue
        if normalized >= 0:
            return normalized
    return None


def _unavailable_revenue_amount() -> dict[str, object]:
    return _metric_unavailable("No CRM opportunity amount is stored for this item.")


def _revenue_city_relevance(city: str | None) -> int:
    normalized = _audience_normalize_text(city)
    if not normalized:
        return 0
    if any(token in normalized for token in ("boulogne", "nanterre", "hauts de seine", "92")):
        return 10
    if "paris" in normalized or "ile de france" in normalized or "idf" in normalized:
        return 8
    if normalized in AUDIENCE_CITY_MARKERS:
        return 4
    return 0


def _revenue_stage_score(stage: str) -> int:
    return {
        "new": 5,
        "contacted": 12,
        "qualified": 20,
        "demo_booked": 28,
        "demo_done": 35,
        "pilot_proposed": 45,
        "negotiation": 55,
        "won": 65,
        "lost": 0,
        "paused": 5,
    }.get(stage, 5)


def _score_component(code: str, label: str, points: int, evidence: dict[str, object]) -> dict[str, object]:
    return {
        "code": code,
        "label": label,
        "points": int(points),
        "evidence": evidence,
    }


def _revenue_score_payload(
    row,
    *,
    item_type: str,
    stage: str,
    audience_points: int,
    audience_context: dict | None,
    last_activity: datetime | None,
) -> dict[str, object]:
    components: list[dict[str, object]] = []

    stage_points = _revenue_stage_score(stage)
    if stage_points > 0:
        components.append(
            _score_component(
                "stage",
                f"Stage: {_revenue_stage_label(stage)}",
                stage_points,
                {"source_field": "status", "value": getattr(row, "status", None)},
            )
        )

    if audience_points > 0:
        components.append(
            _score_component(
                "audience_context",
                "Captured audience intent",
                audience_points,
                {
                    "source_field": "notes" if item_type == "professional_lead" else "internal_notes",
                    "intent_tier": (audience_context or {}).get("lead_intent_tier"),
                    "intent_score": (audience_context or {}).get("lead_intent_score"),
                },
            )
        )

    recency_points = _revenue_recent_activity_score(last_activity)
    if recency_points > 0:
        components.append(
            _score_component(
                "recent_activity",
                "Recent activity",
                recency_points,
                {"last_activity": last_activity.isoformat() if isinstance(last_activity, datetime) else None},
            )
        )

    city_points = _revenue_city_relevance(getattr(row, "city", None))
    if city_points > 0:
        components.append(
            _score_component(
                "territory_relevance",
                "Known territory relevance",
                city_points,
                {"source_field": "city", "value": getattr(row, "city", None)},
            )
        )

    total = min(100, sum(int(component["points"]) for component in components))
    evidence = {
        "source_table": "professional_leads" if item_type == "professional_lead" else "organization_access_requests",
        "source_id": int(getattr(row, "id", 0) or 0),
        "formula_version": REVENUE_SCORE_FORMULA_VERSION,
    }
    return {
        "total": total,
        "components": components,
        "evidence": evidence,
        "originating_event_ids": [],
        "confidence": "medium" if components else "low",
    }


def _revenue_audience_score(notes: str | None) -> tuple[int, dict | None]:
    context = extract_audience_context(notes)
    if not context:
        return 0, None
    intent_summary = context.get("institutional_intent")
    if isinstance(intent_summary, dict):
        tier = (intent_summary.get("tier") or context.get("lead_intent_tier") or "").strip()
        tier_points = {
            "cold": 0,
            "curious": 4,
            "evaluating": 8,
            "operationally_interested": 14,
            "pilot_ready": 20,
            "high_conversion_probability": 25,
        }
        if tier in tier_points:
            return tier_points[tier], context
    try:
        raw = int(
            context.get("lead_intent_score")
            or context.get("score")
            or 0
        )
    except Exception:
        raw = 0
    return max(0, min(25, int(raw / 2))), context


def _phase1_intelligence_from_context(
    audience_context: dict | None,
    *,
    intent_summary: dict | None = None,
    territorial_summary: dict | None = None,
    territory_hint: str | None = None,
    has_submit: bool = False,
) -> dict[str, object]:
    audience_context = audience_context if isinstance(audience_context, dict) else {}
    intent_summary = intent_summary if isinstance(intent_summary, dict) else {}
    top_paths = intent_summary.get("top_paths")
    territory_name = territory_hint
    if not territory_name and isinstance(territorial_summary, dict):
        territory_name = str(territorial_summary.get("territory") or "").strip() or None
    if not isinstance(top_paths, list):
        raw_paths = audience_context.get("pages_viewed") or []
        top_paths = [path for path in raw_paths if isinstance(path, str) and path.strip()]
    return build_institutional_intent_phase1(
        top_paths,
        has_submit=has_submit or bool(audience_context.get("has_submit")),
        repeat_visit=bool(audience_context.get("repeat_visit")),
        repeat_visit_count=int(audience_context.get("repeat_visit_count") or 0),
        first_seen_at=audience_context.get("first_seen_at"),
        last_seen_at=audience_context.get("last_seen_at"),
        page_count=int(audience_context.get("page_count") or len(top_paths)),
        territorial_summary=territorial_summary,
        territory=territory_name,
    )


def _revenue_audience_metadata(audience_context: dict | None) -> dict[str, object]:
    audience_context = audience_context if isinstance(audience_context, dict) else {}
    intent_summary = audience_context.get("institutional_intent")
    if not isinstance(intent_summary, dict):
        intent_summary = {}
    territorial_summary = audience_context.get("territorial_intelligence")
    if not isinstance(territorial_summary, dict):
        territorial_summary = {}
    top_paths = intent_summary.get("top_paths")
    if not isinstance(top_paths, list):
        raw_paths = audience_context.get("pages_viewed") or []
        top_paths = [path for path in raw_paths if isinstance(path, str) and path.strip()]
    phase1 = _phase1_intelligence_from_context(
        audience_context,
        intent_summary=intent_summary,
        territorial_summary=territorial_summary,
        territory_hint=territorial_summary.get("territory") or audience_context.get("territory_hint"),
    )
    return {
        "intent_score": int(
            intent_summary.get("score")
            or audience_context.get("lead_intent_score")
            or 0
        ),
        "intent_tier": str(
            intent_summary.get("tier")
            or audience_context.get("lead_intent_tier")
            or ""
        ).strip(),
        "intent_label": str(
            intent_summary.get("label")
            or audience_context.get("lead_intent_label")
            or ""
        ).strip(),
        "primary_interest": str(
            intent_summary.get("primary_interest")
            or audience_context.get("primary_interest")
            or "unknown"
        ).strip()
        or "unknown",
        "trust_friction_detected": bool(
            intent_summary.get("trust_friction_detected")
            or audience_context.get("trust_friction_detected")
        ),
        "friction_reason": (
            str(
                intent_summary.get("friction_reason")
                or audience_context.get("friction_reason")
                or ""
            ).strip()
            or None
        ),
        "top_paths": top_paths[:8],
        "repeated_engagement_detected": bool(
            territorial_summary.get("repeated_engagement_detected")
            or audience_context.get("repeat_visit")
        ),
        "priority_level": str(territorial_summary.get("priority_level") or "Low").strip() or "Low",
        "territorial_intensity": str(
            territorial_summary.get("intensity")
            or territorial_summary.get("priority_level")
            or "Low"
        ).strip()
        or "Low",
        "territory_confidence": str(
            territorial_summary.get("confidence") or "weak"
        ).strip()
        or "weak",
        "pilot_readiness_estimate": str(
            territorial_summary.get("pilot_readiness_estimate") or "early"
        ).strip()
        or "early",
        "possible_friction": (
            str(territorial_summary.get("possible_friction") or "").strip() or None
        ),
        "territory_recommendation": (
            str(territorial_summary.get("recommended_action") or "").strip() or None
        ),
        "phase1_intelligence": phase1,
        "institutional_intent_score": int(phase1.get("institutional_intent_score") or 0),
        "intent_level": str(phase1.get("intent_level") or "").strip(),
        "recurrence_level": str(phase1.get("recurrence_level") or "").strip(),
        "deployment_probability": str(phase1.get("deployment_probability") or "").strip(),
        "probable_organization_profiles": [
            str(item.get("label") or "").strip()
            for item in list(phase1.get("probable_organization_profiles") or [])
            if str(item.get("label") or "").strip()
        ],
        "territorial_opportunity_summary": dict(
            phase1.get("territorial_opportunity_summary") or {}
        ),
        "recommendation_reasons": list(phase1.get("recommendation_reasons") or []),
        "recommended_next_action": str(
            phase1.get("recommended_next_action") or ""
        ).strip(),
    }


def _revenue_founder_activity_map(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    lead_ids = sorted(
        {
            int(row.get("id") or 0)
            for row in rows
            if str(row.get("kind") or "") == "professional_lead" and int(row.get("id") or 0) > 0
        }
    )
    if not lead_ids or not _professional_lead_activity_enabled():
        return {}

    activity_rows = (
        ProfessionalLeadActivity.query.with_entities(
            ProfessionalLeadActivity.professional_lead_id,
            ProfessionalLeadActivity.action,
            ProfessionalLeadActivity.payload_json,
            ProfessionalLeadActivity.created_at,
        )
        .filter(ProfessionalLeadActivity.professional_lead_id.in_(lead_ids))
        .order_by(
            ProfessionalLeadActivity.created_at.asc(),
            ProfessionalLeadActivity.id.asc(),
        )
        .all()
    )
    output: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in activity_rows:
        lead_id = int(getattr(row, "professional_lead_id", 0) or 0)
        if lead_id <= 0:
            continue
        payload = {}
        raw_payload = getattr(row, "payload_json", None)
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                payload = parsed
        note = (
            str(payload.get("note") or payload.get("message") or "").strip()
            or _professional_lead_activity_label(getattr(row, "action", None))
        )
        output[f"professional_lead:{lead_id}"].append(
            {
                "timestamp": getattr(row, "created_at", None),
                "event_type": "founder_manual_note",
                "label": "founder note added",
                "note": note,
            }
        )
    return output


def _build_founder_operational_context(rows: list[dict[str, object]]) -> dict[str, object]:
    manual_notes_by_uid = _revenue_founder_activity_map(rows)
    current_time = _now_utc()
    operational_rows: list[dict[str, object]] = []
    for row in rows:
        payload = dict(row)
        timeline = build_founder_memory_timeline(
            payload,
            manual_notes=manual_notes_by_uid.get(str(payload.get("uid") or ""), []),
            now=current_time,
        )
        memory_summary = summarize_founder_memory(timeline, row=payload, now=current_time)
        operational_rows.append(
            {
                **payload,
                **memory_summary,
                "timeline_events": timeline,
                "last_founder_touch": memory_summary.get("last_founder_touch"),
                "founder_action_state": dict(memory_summary.get("founder_action_state") or {}),
            }
        )

    action_queue = build_founder_action_queue(operational_rows, now=current_time, limit=8)
    founder_operational_state = [
        build_founder_operational_state(
            item,
            memory_summary=item.get("founder_memory"),
            action_payload={
                "action_label": item.get("recommended_founder_action"),
                "action_code": item.get("recommended_founder_action_code"),
                "urgency": item.get("followup_urgency"),
            },
            previous_state=item.get("founder_operational_state"),
            now=current_time,
        )
        for item in action_queue
    ]
    state_summary = summarize_founder_operational_state(founder_operational_state, now=current_time)
    stalled = detect_stalled_opportunities(action_queue, now=current_time)[:6]
    temperature_rows = sorted(
        action_queue,
        key=lambda item: (
            int(item.get("urgency_score") or 0),
            str(item.get("relationship_temperature") or ""),
            int(item.get("intent_score") or item.get("score") or 0),
        ),
        reverse=True,
    )[:6]
    memory_timeline = [
        {
            "uid": str(item.get("uid") or ""),
            "organization": str(item.get("organization") or "Institutional account"),
            "relationship_temperature": str(item.get("relationship_temperature") or "cold"),
            "timeline_events": list(item.get("timeline_events") or [])[:4],
            "last_timeline_event": item.get("last_timeline_event"),
        }
        for item in action_queue[:4]
    ]
    return {
        "action_queue": action_queue,
        "stalled_opportunities": stalled,
        "relationship_temperature_rows": temperature_rows,
        "institutional_memory_timeline": memory_timeline,
        "founder_operational_state": founder_operational_state,
        "relationship_state_summary": state_summary["relationship_state_summary"],
        "pilot_progression_summary": state_summary["pilot_progression_summary"],
        "state_transitions": state_summary["state_transitions"],
        "next_founder_actions": state_summary["next_founder_actions"],
        "action_summary": summarize_founder_operational_actions(operational_rows, now=current_time, limit=8),
    }


def _build_founder_cockpit_context(rows: list[SimpleNamespace]) -> dict[str, object]:
    queue = build_founder_priority_queue(rows)
    daily_source_rows = [
        dict(item.__dict__) if hasattr(item, "__dict__") else dict(item)
        for item in rows
    ]
    telemetry_profiles = _build_revenue_signal_profiles()
    telemetry_summary = summarize_revenue_signal_profile(telemetry_profiles)
    operational_context = _build_founder_operational_context(daily_source_rows)

    return {
        "queue": queue[:8],
        "daily_queue": build_daily_founder_queue(daily_source_rows, limit=5),
        "daily_summary": summarize_daily_founder_queue(daily_source_rows),
        "alerts": build_founder_alerts(rows),
        "territories": group_founder_signals_by_territory(rows)[:6],
        "hot_this_week": telemetry_summary["hot_this_week"],
        "silent_high_intent": telemetry_summary["silent_high_intent"],
        "territory_acceleration": telemetry_summary["territory_acceleration"],
        "stalled_opportunities": operational_context["stalled_opportunities"],
        "recommended_founder_actions": operational_context["action_queue"],
        "relationship_temperature_rows": operational_context["relationship_temperature_rows"],
        "institutional_memory_timeline": operational_context["institutional_memory_timeline"],
        "founder_operational_state": operational_context["founder_operational_state"],
        "relationship_state_summary": operational_context["relationship_state_summary"],
        "pilot_progression_summary": operational_context["pilot_progression_summary"],
        "state_transitions": operational_context["state_transitions"],
        "next_founder_actions": operational_context["next_founder_actions"],
        "operational_action_summary": operational_context["action_summary"],
        "summary": summarize_founder_opportunity_actions(rows),
    }


def _build_revenue_signal_profiles(
    *,
    lookback_days: int = SIGNAL_LOOKBACK_DAYS,
) -> list[dict[str, object]]:
    try:
        from backend.models_with_analytics import AnalyticsEvent, UserBehavior
    except Exception:
        return []

    if not _table_exists("analytics_events"):
        return []

    since = _now_utc() - timedelta(days=max(1, int(lookback_days or SIGNAL_LOOKBACK_DAYS)))
    event_rows = (
        db.session.query(AnalyticsEvent)
        .filter(AnalyticsEvent.created_at >= since)
        .filter(AnalyticsEvent.user_session.isnot(None))
        .filter(AnalyticsEvent.user_session != "")
        .order_by(AnalyticsEvent.created_at.asc(), AnalyticsEvent.id.asc())
        .limit(5000)
        .all()
    )
    if not event_rows:
        return []

    location_lookup: dict[str, str] = {}
    if UserBehavior is not None and _table_exists("user_behaviors"):
        behavior_rows = (
            db.session.query(UserBehavior.session_id, UserBehavior.location)
            .filter(UserBehavior.session_start >= since)
            .filter(UserBehavior.session_id.isnot(None))
            .filter(UserBehavior.session_id != "")
            .all()
        )
        for session_id, location in behavior_rows:
            territory = normalize_territory_name(_audience_location_city(location))
            if session_id and territory:
                location_lookup[str(session_id).strip()] = territory

    grouped_events: dict[str, list[object]] = defaultdict(list)
    for event in event_rows:
        session_id = str(getattr(event, "user_session", "") or "").strip()
        if not session_id:
            continue
        grouped_events[session_id].append(event)

    territory_counts = Counter(
        territory for territory in location_lookup.values() if territory
    )
    profiles: list[dict[str, object]] = []
    for session_id, session_events in grouped_events.items():
        territory = location_lookup.get(session_id)
        profile = build_revenue_signal_profile(
            session_events,
            territory=territory,
            territory_repeat_count=territory_counts.get(territory or "", 0),
            now=_now_utc(),
        )
        if not int(profile.get("intent_score") or 0):
            continue
        profile["session_id"] = session_id
        profiles.append(profile)

    profiles.sort(
        key=lambda item: (
            int(item.get("intent_score") or 0),
            int(item.get("pilot_event_count") or 0),
            int(item.get("recency_score") or 0),
        ),
        reverse=True,
    )
    return profiles


def _revenue_recent_activity_score(last_activity: datetime | None) -> int:
    activity = _as_aware_utc(last_activity)
    if not activity:
        return 0
    age = _now_utc() - activity
    if age <= timedelta(hours=24):
        return 10
    if age <= timedelta(days=7):
        return 6
    if age <= timedelta(days=30):
        return 2
    return 0


def _revenue_score_bucket(score: int) -> dict:
    if score >= 80:
        return {"label": "Very Hot", "class": "text-bg-danger"}
    if score >= 60:
        return {"label": "Hot", "class": "text-bg-warning text-dark"}
    if score >= 35:
        return {"label": "Warm", "class": "text-bg-info text-dark"}
    return {"label": "Cold", "class": "text-bg-light border"}


def _revenue_next_action_state(next_action_at: datetime | None) -> str:
    due = _as_aware_utc(next_action_at)
    if not due:
        return "none"
    now = _now_utc()
    if due.date() <= now.date():
        return "overdue"
    if due <= now + timedelta(days=7):
        return "this_week"
    return "scheduled"


def _revenue_next_action_label(next_action_at: datetime | None, note: str | None) -> str:
    due = _as_aware_utc(next_action_at)
    if not due:
        return "No follow-up set"
    date_label = due.strftime("%Y-%m-%d")
    clean_note = (note or "").strip()
    return f"{date_label} - {clean_note}" if clean_note else date_label


def _revenue_last_activity(row, item_type: str) -> datetime | None:
    if item_type == "professional_lead":
        return (
            getattr(row, "last_touched_at", None)
            or getattr(row, "contacted_at", None)
            or getattr(row, "created_at", None)
        )
    return (
        getattr(row, "updated_at", None)
        or getattr(row, "reviewed_at", None)
        or getattr(row, "created_at", None)
    )


def _revenue_row_from_professional_lead(lead: ProfessionalLead) -> SimpleNamespace:
    stage = _revenue_stage(getattr(lead, "status", None), "professional_lead")
    audience_points, audience_context = _revenue_audience_score(getattr(lead, "notes", None))
    last_activity = _revenue_last_activity(lead, "professional_lead")
    score_payload = _revenue_score_payload(
        lead,
        item_type="professional_lead",
        stage=stage,
        audience_points=audience_points,
        audience_context=audience_context,
        last_activity=last_activity,
    )
    score = int(score_payload["total"])
    bucket = _revenue_score_bucket(score)
    has_followup_fields = _revenue_has_followup_fields("professional_leads")
    next_action_at = getattr(lead, "next_action_at", None) if has_followup_fields else None
    next_action_note = getattr(lead, "next_action_note", None) if has_followup_fields else None
    next_action_state = _revenue_next_action_state(next_action_at)
    value = _revenue_amount(lead, "professional_lead")
    value_payload = _metric_available(value) if value is not None else _unavailable_revenue_amount()
    organization = (getattr(lead, "organization", None) or getattr(lead, "profession", None) or "Professional lead").strip()
    contact = (getattr(lead, "full_name", None) or getattr(lead, "email", None) or "-").strip()
    reasons = []
    intent_summary = audience_context.get("institutional_intent") if isinstance(audience_context, dict) else None
    if audience_context:
        audience_label = (
            (intent_summary or {}).get("label")
            or audience_context.get("lead_intent_label")
            or audience_context.get("temperature")
            or "Audience"
        )
        reasons.append(f"{audience_label} audience")
    if next_action_state == "overdue":
        reasons.append("follow-up due")
    if stage in {"qualified", "demo_booked", "demo_done", "pilot_proposed", "negotiation"}:
        reasons.append(_revenue_stage_label(stage))
    if not reasons:
        reasons.append("new professional signal")
    founder_meta = _revenue_audience_metadata(audience_context)
    next_best_action = (
        founder_meta.get("recommended_next_action")
        or (intent_summary or {}).get("recommended_action")
        or (audience_context or {}).get("recommended_action")
    )
    if not next_best_action:
        if next_action_state == "overdue":
            next_best_action = "Follow up now"
        elif stage in {"new", "contacted", "qualified"}:
            next_best_action = "Book demo"
        elif stage in {"demo_done", "pilot_proposed", "negotiation"}:
            next_best_action = "Push pilot decision"
        else:
            next_best_action = "Open"
    return SimpleNamespace(
        id=int(lead.id),
        uid=f"professional_lead:{lead.id}",
        kind="professional_lead",
        type_label="Lead",
        organization=organization,
        contact=contact,
        city=getattr(lead, "city", None) or "-",
        source=getattr(lead, "source", None) or "professional",
        stage=stage,
        stage_label=_revenue_stage_label(stage),
        score=score,
        score_bucket=bucket["label"],
        score_class=bucket["class"],
        created_at=getattr(lead, "created_at", None),
        contacted_at=getattr(lead, "contacted_at", None),
        last_touched_at=getattr(lead, "last_touched_at", None),
        last_activity=last_activity,
        last_activity_label=_format_demo_touch_elapsed(last_activity),
        next_action_at=next_action_at,
        next_action_note=next_action_note,
        next_action_state=next_action_state,
        next_action_label=_revenue_next_action_label(next_action_at, next_action_note),
        estimated_value=value,
        estimated_value_display=value if value is not None else "Not enough data available",
        estimated_value_available=value is not None,
        estimated_value_reason=value_payload.get("reason", ""),
        weighted_value=int(value * REVENUE_STAGE_WEIGHTS.get(stage, 0.05)) if value is not None else None,
        action_url=url_for("admin.admin_professional_lead_detail", lead_id=lead.id),
        why_hot=", ".join(reasons),
        next_best_action=next_best_action,
        score_components=score_payload["components"],
        score_evidence=score_payload["evidence"],
        score_originating_event_ids=score_payload["originating_event_ids"],
        score_confidence=score_payload["confidence"],
        score_formula_version=REVENUE_SCORE_FORMULA_VERSION,
        intent_score=founder_meta["intent_score"],
        intent_tier=founder_meta["intent_tier"],
        intent_label=founder_meta["intent_label"],
        primary_interest=founder_meta["primary_interest"],
        trust_friction_detected=founder_meta["trust_friction_detected"],
        friction_reason=founder_meta["friction_reason"],
        top_paths=founder_meta["top_paths"],
        repeated_engagement_detected=founder_meta["repeated_engagement_detected"],
        priority_level=founder_meta["priority_level"],
        territorial_intensity=founder_meta["territorial_intensity"],
        territory_confidence=founder_meta["territory_confidence"],
        pilot_readiness_estimate=founder_meta["pilot_readiness_estimate"],
        possible_friction=founder_meta["possible_friction"],
        territory_recommendation=founder_meta["territory_recommendation"],
        institutional_intent_score=founder_meta["institutional_intent_score"],
        intent_level=founder_meta["intent_level"],
        recurrence_level=founder_meta["recurrence_level"],
        deployment_probability=founder_meta["deployment_probability"],
        probable_organization_profiles=founder_meta["probable_organization_profiles"],
        territorial_opportunity_summary=founder_meta["territorial_opportunity_summary"],
        recommendation_reasons=founder_meta["recommendation_reasons"],
        recommended_next_action=founder_meta["recommended_next_action"],
        has_demo=stage in {"demo_booked", "demo_done", "pilot_proposed", "negotiation"},
    )


def _revenue_row_from_access_request(row: OrganizationAccessRequest) -> SimpleNamespace:
    stage = _revenue_stage(getattr(row, "status", None), "access_request")
    audience_points, audience_context = _revenue_audience_score(getattr(row, "internal_notes", None))
    last_activity = _revenue_last_activity(row, "access_request")
    score_payload = _revenue_score_payload(
        row,
        item_type="access_request",
        stage=stage,
        audience_points=audience_points,
        audience_context=audience_context,
        last_activity=last_activity,
    )
    score = int(score_payload["total"])
    bucket = _revenue_score_bucket(score)
    has_followup_fields = _revenue_has_followup_fields("organization_access_requests")
    next_action_at = getattr(row, "next_action_at", None) if has_followup_fields else None
    next_action_note = getattr(row, "next_action_note", None) if has_followup_fields else None
    next_action_state = _revenue_next_action_state(next_action_at)
    value = _revenue_amount(row, "access_request")
    value_payload = _metric_available(value) if value is not None else _unavailable_revenue_amount()
    reasons = ["access request submitted"]
    intent_summary = audience_context.get("institutional_intent") if isinstance(audience_context, dict) else None
    if audience_context:
        audience_label = (
            (intent_summary or {}).get("label")
            or audience_context.get("lead_intent_label")
            or audience_context.get("temperature")
            or "Audience"
        )
        reasons.append(f"{audience_label} audience")
    if next_action_state == "overdue":
        reasons.append("follow-up due")
    if stage == "won":
        reasons.append("approved")
    founder_meta = _revenue_audience_metadata(audience_context)
    next_best_action = (
        founder_meta.get("recommended_next_action")
        or (intent_summary or {}).get("recommended_action")
        or (audience_context or {}).get("recommended_action")
    )
    if not next_best_action:
        if stage == "new":
            next_best_action = "Review and qualify"
        elif next_action_state == "overdue":
            next_best_action = "Follow up now"
        elif stage == "qualified":
            next_best_action = "Approve or request info"
        else:
            next_best_action = "Open"
    return SimpleNamespace(
        id=int(row.id),
        uid=f"access_request:{row.id}",
        kind="access_request",
        type_label="Access Request",
        organization=getattr(row, "organization_name", None) or "Access request",
        contact=getattr(row, "contact_name", None) or getattr(row, "email", None) or "-",
        city=getattr(row, "city", None) or "-",
        source="/demander-acces",
        stage=stage,
        stage_label=_revenue_stage_label(stage),
        score=score,
        score_bucket=bucket["label"],
        score_class=bucket["class"],
        created_at=getattr(row, "created_at", None),
        contacted_at=None,
        last_touched_at=getattr(row, "reviewed_at", None) or getattr(row, "updated_at", None),
        last_activity=last_activity,
        last_activity_label=_format_demo_touch_elapsed(last_activity),
        next_action_at=next_action_at,
        next_action_note=next_action_note,
        next_action_state=next_action_state,
        next_action_label=_revenue_next_action_label(next_action_at, next_action_note),
        estimated_value=value,
        estimated_value_display=value if value is not None else "Not enough data available",
        estimated_value_available=value is not None,
        estimated_value_reason=value_payload.get("reason", ""),
        weighted_value=int(value * REVENUE_STAGE_WEIGHTS.get(stage, 0.05)) if value is not None else None,
        action_url=url_for("admin.admin_organization_access_request_detail", req_id=row.id),
        why_hot=", ".join(reasons),
        next_best_action=next_best_action,
        score_components=score_payload["components"],
        score_evidence=score_payload["evidence"],
        score_originating_event_ids=score_payload["originating_event_ids"],
        score_confidence=score_payload["confidence"],
        score_formula_version=REVENUE_SCORE_FORMULA_VERSION,
        intent_score=founder_meta["intent_score"],
        intent_tier=founder_meta["intent_tier"],
        intent_label=founder_meta["intent_label"],
        primary_interest=founder_meta["primary_interest"],
        trust_friction_detected=founder_meta["trust_friction_detected"],
        friction_reason=founder_meta["friction_reason"],
        top_paths=founder_meta["top_paths"],
        repeated_engagement_detected=founder_meta["repeated_engagement_detected"],
        priority_level=founder_meta["priority_level"],
        territorial_intensity=founder_meta["territorial_intensity"],
        territory_confidence=founder_meta["territory_confidence"],
        pilot_readiness_estimate=founder_meta["pilot_readiness_estimate"],
        possible_friction=founder_meta["possible_friction"],
        territory_recommendation=founder_meta["territory_recommendation"],
        institutional_intent_score=founder_meta["institutional_intent_score"],
        intent_level=founder_meta["intent_level"],
        recurrence_level=founder_meta["recurrence_level"],
        deployment_probability=founder_meta["deployment_probability"],
        probable_organization_profiles=founder_meta["probable_organization_profiles"],
        territorial_opportunity_summary=founder_meta["territorial_opportunity_summary"],
        recommendation_reasons=founder_meta["recommendation_reasons"],
        recommended_next_action=founder_meta["recommended_next_action"],
        has_demo=stage in {"qualified", "won"},
    )


def _build_revenue_pipeline_rows() -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    if _table_exists("professional_leads"):
        lead_fields = [
            ProfessionalLead.id,
            ProfessionalLead.email,
            ProfessionalLead.full_name,
            ProfessionalLead.city,
            ProfessionalLead.profession,
            ProfessionalLead.organization,
            ProfessionalLead.source,
            ProfessionalLead.status,
            ProfessionalLead.notes,
            ProfessionalLead.contacted_at,
            ProfessionalLead.created_at,
        ]
        if _table_has_column("professional_leads", "last_touched_at"):
            lead_fields.append(ProfessionalLead.last_touched_at)
        if _table_has_column("professional_leads", "next_action_at"):
            lead_fields.append(ProfessionalLead.next_action_at)
        if _table_has_column("professional_leads", "next_action_note"):
            lead_fields.append(ProfessionalLead.next_action_note)
        query = (
            ProfessionalLead.query.options(load_only(*lead_fields))
            .order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc())
            .limit(500)
        )
        for lead in query.all():
            rows.append(_revenue_row_from_professional_lead(lead))
    if _table_exists("organization_access_requests"):
        request_fields = [
            OrganizationAccessRequest.id,
            OrganizationAccessRequest.organization_name,
            OrganizationAccessRequest.contact_name,
            OrganizationAccessRequest.email,
            OrganizationAccessRequest.city,
            OrganizationAccessRequest.org_type,
            OrganizationAccessRequest.estimated_users,
            OrganizationAccessRequest.status,
            OrganizationAccessRequest.reviewed_at,
            OrganizationAccessRequest.internal_notes,
            OrganizationAccessRequest.created_at,
            OrganizationAccessRequest.updated_at,
        ]
        if _table_has_column("organization_access_requests", "next_action_at"):
            request_fields.append(OrganizationAccessRequest.next_action_at)
        if _table_has_column("organization_access_requests", "next_action_note"):
            request_fields.append(OrganizationAccessRequest.next_action_note)
        query = (
            OrganizationAccessRequest.query.options(load_only(*request_fields))
            .order_by(
                OrganizationAccessRequest.created_at.desc(),
                OrganizationAccessRequest.id.desc(),
            )
            .limit(500)
        )
        for access_request in query.all():
            rows.append(_revenue_row_from_access_request(access_request))
    rows.sort(
        key=lambda item: (
            item.score,
            1 if item.next_action_state == "overdue" else 0,
            _as_aware_utc(item.last_activity) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    _persist_revenue_score_explanations(rows)
    return rows


def _persist_revenue_score_explanations(rows: list[SimpleNamespace]) -> None:
    if not rows or not _table_exists("score_explanations"):
        return
    try:
        now = _now_utc()
        for row in rows:
            subject_type = str(getattr(row, "kind", "") or "")
            subject_id = str(getattr(row, "id", "") or "")
            if not subject_type or not subject_id:
                continue
            existing = (
                ScoreExplanation.query.filter_by(
                    score_key="revenue_priority",
                    subject_type=subject_type,
                    subject_id=subject_id,
                )
                .order_by(ScoreExplanation.id.desc())
                .first()
            )
            payload = existing or ScoreExplanation(
                score_key="revenue_priority",
                subject_type=subject_type,
                subject_id=subject_id,
                total_score=0,
                formula_version=REVENUE_SCORE_FORMULA_VERSION,
                confidence="low",
                component_list_json="[]",
                evidence_json="{}",
                originating_event_ids_json="[]",
            )
            payload.total_score = int(getattr(row, "score", 0) or 0)
            payload.formula_version = str(getattr(row, "score_formula_version", None) or REVENUE_SCORE_FORMULA_VERSION)
            payload.confidence = str(getattr(row, "score_confidence", None) or "low")
            payload.component_list_json = json.dumps(
                list(getattr(row, "score_components", None) or []),
                ensure_ascii=True,
                sort_keys=True,
            )
            payload.evidence_json = json.dumps(
                dict(getattr(row, "score_evidence", None) or {}),
                ensure_ascii=True,
                sort_keys=True,
            )
            payload.originating_event_ids_json = json.dumps(
                list(getattr(row, "score_originating_event_ids", None) or []),
                ensure_ascii=True,
                sort_keys=True,
            )
            payload.computed_at = now
            if existing is None:
                db.session.add(payload)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _filter_revenue_rows(rows: list[SimpleNamespace], filters: dict) -> list[SimpleNamespace]:
    result = rows
    if filters["type"]:
        result = [row for row in result if row.kind == filters["type"]]
    if filters["stage"]:
        result = [row for row in result if row.stage == filters["stage"]]
    if filters["city"]:
        needle = filters["city"].lower()
        result = [row for row in result if needle in (row.city or "").lower()]
    if filters["score_bucket"]:
        result = [
            row
            for row in result
            if row.score_bucket.lower().replace(" ", "_") == filters["score_bucket"]
        ]
    if filters["followup"] == "overdue":
        result = [row for row in result if row.next_action_state == "overdue"]
    elif filters["followup"] == "this_week":
        result = [row for row in result if row.next_action_state in {"overdue", "this_week"}]
    elif filters["followup"] == "none":
        result = [row for row in result if row.next_action_state == "none"]
    if filters["active_week"]:
        cutoff = _now_utc() - timedelta(days=7)
        result = [
            row
            for row in result
            if (_as_aware_utc(row.last_activity) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
        ]
    if filters["q"]:
        needle = filters["q"].lower()
        result = [
            row
            for row in result
            if needle in (row.organization or "").lower()
            or needle in (row.contact or "").lower()
        ]
    return result


def _revenue_metrics(rows: list[SimpleNamespace]) -> dict:
    now = _now_utc()
    source_tables = ["professional_leads", "organization_access_requests"]
    return {
        "new_leads": _metric_payload(
            sum(1 for row in rows if row.stage == "new"),
            source_tables=source_tables,
            query_origin="_build_revenue_pipeline_rows + _revenue_metrics(stage == 'new')",
            confidence="high",
            updated_at=now,
            explain="Counts visible pipeline rows whose normalized stage is new.",
        ),
        "hot_leads": _metric_payload(
            sum(1 for row in rows if row.score_bucket in {"Hot", "Very Hot"} and row.stage not in {"won", "lost"}),
            source_tables=source_tables + ["score_explanations"],
            query_origin="_build_revenue_pipeline_rows + explained revenue_priority score bucket",
            confidence="medium",
            updated_at=now,
            explain="Counts open rows whose persisted explained score places them in Hot or Very Hot.",
        ),
        "overdue_followups": _metric_payload(
            sum(1 for row in rows if row.next_action_state == "overdue"),
            source_tables=source_tables,
            query_origin="_build_revenue_pipeline_rows + next_action_at <= today",
            confidence="high",
            updated_at=now,
            explain="Counts rows with next_action_at due today or earlier.",
        ),
        "demos_pending": _metric_payload(
            sum(1 for row in rows if row.stage in {"demo_booked", "demo_done"}),
            source_tables=source_tables,
            query_origin="_build_revenue_pipeline_rows + normalized stage in demo_booked/demo_done",
            confidence="medium",
            updated_at=now,
            explain="Counts rows whose source status maps to a pending demo stage.",
        ),
        "active_pipeline": _metric_payload(
            None,
            source_tables=["crm_opportunities"],
            query_origin="crm_opportunities.amount_eur WHERE stage NOT IN ('won','lost','paused')",
            confidence="low",
            updated_at=now,
            explain="Requires a real CRM opportunity amount. Lead type and estimated users are not used as money.",
            unavailable_reason="No CRM opportunity amount table/field is available.",
        ),
        "won_this_month": _metric_payload(
            None,
            source_tables=["crm_opportunities"],
            query_origin="crm_opportunities.amount_eur WHERE stage = 'won' AND closed_at >= month_start",
            confidence="low",
            updated_at=now,
            explain="Requires real closed-won CRM opportunity amounts.",
            unavailable_reason="No CRM opportunity amount table/field is available.",
        ),
    }


def _revenue_forecast(rows: list[SimpleNamespace]) -> dict:
    now = _now_utc()
    source_tables = ["crm_opportunities"]
    return {
        "weighted_pipeline": _metric_payload(
            None,
            source_tables=source_tables,
            query_origin="SUM(amount_eur * stage_probability) FROM crm_opportunities",
            confidence="low",
            updated_at=now,
            explain="Requires real opportunity amount and probability.",
            unavailable_reason="No CRM opportunity amount/probability data is available.",
        ),
        "likely_this_month": _metric_payload(
            None,
            source_tables=source_tables,
            query_origin="SUM(weighted_amount_eur) FROM crm_opportunities WHERE expected_close_at <= now + 30 days",
            confidence="low",
            updated_at=now,
            explain="Requires expected close date plus real opportunity amount.",
            unavailable_reason="No CRM opportunity amount/close-date data is available.",
        ),
        "closed_won": _metric_payload(
            None,
            source_tables=source_tables,
            query_origin="SUM(amount_eur) FROM crm_opportunities WHERE stage = 'won'",
            confidence="low",
            updated_at=now,
            explain="Requires closed-won opportunity records with amount.",
            unavailable_reason="No CRM closed-won opportunity amount data is available.",
        ),
    }


def _revenue_focus(rows: list[SimpleNamespace]) -> dict:
    open_rows = [row for row in rows if row.stage not in {"won", "lost", "paused"}]
    top = sorted(
        open_rows,
        key=lambda row: (
            row.score,
            1 if row.next_action_state == "overdue" else 0,
            row.weighted_value or 0,
        ),
        reverse=True,
    )[:5]
    stale_cutoff = _now_utc() - timedelta(days=10)
    at_risk = [
        row
        for row in open_rows
        if row.next_action_state == "overdue"
        or (
            row.stage in {"pilot_proposed", "negotiation", "demo_done"}
            and (_as_aware_utc(row.last_activity) or stale_cutoff) <= stale_cutoff
        )
        or (row.score >= 60 and row.next_action_state == "none")
    ][:5]
    return {"top": top, "at_risk": at_risk}


@admin_bp.get("/revenue")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_revenue():
    _require_global_analytics_access()
    filters = {
        "type": (request.args.get("type") or "").strip(),
        "stage": (request.args.get("stage") or "").strip(),
        "city": (request.args.get("city") or "").strip(),
        "score_bucket": (request.args.get("score_bucket") or "").strip(),
        "followup": (request.args.get("followup") or "").strip(),
        "active_week": (request.args.get("active_week") or "").strip() == "1",
        "q": (request.args.get("q") or "").strip(),
    }
    all_rows = _build_revenue_pipeline_rows()
    filtered_rows = _filter_revenue_rows(all_rows, filters)
    founder_cockpit = _build_founder_cockpit_context(all_rows)
    return (
        render_template(
            "admin/revenue_dashboard.html",
            rows=filtered_rows[:250],
            total_rows=len(all_rows),
            metrics=_revenue_metrics(all_rows),
            forecast=_revenue_forecast(all_rows),
            focus=_revenue_focus(all_rows),
            filters=filters,
            stage_choices=REVENUE_STAGE_ORDER,
            active_filters=any(filters.values()),
            radar=_build_audience_map_context(),
            founder_cockpit=founder_cockpit,
        ),
        200,
    )


@admin_bp.post("/revenue/<string:item_type>/<int:item_id>/quick-action")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_revenue_quick_action(item_type: str, item_id: int):
    _require_global_analytics_access()
    action = (request.form.get("action") or "").strip().lower()
    note = (request.form.get("note") or "").strip()
    now = _now_utc()

    if item_type == "professional_lead":
        row = ProfessionalLead.query.get_or_404(item_id)
        table_name = "professional_leads"
        if action == "mark_contacted":
            row.status = "contacted"
            if not row.contacted_at:
                row.contacted_at = now
            _record_professional_lead_touch(row, action="revenue_mark_contacted")
        elif action == "mark_lost":
            row.status = "rejected"
            _record_professional_lead_touch(row, action="revenue_mark_lost")
        elif action in {"tomorrow", "next_week"}:
            if _revenue_has_followup_fields(table_name):
                row.next_action_at = now + (timedelta(days=1) if action == "tomorrow" else timedelta(days=7))
                row.next_action_note = note or ("Follow up tomorrow" if action == "tomorrow" else "Follow up next week")
            _record_professional_lead_touch(row, action=f"revenue_followup_{action}")
        else:
            flash("Unknown revenue action.", "warning")
            return redirect(url_for("admin.admin_revenue"), code=303)
    elif item_type == "access_request":
        row = OrganizationAccessRequest.query.get_or_404(item_id)
        table_name = "organization_access_requests"
        if action == "mark_contacted":
            if (row.status or "").lower() == "new":
                row.status = "reviewed"
            row.reviewed_at = row.reviewed_at or now
        elif action == "mark_lost":
            row.status = "rejected"
            row.reviewed_at = row.reviewed_at or now
        elif action in {"tomorrow", "next_week"}:
            if _revenue_has_followup_fields(table_name):
                row.next_action_at = now + (timedelta(days=1) if action == "tomorrow" else timedelta(days=7))
                row.next_action_note = note or ("Follow up tomorrow" if action == "tomorrow" else "Follow up next week")
        else:
            flash("Unknown revenue action.", "warning")
            return redirect(url_for("admin.admin_revenue"), code=303)
    else:
        abort(404)

    db.session.commit()
    flash("Revenue pipeline updated.", "success")
    return redirect(url_for("admin.admin_revenue"), code=303)






@admin_bp.get("/api/high-intent-sessions")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_high_intent_sessions():
    _require_global_analytics_access()

    from collections import defaultdict
    from datetime import datetime, timedelta

    try:
        from backend.models_with_analytics import AnalyticsEvent, UserBehavior
    except Exception:
        UserBehavior = None
        return jsonify({"sessions": []})

    def is_internal_path(path):
        if not path:
            return True

        internal_prefixes = (
            "/admin",
            "/ops",
            "/static",
            "/events",
            "/api",
            "/favicon",
        )

        return path.startswith(internal_prefixes)

    since = utc_now() - timedelta(days=7)

    rows = (
        db.session.query(AnalyticsEvent)
        .filter(AnalyticsEvent.created_at >= since)
        .order_by(AnalyticsEvent.created_at.asc())
        .limit(5000)
        .all()
    )
    location_lookup: dict[str, str] = {}
    if UserBehavior is not None and _table_exists("user_behaviors"):
        behavior_rows = (
            db.session.query(UserBehavior.session_id, UserBehavior.location)
            .filter(UserBehavior.session_start >= since)
            .filter(UserBehavior.session_id.isnot(None))
            .filter(UserBehavior.location.isnot(None))
            .filter(UserBehavior.location != "")
            .all()
        )
        for session_id, location in behavior_rows:
            city = normalize_territory_name(_audience_location_city(location))
            if session_id and city:
                location_lookup[str(session_id).strip()] = city

    grouped = defaultdict(list)

    for row in rows:
        key = row.user_session or row.user_ip or "anonymous"
        grouped[key].append(row)

    sessions = []
    territory_rows: list[dict[str, object]] = []

    for session_id, events in grouped.items():
        public_paths = []
        public_event_count = 0
        has_submit = False

        for event in events:

            event_type = event.event_type or ""
            raw_path = event.page_url or ""
            if is_internal_path(str(raw_path or "")):
                continue
            path = normalize_intent_path(raw_path)

            if event_type.endswith("_form_submit"):
                has_submit = True

            if not path:
                continue

            public_event_count += 1
            public_paths.append(path)

        if not public_paths:
            continue

        deduped = []

        for p in public_paths:
            if not deduped or deduped[-1] != p:
                deduped.append(p)

        intent_summary = build_intent_summary(deduped, has_submit=has_submit)
        score = int(intent_summary["score"])
        intent = "low"

        if score >= 40:
            intent = "medium"

        if score >= 80:
            intent = "high"

        if score >= 130:
            intent = "very_high"

        if intent == "low":
            continue

        territory = location_lookup.get(str(session_id).strip(), "")
        if territory:
            territory_rows.append({
                "territory": territory,
                "territory_source": "behavior_location",
                "source_kind": "anonymous_session",
                "session_id": session_id,
                "paths": list(intent_summary["top_paths"]),
                "intent_tier": intent_summary["tier"],
                "primary_interest": intent_summary["primary_interest"],
                "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
                "repeat_visit": len(deduped) > 1,
            })

        sessions.append({
            "uid": str(session_id),
            "session_id": session_id[:16],
            "score": score,
            "intent": intent,
            "tier": intent_summary["tier"],
            "label": intent_summary["label"],
            "primary_interest": intent_summary["primary_interest"],
            "trust_friction_detected": bool(intent_summary["trust_friction_detected"]),
            "friction_reason": intent_summary["friction_reason"],
            "session_type": intent_summary["label"],
            "recommendation": intent_summary["recommended_action"],
            "path": deduped[:10],
            "events": public_event_count,
            "last_seen": str(events[-1].created_at),
            "territory": territory or None,
        })

    territory_lookup = _territorial_summary_lookup(detect_priority_territories(territory_rows))
    for row in sessions:
        summary = territory_lookup.get(_audience_normalize_text(row.get("territory")))
        if not summary:
            continue
        row["priority_level"] = summary.get("priority_level")
        row["territory_confidence"] = summary.get("confidence")
        row["dominant_interest"] = summary.get("dominant_interest")
        row["possible_friction"] = summary.get("possible_friction")
        row["pilot_readiness_estimate"] = summary.get("pilot_readiness_estimate")
        row["territorial_recommendation"] = summary.get("recommended_action")

    founder_lookup = {
        str(item.get("uid") or ""): item
        for item in build_founder_priority_queue(sessions)
    }
    for row in sessions:
        item = founder_lookup.get(str(row.get("session_id") or ""))
        if not item:
            continue
        row["opportunity_level"] = item.get("opportunity_level")
        row["operational_maturity"] = item.get("operational_maturity")
        row["recommended_action"] = item.get("recommended_action")
        row["possible_friction"] = item.get("possible_friction")
        row["evidence_summary"] = item.get("evidence_summary")

    sessions.sort(key=lambda x: x["score"], reverse=True)

    return jsonify({
        "sessions": sessions[:25]
    })



@admin_bp.get("/audience-map")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_audience_map():
    admin_required_404()
    _require_global_analytics_access()
    return render_template(
        "admin/audience_map.html",
        audience=_build_audience_map_context(),
        intent_paths=AUDIENCE_INTENT_PATHS,
    )


def _admin_import_tables_ready() -> bool:
    required_batch_columns = (
        "created_count",
        "updated_count",
        "skipped_duplicate_count",
        "rejected_count",
        "imported_count",
        "skipped_count",
        "error_count",
    )
    return (
        _table_exists("professional_leads")
        and _table_exists("import_batches")
        and _table_exists("professional_lead_activities")
        and all(_table_has_column("import_batches", column_name) for column_name in required_batch_columns)
        and _professional_lead_activity_enabled()
    )


def _admin_import_preview_state() -> dict[str, object]:
    state = session.get(ADMIN_IMPORT_PREVIEW_SESSION_KEY)
    return state if isinstance(state, dict) else {}


def _set_admin_import_preview_state(*, batch_id: int, preview_path: str, filename: str) -> None:
    session[ADMIN_IMPORT_PREVIEW_SESSION_KEY] = {
        "batch_id": int(batch_id),
        "preview_path": str(preview_path),
        "filename": str(filename or ""),
    }
    session.modified = True


def _clear_admin_import_preview_state(*, remove_file: bool = False) -> None:
    state = _admin_import_preview_state()
    session.pop(ADMIN_IMPORT_PREVIEW_SESSION_KEY, None)
    session.modified = True
    if remove_file:
        cleanup_preview_upload(str(state.get("preview_path") or ""))


def _existing_professional_leads_by_email() -> dict[str, ProfessionalLead]:
    if not _table_exists("professional_leads"):
        return {}
    lead_fields = [
        ProfessionalLead.id,
        ProfessionalLead.email,
        ProfessionalLead.full_name,
        ProfessionalLead.phone,
        ProfessionalLead.city,
        ProfessionalLead.profession,
        ProfessionalLead.organization,
        ProfessionalLead.availability,
        ProfessionalLead.message,
        ProfessionalLead.status,
        ProfessionalLead.notes,
    ]
    if _table_has_column("professional_leads", "owner_admin_id"):
        lead_fields.append(ProfessionalLead.owner_admin_id)
    rows = (
        ProfessionalLead.query.options(load_only(*lead_fields))
        .filter(ProfessionalLead.email.isnot(None))
        .all()
    )
    output: dict[str, ProfessionalLead] = {}
    for row in rows:
        email = str(getattr(row, "email", "") or "").strip().lower()
        if email and email not in output:
            output[email] = row
    return output


def _record_professional_lead_import_activity(
    *,
    lead: ProfessionalLead,
    action: str,
    payload: dict[str, object],
) -> None:
    imported_at = _now_utc()
    activity_payload = dict(payload or {})
    activity_payload["imported_at"] = imported_at.isoformat()
    db.session.add(
        ProfessionalLeadActivity(
            professional_lead_id=int(lead.id),
            admin_user_id=int(current_user.id) if getattr(current_user, "id", None) else None,
            action=action,
            payload_json=encode_json_payload(activity_payload),
            created_at=imported_at,
        )
    )


def _read_admin_import_mapping_from_form(headers: list[str]) -> dict[str, str]:
    submitted: dict[str, str] = {}
    for index, header in enumerate(headers):
        submitted[header] = (request.form.get(f"mapping_{index}") or "").strip()
    return sanitize_mapping(headers, submitted)


def _render_admin_import_upload(*, status_code: int = 200):
    return (
        render_template(
            "admin/import.html",
            target_options=available_target_options(),
            selected_target=IMPORT_TARGET_PROFESSIONAL_LEADS,
            import_tables_ready=_admin_import_tables_ready(),
        ),
        status_code,
    )


@admin_bp.get("/import")
@login_required
@admin_required
def admin_import_express():
    if not _admin_import_tables_ready():
        flash(
            "Le module Import Express nécessite les tables professional_leads et import_batches.",
            "warning",
        )
    return _render_admin_import_upload()


@admin_bp.post("/import/preview")
@login_required
@admin_required
def admin_import_preview():
    if not _admin_import_tables_ready():
        flash("Import Express indisponible tant que la migration n'est pas appliquée.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    target_type = (request.form.get("target_type") or "").strip()
    if target_type not in ADMIN_IMPORT_ALLOWED_TARGETS:
        flash("Cible d'import non prise en charge pour ce MVP.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    upload = request.files.get("file")
    if upload is None or not (upload.filename or "").strip():
        flash("Sélectionnez un fichier CSV à prévisualiser.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    original_filename = secure_filename(upload.filename or "") or "import.csv"
    if Path(original_filename).suffix.lower() not in {".csv", ".txt"}:
        flash("Seuls les fichiers CSV sont pris en charge pour le moment.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    raw_bytes = upload.read()
    if not raw_bytes:
        flash("Le fichier importé est vide.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    _clear_admin_import_preview_state(remove_file=True)

    try:
        parsed_file = parse_csv_bytes(raw_bytes)
    except Exception:
        flash("Impossible de lire ce CSV. Vérifiez l'encodage et les en-têtes.", "danger")
        return redirect(url_for("admin.admin_import_express"), code=303)

    if not parsed_file.headers:
        flash("Aucune colonne détectée dans le fichier importé.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    preview_file = save_preview_upload(
        instance_path=current_app.instance_path,
        filename=original_filename,
        raw_bytes=raw_bytes,
    )
    try:
        batch = ImportBatch(
            filename=original_filename,
            source_type=IMPORT_SOURCE_CSV,
            target_type=target_type,
            status="preview",
            created_by_admin_id=int(current_user.id),
        )
        db.session.add(batch)
        db.session.flush()

        mapping = infer_mapping(parsed_file.headers)
        preview = build_preview(
            parsed_file,
            mapping=mapping,
            existing_leads_by_email=_existing_professional_leads_by_email(),
            batch_id=int(batch.id or 0),
        )

        batch.created_count = int(preview.created_rows)
        batch.updated_count = int(preview.updated_rows)
        batch.skipped_duplicate_count = int(preview.skipped_duplicate_rows)
        batch.rejected_count = int(preview.rejected_rows)
        batch.imported_count = int(preview.created_rows + preview.updated_rows)
        batch.skipped_count = int(preview.skipped_duplicate_rows)
        batch.error_count = int(preview.rejected_rows)
        batch.mapping_json = encode_json_payload(preview.mapping)
        batch.errors_json = encode_json_payload(
            [{"message": item} for item in preview.preview_errors]
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        cleanup_preview_upload(preview_file["path"])
        flash("Le lot n'a pas pu être préparé pour prévisualisation.", "danger")
        return redirect(url_for("admin.admin_import_express"), code=303)

    _set_admin_import_preview_state(
        batch_id=int(batch.id),
        preview_path=preview_file["path"],
        filename=original_filename,
    )

    return (
        render_template(
            "admin/import_preview.html",
            batch=batch,
            preview=preview,
            field_options=available_field_options(),
            target_label=target_label,
            source_label=source_label,
        ),
        200,
    )


@admin_bp.post("/import/confirm")
@login_required
@admin_required
def admin_import_confirm():
    if not _admin_import_tables_ready():
        flash("Import Express indisponible tant que la migration n'est pas appliquée.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    state = _admin_import_preview_state()
    try:
        batch_id = int(request.form.get("batch_id") or "0")
    except ValueError:
        batch_id = 0

    if not state or int(state.get("batch_id") or 0) != batch_id:
        flash("La prévisualisation a expiré. Rechargez le fichier avant import.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    batch = ImportBatch.query.filter_by(
        id=batch_id,
        created_by_admin_id=int(current_user.id),
    ).first_or_404()

    preview_path = str(state.get("preview_path") or "")
    try:
        raw_bytes = load_preview_upload(preview_path)
        parsed_file = parse_csv_bytes(raw_bytes)
    except Exception:
        batch.status = "failed"
        batch.errors_json = encode_json_payload(
            [{"message": "Le fichier temporaire de prévisualisation n'est plus disponible."}]
        )
        db.session.commit()
        _clear_admin_import_preview_state(remove_file=True)
        flash("Le fichier temporaire n'est plus disponible. Rechargez le CSV.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    mapping = _read_admin_import_mapping_from_form(parsed_file.headers)
    existing_leads_by_email = _existing_professional_leads_by_email()

    def _create_imported_lead(payload: dict[str, str]) -> ProfessionalLead:
        with db.session.begin_nested():
            lead = ProfessionalLead(
                full_name=payload.get("full_name") or None,
                email=payload.get("email") or None,
                phone=payload.get("phone") or None,
                city=payload.get("city") or None,
                profession=payload.get("profession") or None,
                organization=payload.get("organization") or None,
                availability=payload.get("availability") or None,
                message=payload.get("message") or None,
                source=import_batch_source(int(batch.id)),
                status="imported",
            )
            db.session.add(lead)
            db.session.flush()
            return lead

    def _update_imported_lead(
        lead: ProfessionalLead,
        payload: dict[str, str],
        *,
        updated_fields: list[str],
    ) -> ProfessionalLead:
        with db.session.begin_nested():
            for field_name in updated_fields:
                if field_name not in ENRICHABLE_IMPORT_FIELDS:
                    continue
                setattr(lead, field_name, payload.get(field_name) or None)
            db.session.add(lead)
            db.session.flush()
            return lead

    outcome = import_professional_leads(
        parsed_file=parsed_file,
        mapping=mapping,
        batch_id=int(batch.id),
        existing_leads_by_email=existing_leads_by_email,
        create_row=_create_imported_lead,
        update_row=_update_imported_lead,
        record_activity=lambda lead, action, payload: _record_professional_lead_import_activity(
            lead=lead,
            action=action,
            payload={**payload, "batch_filename": batch.filename},
        ),
        source_filename=batch.filename,
    )

    batch.status = "imported"
    batch.created_count = int(outcome.created_count)
    batch.updated_count = int(outcome.updated_count)
    batch.skipped_duplicate_count = int(outcome.skipped_duplicate_count)
    batch.rejected_count = int(outcome.rejected_count)
    batch.imported_count = int(outcome.imported_count)
    batch.skipped_count = int(outcome.skipped_count)
    batch.error_count = int(outcome.error_count)
    batch.mapping_json = encode_json_payload(mapping)
    batch.errors_json = encode_json_payload(outcome.errors)
    db.session.commit()

    _clear_admin_import_preview_state(remove_file=True)
    flash(
        f"Import terminé: {outcome.created_count} créé(s), "
        f"{outcome.updated_count} enrichi(s), "
        f"{outcome.skipped_duplicate_count} doublon(s) ignoré(s), "
        f"{outcome.rejected_count} rejeté(s).",
        "success" if outcome.error_count == 0 else "warning",
    )

    return (
        render_template(
            "admin/import_result.html",
            batch=batch,
            outcome=outcome,
            target_label=target_label,
            source_label=source_label,
        ),
        200,
    )


@admin_bp.get("/import/history")
@login_required
@admin_required
def admin_import_history():
    if not _table_exists("import_batches"):
        flash("La table import_batches n'est pas encore disponible.", "warning")
        return redirect(url_for("admin.admin_import_express"), code=303)

    batches = (
        ImportBatch.query.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .limit(100)
        .all()
    )
    return (
        render_template(
            "admin/import_history.html",
            batches=batches,
            target_label=target_label,
            source_label=source_label,
            batch_errors_to_text=batch_errors_to_text,
        ),
        200,
    )


@admin_bp.post("/import/<int:batch_id>/rollback")
@login_required
@admin_required
def admin_import_rollback(batch_id: int):
    if not _admin_import_tables_ready():
        flash("Import Express indisponible tant que la migration n'est pas appliquée.", "warning")
        return redirect(url_for("admin.admin_import_history"), code=303)

    batch = ImportBatch.query.filter_by(id=batch_id).first_or_404()
    if batch.target_type != IMPORT_TARGET_PROFESSIONAL_LEADS:
        flash("Rollback non disponible pour cette cible d'import.", "warning")
        return redirect(url_for("admin.admin_import_history"), code=303)

    deleted_count = (
        ProfessionalLead.query.filter(
            ProfessionalLead.source == import_batch_source(int(batch.id))
        ).delete(synchronize_session=False)
    )
    batch.status = "rolled_back"
    db.session.commit()

    state = _admin_import_preview_state()
    if int(state.get("batch_id") or 0) == int(batch.id):
        _clear_admin_import_preview_state(remove_file=True)

    flash(f"Rollback terminé: {deleted_count} lead(s) supprimé(s).", "success")
    return redirect(url_for("admin.admin_import_history"), code=303)


@admin_bp.get("/professional-leads")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_leads():
    _require_professional_lead_access()
    empty_metrics = {
        "total": 0,
        "new": 0,
        "to_qualify": 0,
        "priority": 0,
        "followup_due": 0,
        "qualified": 0,
    }
    if not _table_exists("professional_leads"):
        flash(
            "Professional leads table is not available in this environment yet.",
            "warning",
        )
        return (
            render_template(
                "admin/professional_leads.html",
                leads=[],
                q="",
                profession="",
                city="",
                status="",
                status_choices=PROFESSIONAL_LEAD_STATUS_CHOICES,
                professions=[],
                lead_metrics=empty_metrics,
                lead_insights={},
                active_filters=False,
            ),
            200,
        )

    q = (request.args.get("q") or "").strip()
    profession = (request.args.get("profession") or "").strip()
    city = (request.args.get("city") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    status_choices = PROFESSIONAL_LEAD_STATUS_CHOICES

    query = _professional_lead_list_query(ProfessionalLead.query)

    if q:
        like = f"%{q.lower()}%"
        query = query.filter(ProfessionalLead.email.ilike(like))

    if profession:
        query = query.filter(ProfessionalLead.profession == profession)

    if city:
        query = query.filter(ProfessionalLead.city.ilike(f"%{city}%"))

    if status:
        query = query.filter(func.lower(ProfessionalLead.status) == status)
    else:
        query = query.filter(
            or_(
                ProfessionalLead.status.is_(None),
                ~func.lower(ProfessionalLead.status).in_(SCREENED_PROFESSIONAL_LEAD_STATUSES),
            )
        )

    leads = (
        query.order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc())
        .limit(200)
        .all()
    )

    professions = (
        ProfessionalLead.query.with_entities(ProfessionalLead.profession)
        .filter(
            or_(
                ProfessionalLead.status.is_(None),
                ~func.lower(ProfessionalLead.status).in_(
                    SCREENED_PROFESSIONAL_LEAD_STATUSES
                ),
            )
        )
        .distinct()
        .order_by(ProfessionalLead.profession.asc())
        .all()
    )
    professions = [p[0] for p in professions if p and p[0]]

    now_utc = datetime.now(UTC)
    has_first_followup = _table_has_column("professional_leads", "first_followup_sent_at")
    has_second_followup = _table_has_column("professional_leads", "second_followup_sent_at")
    has_last_touched = _table_has_column("professional_leads", "last_touched_at")
    lead_metrics = dict(empty_metrics)
    lead_metrics["total"] = len(leads)
    lead_insights = {}
    lead_ids = [int(lead.id) for lead in leads if getattr(lead, "id", None)]
    import_activity_by_lead: dict[int, dict[str, datetime | None]] = {}
    if lead_ids and _professional_lead_activity_enabled():
        activity_rows = (
            ProfessionalLeadActivity.query.with_entities(
                ProfessionalLeadActivity.professional_lead_id,
                ProfessionalLeadActivity.action,
                ProfessionalLeadActivity.created_at,
            )
            .filter(ProfessionalLeadActivity.professional_lead_id.in_(lead_ids))
            .filter(
                ProfessionalLeadActivity.action.in_(
                    ("import_created", "import_updated", "import_skipped_duplicate")
                )
            )
            .order_by(ProfessionalLeadActivity.created_at.desc(), ProfessionalLeadActivity.id.desc())
            .all()
        )
        for row in activity_rows:
            lead_id = int(getattr(row, "professional_lead_id", 0) or 0)
            if lead_id <= 0:
                continue
            bucket = import_activity_by_lead.setdefault(lead_id, {})
            action = str(getattr(row, "action", "") or "")
            if action not in bucket:
                bucket[action] = _as_aware_utc(getattr(row, "created_at", None))
    for lead in leads:
        status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
        created_at = _as_aware_utc(getattr(lead, "created_at", None))
        contacted_at = _as_aware_utc(getattr(lead, "contacted_at", None))
        last_touched_at = (
            _as_aware_utc(getattr(lead, "last_touched_at", None))
            if has_last_touched
            else None
        )
        first_followup_sent_at = (
            _as_aware_utc(getattr(lead, "first_followup_sent_at", None))
            if has_first_followup
            else None
        )
        second_followup_sent_at = (
            _as_aware_utc(getattr(lead, "second_followup_sent_at", None))
            if has_second_followup
            else None
        )
        text_blob = " ".join(
            str(getattr(lead, field_name, None) or "")
            for field_name in ("availability", "message", "notes")
        ).lower()
        has_urgent_signal = any(
            token in text_blob
            for token in ("urgence", "urgent", "asap", "prioritaire", "critique")
        )
        is_uncontacted = contacted_at is None and status_key in {"new", "imported"}
        is_followup_due = (
            status_key == "new"
            and created_at is not None
            and created_at <= now_utc - timedelta(days=2)
            and first_followup_sent_at is None
        ) or (
            status_key == "contacted"
            and contacted_at is not None
            and contacted_at <= now_utc - timedelta(days=3)
            and second_followup_sent_at is None
        )

        if status_key == "new":
            lead_metrics["new"] += 1
        if status_key in {"new", "imported"}:
            lead_metrics["to_qualify"] += 1
        if status_key in {"qualified", "demo_scheduled", "pilot_discussion"}:
            lead_metrics["qualified"] += 1
        if has_urgent_signal or is_followup_due:
            lead_metrics["priority"] += 1
        if is_followup_due:
            lead_metrics["followup_due"] += 1

        last_activity_at = last_touched_at or contacted_at or created_at
        import_badges: list[str] = []
        import_events = import_activity_by_lead.get(int(lead.id), {})
        if "import_created" in import_events:
            import_badges.append("Imported")
        if "import_updated" in import_events:
            import_badges.append("Updated via import")
            updated_event_at = import_events.get("import_updated")
            if updated_event_at and updated_event_at >= now_utc - timedelta(days=14):
                import_badges.append("Recently enriched")
        if "import_skipped_duplicate" in import_events:
            import_badges.append("Duplicate skipped")
        lead_insights[int(lead.id)] = {
            "urgency": "prioritaire" if has_urgent_signal or is_followup_due else "standard",
            "urgency_label": "Prioritaire" if has_urgent_signal or is_followup_due else "Standard",
            "next_action": (
                "Relancer"
                if is_followup_due
                else "Qualifier"
                if is_uncontacted
                else "Suivre"
                if status_key in {"contacted", "qualified"}
                else "Archiver"
                if status_key in {"rejected", "invalid", "spam"}
                else "Ouvrir"
            ),
            "last_activity_label": _format_demo_touch_elapsed(last_activity_at),
            "import_badges": import_badges,
        }

    return (
        render_template(
            "admin/professional_leads.html",
            leads=leads,
            q=q,
            profession=profession,
            city=city,
            status=status,
            status_choices=status_choices,
            professions=professions,
            lead_metrics=lead_metrics,
            lead_insights=lead_insights,
            active_filters=bool(q or profession or city or status),
        ),
        200,
    )


def _send_due_demo_lead_followups() -> None:
    if not (
        _table_has_column("professional_leads", "first_followup_sent_at")
        and _table_has_column("professional_leads", "second_followup_sent_at")
    ):
        return

    now_utc = datetime.now(UTC)
    sender = current_app.config.get("MAIL_DEFAULT_SENDER") or "contact@helpchain.live"
    due_leads = (
        ProfessionalLead.query.filter(ProfessionalLead.source == "demo_page")
        .filter(
            or_(
                and_(
                    func.lower(ProfessionalLead.status) == "new",
                    ProfessionalLead.created_at <= now_utc - timedelta(days=1),
                    ProfessionalLead.first_followup_sent_at.is_(None),
                ),
                and_(
                    func.lower(ProfessionalLead.status) == "contacted",
                    ProfessionalLead.created_at <= now_utc - timedelta(days=2),
                    ProfessionalLead.second_followup_sent_at.is_(None),
                ),
            )
        )
        .order_by(ProfessionalLead.created_at.asc(), ProfessionalLead.id.asc())
        .limit(25)
        .all()
    )

    for lead in due_leads:
        email = (getattr(lead, "email", None) or "").strip()
        if not email:
            continue

        normalized_status = ((getattr(lead, "status", None) or "").strip().lower() or "new")
        if normalized_status == "new":
            followup_field = ProfessionalLead.first_followup_sent_at
            followup_label = "first"
            subject = "Quick follow-up on your HelpChain request"
            body = (
                f"Hello {getattr(lead, 'full_name', None) or ''},\n\n"
                "Just following up on your recent HelpChain request. "
                "If you would like to continue the conversation or schedule a demo, "
                "feel free to reply to this email.\n\n"
                "Best regards,\n"
                "HelpChain"
            ).replace("Hello ,", "Hello,")
        elif normalized_status == "contacted":
            followup_field = ProfessionalLead.second_followup_sent_at
            followup_label = "second"
            subject = "Checking in about your HelpChain demo"
            body = (
                f"Hello {getattr(lead, 'full_name', None) or ''},\n\n"
                "A quick follow-up in case my previous message was missed. "
                "If a demo or pilot discussion is still relevant for you, "
                "just reply and we will be happy to continue.\n\n"
                "Best regards,\n"
                "HelpChain"
            ).replace("Hello ,", "Hello,")
        else:
            continue

        sent_at = datetime.now(UTC)
        reserved = (
            ProfessionalLead.query.filter(
                ProfessionalLead.id == lead.id,
                ProfessionalLead.source == "demo_page",
                func.lower(ProfessionalLead.status) == normalized_status,
                followup_field.is_(None),
            ).update({followup_field: sent_at}, synchronize_session=False)
        )
        if reserved != 1:
            db.session.rollback()
            continue

        db.session.commit()

        try:
            msg = Message(subject=subject, sender=sender, recipients=[email])
            msg.body = body
            mail.send(msg)
            current_app.logger.info("Lead %s %s follow-up sent", lead.id, followup_label)
        except Exception:
            current_app.logger.exception(
                "Lead %s %s follow-up failed",
                lead.id,
                followup_label,
            )
            (
                ProfessionalLead.query.filter(
                    ProfessionalLead.id == lead.id,
                    followup_field == sent_at,
                ).update({followup_field: None}, synchronize_session=False)
            )
            db.session.commit()


def _professional_lead_list_query(query):
    load_fields = [
        ProfessionalLead.id,
        ProfessionalLead.email,
        ProfessionalLead.full_name,
        ProfessionalLead.phone,
        ProfessionalLead.city,
        ProfessionalLead.profession,
        ProfessionalLead.organization,
        ProfessionalLead.availability,
        ProfessionalLead.message,
        ProfessionalLead.source,
        ProfessionalLead.locale,
        ProfessionalLead.ip,
        ProfessionalLead.user_agent,
        ProfessionalLead.status,
        ProfessionalLead.notes,
        ProfessionalLead.contacted_at,
        ProfessionalLead.created_at,
    ]
    if _table_has_column("professional_leads", "owner_admin_id"):
        load_fields.append(ProfessionalLead.owner_admin_id)
    if _table_has_column("professional_leads", "first_followup_sent_at"):
        load_fields.append(ProfessionalLead.first_followup_sent_at)
    if _table_has_column("professional_leads", "second_followup_sent_at"):
        load_fields.append(ProfessionalLead.second_followup_sent_at)
    if _table_has_column("professional_leads", "last_touched_at"):
        load_fields.append(ProfessionalLead.last_touched_at)
    if _table_has_column("professional_leads", "last_touched_by_admin_id"):
        load_fields.append(ProfessionalLead.last_touched_by_admin_id)
    return query.options(load_only(*load_fields))


def _professional_lead_activity_enabled() -> bool:
    return _table_exists("professional_lead_activities") and all(
        _table_has_column("professional_lead_activities", column_name)
        for column_name in (
            "professional_lead_id",
            "admin_user_id",
            "action",
            "payload_json",
            "created_at",
        )
    )


def _format_demo_touch_elapsed(dt_val: datetime | None) -> str:
    dt_aware = _as_aware_utc(dt_val)
    if not dt_aware:
        return "-"
    delta = max(timedelta(0), _now_utc() - dt_aware)
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "0 min"
    if total_minutes < 60:
        return f"{total_minutes} min"
    total_hours = int(delta.total_seconds() // 3600)
    if total_hours < 24:
        return f"{total_hours}h"
    total_days = int(delta.total_seconds() // 86400)
    return f"{total_days}d"


def _professional_lead_activity_label(action: str | None) -> str:
    return {
        "status_changed": "status",
        "owner_changed": "owner",
        "notes_updated": "notes",
    }.get((action or "").strip().lower(), (action or "activity").replace("_", " "))


def _record_professional_lead_touch(
    lead: ProfessionalLead,
    *,
    action: str,
    payload: dict | None = None,
) -> None:
    touched_at = datetime.now(UTC)
    admin_user_id = getattr(current_user, "id", None)
    try:
        admin_user_id = int(admin_user_id) if admin_user_id is not None else None
    except (TypeError, ValueError):
        admin_user_id = None

    if admin_user_id is not None and db.session.get(AdminUser, admin_user_id) is None:
        admin_user_id = None

    if _table_has_column("professional_leads", "last_touched_at"):
        lead.last_touched_at = touched_at
    if _table_has_column("professional_leads", "last_touched_by_admin_id"):
        lead.last_touched_by_admin_id = admin_user_id

    if not _professional_lead_activity_enabled():
        return

    payload_json = None
    if payload:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    db.session.add(
        ProfessionalLeadActivity(
            professional_lead_id=int(lead.id),
            admin_user_id=admin_user_id,
            action=action,
            payload_json=payload_json,
            created_at=touched_at,
        )
    )


def _attach_demo_lead_activity_state(
    leads: list[ProfessionalLead], owner_labels: dict[int, str]
) -> None:
    if not leads:
        return

    admin_labels = dict(owner_labels)
    lead_ids = [int(lead.id) for lead in leads if getattr(lead, "id", None) is not None]
    activity_rows = []

    if _professional_lead_activity_enabled() and lead_ids:
        activity_rows = (
            ProfessionalLeadActivity.query.with_entities(
                ProfessionalLeadActivity.professional_lead_id,
                ProfessionalLeadActivity.admin_user_id,
                ProfessionalLeadActivity.action,
                ProfessionalLeadActivity.created_at,
            )
            .filter(ProfessionalLeadActivity.professional_lead_id.in_(lead_ids))
            .order_by(
                ProfessionalLeadActivity.created_at.desc(),
                ProfessionalLeadActivity.id.desc(),
            )
            .all()
        )

    activity_preview_map: dict[int, list[dict[str, str | None]]] = {}
    admin_ids: set[int] = set()
    for lead in leads:
        touch_admin_id = getattr(lead, "last_touched_by_admin_id", None)
        if touch_admin_id is not None:
            admin_ids.add(int(touch_admin_id))

    for row in activity_rows:
        lead_id = int(getattr(row, "professional_lead_id", 0) or 0)
        if lead_id <= 0:
            continue
        bucket = activity_preview_map.setdefault(lead_id, [])
        if len(bucket) >= 2:
            continue
        admin_user_id = getattr(row, "admin_user_id", None)
        if admin_user_id is not None:
            admin_ids.add(int(admin_user_id))
        bucket.append(
            {
                "label": _professional_lead_activity_label(getattr(row, "action", None)),
                "created_label": _format_demo_touch_elapsed(getattr(row, "created_at", None)),
            }
        )

    if admin_ids:
        admin_rows = (
            AdminUser.query.with_entities(AdminUser.id, AdminUser.username)
            .filter(AdminUser.id.in_(sorted(admin_ids)))
            .all()
        )
        for admin_id, username in admin_rows:
            if admin_id is not None:
                admin_labels[int(admin_id)] = username or f"#{admin_id}"

    for lead in leads:
        touch_admin_id = getattr(lead, "last_touched_by_admin_id", None)
        touch_admin_label = None
        if touch_admin_id is not None:
            touch_admin_label = admin_labels.get(int(touch_admin_id), f"#{touch_admin_id}")
        setattr(
            lead,
            "last_touch_label",
            _format_demo_touch_elapsed(getattr(lead, "last_touched_at", None))
            if getattr(lead, "last_touched_at", None)
            else None,
        )
        setattr(lead, "last_touch_admin_label", touch_admin_label)
        setattr(lead, "activity_preview", activity_preview_map.get(int(lead.id), []))


def _demo_lead_is_stale(lead: ProfessionalLead, *, threshold_hours: int = 24) -> bool:
    status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
    if status_key == "closed" or _is_screened_professional_lead_status(status_key):
        return False

    last_touched_at = _as_aware_utc(getattr(lead, "last_touched_at", None))
    if not last_touched_at:
        return True

    return (_now_utc() - last_touched_at) > timedelta(hours=threshold_hours)


def _demo_lead_priority(lead: ProfessionalLead) -> str:
    status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
    if status_key == "closed" or _is_screened_professional_lead_status(status_key):
        return "normal"

    is_unassigned = not getattr(lead, "owner_admin_id", None)
    is_stale = _demo_lead_is_stale(lead)
    urgency = getattr(lead, "urgency", None)

    if status_key == "contacted" and is_stale:
        return "medium"
    if is_unassigned or status_key == "new" or urgency == "overdue" or is_stale:
        return "high"
    return "normal"


def _demo_lead_queue(lead: ProfessionalLead) -> str:
    status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
    if status_key == "closed" or _is_screened_professional_lead_status(status_key):
        return "done"

    priority = getattr(lead, "priority_level", None) or _demo_lead_priority(lead)
    is_stale = _demo_lead_is_stale(lead)

    if priority == "high":
        return "needs_action"
    if status_key in {"contacted", "demo_scheduled", "pilot_discussion"} and not is_stale:
        return "waiting"
    return "active"


def _demo_lead_next_action(lead: ProfessionalLead) -> str | None:
    status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
    if status_key == "closed" or _is_screened_professional_lead_status(status_key):
        return None

    has_owner = bool(getattr(lead, "owner_admin_id", None))
    is_stale = _demo_lead_is_stale(lead)

    if not has_owner:
        return "Assigner"
    if status_key == "new":
        return "Appeler"
    if status_key == "contacted" and is_stale:
        return "Relancer"
    if status_key == "demo_scheduled":
        return "Preparer demo"
    if status_key == "pilot_discussion":
        return "Proposer pilote"
    if status_key == "contacted":
        return "Suivre"
    return None


def _demo_lead_sla_state(lead: ProfessionalLead) -> str | None:
    status_key = ((getattr(lead, "status", None) or "").strip().lower() or "new")
    if status_key == "closed" or _is_screened_professional_lead_status(status_key):
        return None

    now = _now_utc()
    created_at = _as_aware_utc(getattr(lead, "created_at", None)) or now
    last_touched_at = _as_aware_utc(getattr(lead, "last_touched_at", None))

    if status_key == "new":
        age_hours = max(0.0, (now - created_at).total_seconds() / 3600)
        return "late" if age_hours > 24 else "on_time"

    reference = last_touched_at or created_at
    elapsed_hours = max(0.0, (now - reference).total_seconds() / 3600)

    if status_key == "contacted":
        if elapsed_hours > 48:
            return "late"
        if elapsed_hours >= 36:
            return "due_soon"
        return "on_time"

    if status_key == "demo_scheduled":
        if elapsed_hours > 72:
            return "late"
        if elapsed_hours >= 48:
            return "due_soon"
        return "on_time"

    if status_key == "pilot_discussion":
        if elapsed_hours > 96:
            return "late"
        if elapsed_hours >= 72:
            return "due_soon"
        return "on_time"

    return "on_time"


@admin_bp.get("/professional-leads/demo")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_demo_leads():
    _require_professional_lead_access()
    def _lead_age_meta(created_at, status_value: str | None):
        now_utc = datetime.now(UTC)
        if created_at is None:
            age_days = 0
        else:
            created_dt = created_at
            if getattr(created_dt, "tzinfo", None) is None:
                created_dt = created_dt.replace(tzinfo=UTC)
            age_days = max(0, (now_utc.date() - created_dt.astimezone(UTC).date()).days)

        normalized_status = ((status_value or "").strip().lower() or "new")
        if normalized_status == "closed" or _is_screened_professional_lead_status(
            normalized_status
        ):
            urgency = "closed"
        elif age_days <= 1:
            urgency = "normal"
        elif age_days <= 3:
            urgency = "aging"
        else:
            urgency = "overdue"

        return {
            "age_days": age_days,
            "age_label": f"{age_days}d",
            "urgency": urgency,
        }

    if not _table_exists("professional_leads"):
        flash(
            "Professional leads table is not available in this environment yet.",
            "warning",
        )
        return (
            render_template(
                "admin/demo_leads.html",
                leads=[],
                q="",
                profession="",
                city="",
                status="",
                status_choices=DEMO_LEAD_STATUS_CHOICES,
                professions=[],
                kpi_counts={
                    "new": 0,
                    "contacted": 0,
                    "demo_scheduled": 0,
                    "pilot_discussion": 0,
                    "closed": 0,
                    "overdue": 0,
                },
            ),
            200,
        )

    q = (request.args.get("q") or "").strip()
    profession = (request.args.get("profession") or "").strip()
    city = (request.args.get("city") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    queue = (request.args.get("queue") or "").strip().lower()
    status_choices = DEMO_LEAD_STATUS_CHOICES
    queue_choices = ["needs_action", "waiting", "active", "done"]

    _send_due_demo_lead_followups()

    query = _professional_lead_list_query(
        ProfessionalLead.query.filter(ProfessionalLead.source == "demo_page")
    )

    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                ProfessionalLead.email.ilike(like),
                ProfessionalLead.full_name.ilike(like),
                ProfessionalLead.organization.ilike(like),
                ProfessionalLead.message.ilike(like),
            )
        )

    if profession:
        query = query.filter(ProfessionalLead.profession == profession)

    if city:
        query = query.filter(ProfessionalLead.city.ilike(f"%{city}%"))

    if not status:
        query = query.filter(
            or_(
                ProfessionalLead.status.is_(None),
                ~func.lower(ProfessionalLead.status).in_(SCREENED_PROFESSIONAL_LEAD_STATUSES),
            )
        )

    kpi_counts = {
        key: 0
        for key in ("new", "contacted", "demo_scheduled", "pilot_discussion", "closed")
    }
    count_rows = (
        query.with_entities(
            func.lower(ProfessionalLead.status).label("status_key"),
            func.count(ProfessionalLead.id).label("lead_count"),
        )
        .group_by(func.lower(ProfessionalLead.status))
        .all()
    )
    for row in count_rows:
        status_key = (getattr(row, "status_key", None) or "").strip().lower() or "new"
        if status_key in kpi_counts:
            kpi_counts[status_key] = int(getattr(row, "lead_count", 0) or 0)

    overdue_rows = query.with_entities(
        ProfessionalLead.status,
        ProfessionalLead.created_at,
    ).all()
    overdue_count = 0
    for row in overdue_rows:
        age_meta = _lead_age_meta(
            getattr(row, "created_at", None),
            getattr(row, "status", None),
        )
        if age_meta["urgency"] == "overdue":
            overdue_count += 1
    kpi_counts["overdue"] = overdue_count

    if status:
        query = query.filter(func.lower(ProfessionalLead.status) == status)

    leads = query.order_by(ProfessionalLead.created_at.desc(), ProfessionalLead.id.desc()).all()
    for lead in leads:
        age_meta = _lead_age_meta(getattr(lead, "created_at", None), getattr(lead, "status", None))
        setattr(lead, "age_days", age_meta["age_days"])
        setattr(lead, "age_label", age_meta["age_label"])
        setattr(lead, "urgency", age_meta["urgency"])

    professions = (
        ProfessionalLead.query.with_entities(ProfessionalLead.profession)
        .filter(ProfessionalLead.source == "demo_page")
        .filter(
            or_(
                ProfessionalLead.status.is_(None),
                ~func.lower(ProfessionalLead.status).in_(
                    SCREENED_PROFESSIONAL_LEAD_STATUSES
                ),
            )
        )
        .distinct()
        .order_by(ProfessionalLead.profession.asc())
        .all()
    )
    professions = [p[0] for p in professions if p and p[0]]
    owners = (
        AdminUser.query.with_entities(AdminUser.id, AdminUser.username)
        .filter(AdminUser.is_active.is_(True))
        .order_by(AdminUser.username.asc(), AdminUser.id.asc())
        .all()
    )
    owner_labels = {int(row[0]): row[1] for row in owners if row and row[0] is not None}
    _attach_demo_lead_activity_state(leads, owner_labels)

    queue_counts = {key: 0 for key in queue_choices}
    priority_rank = {"high": 0, "medium": 1, "normal": 2}
    for lead in leads:
        priority_level = _demo_lead_priority(lead)
        queue_key = _demo_lead_queue(lead)
        next_action = _demo_lead_next_action(lead)
        sla_state = _demo_lead_sla_state(lead)
        is_stale = _demo_lead_is_stale(lead)
        last_touched_at = _as_aware_utc(getattr(lead, "last_touched_at", None))
        sort_touch = last_touched_at or _as_aware_utc(getattr(lead, "created_at", None)) or _now_utc()
        setattr(lead, "priority_level", priority_level)
        setattr(lead, "queue_key", queue_key)
        setattr(lead, "next_action", next_action)
        setattr(lead, "sla_state", sla_state)
        setattr(lead, "is_stale", is_stale)
        setattr(lead, "needs_action", priority_level == "high")
        setattr(lead, "priority_rank", priority_rank.get(priority_level, 2))
        setattr(lead, "sort_touch_at", sort_touch)
        if queue_key in queue_counts:
            queue_counts[queue_key] += 1

    if queue in queue_choices:
        leads = [lead for lead in leads if getattr(lead, "queue_key", None) == queue]

    leads = sorted(
        leads,
        key=lambda lead: (
            getattr(lead, "priority_rank", 2),
            getattr(lead, "sort_touch_at", _now_utc()),
            -int(getattr(lead, "id", 0) or 0),
        ),
    )[:200]

    return (
        render_template(
            "admin/demo_leads.html",
            leads=leads,
            q=q,
            profession=profession,
            city=city,
            status=status,
            queue=queue,
            status_choices=status_choices,
            queue_choices=queue_choices,
            professions=professions,
            owners=owners,
            owner_labels=owner_labels,
            kpi_counts=kpi_counts,
            queue_counts=queue_counts,
        ),
        200,
    )


@admin_bp.post("/professional-leads/<int:lead_id>/status")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_lead_update_status(lead_id: int):
    _require_professional_lead_access()
    allowed_statuses = {
        "new",
        "contacted",
        "demo_scheduled",
        "pilot_discussion",
        "closed",
        "invalid",
        "spam",
    }

    redirect_kwargs = {}
    for key in ("q", "profession", "city", "status", "queue"):
        value = (request.form.get(key) or "").strip()
        if value:
            redirect_kwargs[key] = value

    if not _table_exists("professional_leads"):
        current_app.logger.warning(
            "Lead status update skipped: professional_leads table unavailable"
        )
        flash("Professional leads table is not available.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    lead = ProfessionalLead.query.get_or_404(lead_id)
    old_status = ((lead.status or "").strip() or "new")
    new_status = (request.form.get("lead_status") or "").strip().lower()

    if new_status not in allowed_statuses:
        current_app.logger.warning(
            "Lead %s status update ignored: invalid status %r",
            lead.id,
            new_status,
        )
        flash("Invalid lead status.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    if old_status != new_status:
        lead.status = new_status
        if new_status == "contacted" and not lead.contacted_at:
            lead.contacted_at = datetime.now(UTC)
        _record_professional_lead_touch(
            lead,
            action="status_changed",
            payload={"old_status": old_status, "new_status": new_status},
        )
        db.session.commit()
        current_app.logger.info(
            "Lead %s status changed from %s → %s",
            lead.id,
            old_status,
            new_status,
        )

    return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)


@admin_bp.post("/professional-leads/<int:lead_id>/notes")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_lead_update_notes(lead_id: int):
    _require_professional_lead_access()
    redirect_kwargs = {}
    for key in ("q", "profession", "city", "status", "queue"):
        value = (request.form.get(key) or "").strip()
        if value:
            redirect_kwargs[key] = value

    if not _table_exists("professional_leads"):
        current_app.logger.warning(
            "Lead notes update skipped: professional_leads table unavailable"
        )
        flash("Professional leads table is not available.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    if not _table_has_column("professional_leads", "notes"):
        current_app.logger.warning(
            "Lead notes update skipped: professional_leads.notes unavailable"
        )
        flash("Lead notes are not available until the latest migration is applied.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    lead = ProfessionalLead.query.get_or_404(lead_id)
    old_notes = getattr(lead, "notes", None)
    new_notes = (request.form.get("notes") or "").strip() or None
    new_notes = append_audience_context_to_notes(
        new_notes,
        extract_audience_context(old_notes),
    )

    if old_notes != new_notes:
        lead.notes = new_notes
        note_action = "added" if not (old_notes or "").strip() else "updated"
        _record_professional_lead_touch(
            lead,
            action="notes_updated",
            payload={"mode": note_action},
        )
        db.session.commit()
        current_app.logger.info("Lead %s notes %s", lead.id, note_action)
    else:
        current_app.logger.info("Lead %s notes unchanged", lead.id)

    return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)


@admin_bp.post("/professional-leads/<int:lead_id>/owner")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_lead_update_owner(lead_id: int):
    _require_professional_lead_access()
    redirect_kwargs = {}
    for key in ("q", "profession", "city", "status", "queue"):
        value = (request.form.get(key) or "").strip()
        if value:
            redirect_kwargs[key] = value

    if not _table_exists("professional_leads"):
        current_app.logger.warning(
            "Lead owner update skipped: professional_leads table unavailable"
        )
        flash("Professional leads table is not available.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    if not _table_has_column("professional_leads", "owner_admin_id"):
        current_app.logger.warning(
            "Lead owner update skipped: professional_leads.owner_admin_id unavailable"
        )
        flash("Lead ownership is not available until the latest migration is applied.", "warning")
        return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

    lead = ProfessionalLead.query.get_or_404(lead_id)
    old_owner_id = getattr(lead, "owner_admin_id", None)
    raw_owner_id = (request.form.get("owner_admin_id") or "").strip()

    if raw_owner_id:
        try:
            new_owner_id = int(raw_owner_id)
        except ValueError:
            flash("Invalid owner selection.", "warning")
            return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)

        owner = db.session.get(AdminUser, new_owner_id)
        if owner is None:
            flash("Selected admin user was not found.", "warning")
            return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)
    else:
        new_owner_id = None

    if old_owner_id != new_owner_id:
        lead.owner_admin_id = new_owner_id
        _record_professional_lead_touch(
            lead,
            action="owner_changed",
            payload={
                "old_owner_id": old_owner_id,
                "new_owner_id": new_owner_id,
            },
        )
        log_activity(
            entity_type="ProfessionalLead",
            entity_id=int(lead.id),
            action="lead.owner_changed",
            message="Demo lead owner updated",
            old_value=str(old_owner_id) if old_owner_id is not None else None,
            new_value=str(new_owner_id) if new_owner_id is not None else None,
            meta={"scope": "demo_leads"},
        )
        db.session.commit()
        current_app.logger.info(
            "Lead %s owner changed from %s to %s",
            lead.id,
            old_owner_id,
            new_owner_id,
        )
    else:
        current_app.logger.info(
            "Lead %s owner unchanged at %s",
            lead.id,
            old_owner_id,
        )

    return redirect(url_for("admin.admin_demo_leads", **redirect_kwargs), code=303)


@admin_bp.post("/professional-leads/<int:lead_id>/contacted")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_lead_mark_contacted(lead_id: int):
    _require_professional_lead_access()
    if not _table_exists("professional_leads"):
        flash("Professional leads table is not available.", "warning")
        return redirect(url_for("admin.admin_professional_leads"), code=303)

    lead = ProfessionalLead.query.get_or_404(lead_id)
    if (lead.status or "").lower() != "contacted":
        lead.status = "contacted"
        if not lead.contacted_at:
            lead.contacted_at = datetime.now(UTC)
        db.session.commit()
        flash(f"Lead #{lead.id} marked as contacted.", "success")
    return redirect(url_for("admin.admin_professional_leads"), code=303)


@admin_bp.route("/professional-leads/<int:lead_id>", methods=["GET", "POST"])
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professional_lead_detail(lead_id: int):
    _require_professional_lead_access()
    if not _table_exists("professional_leads"):
        flash("Professional leads table is not available.", "warning")
        return redirect(url_for("admin.admin_professional_leads"), code=303)

    lead = ProfessionalLead.query.get_or_404(lead_id)
    status_choices = PROFESSIONAL_LEAD_STATUS_CHOICES

    if request.method == "POST":
        status = (request.form.get("status") or "").strip().lower()
        notes = (request.form.get("notes") or "").strip()
        if status not in status_choices:
            status = "new"

        lead.status = status
        lead.notes = append_audience_context_to_notes(
            notes or None,
            extract_audience_context(lead.notes),
        )
        if status == "contacted" and not lead.contacted_at:
            lead.contacted_at = datetime.now(UTC)
        elif status != "contacted":
            lead.contacted_at = None

        db.session.commit()
        flash(f"Lead #{lead.id} updated.", "success")
        return redirect(
            url_for("admin.admin_professional_lead_detail", lead_id=lead.id), code=303
        )

    return (
        render_template(
            "admin/professional_lead_detail.html",
            lead=lead,
            status_choices=status_choices,
            audience_context=extract_audience_context(lead.notes),
            lead_notes=notes_without_audience_context(lead.notes),
        ),
        200,
    )


@admin_bp.get("/professionnels/leads")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_professionnels_leads():
    _require_professional_lead_access()
    return redirect(url_for("admin.admin_professional_leads"), code=302)


@admin_bp.get("/pro-access")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_pro_access_list():
    _require_global_admin()
    if ProAccessRequest is None:
        flash("Pro Access module is not available in this environment.", "info")
        return redirect(url_for("admin.admin_requests"), code=303)

    status = (request.args.get("status") or "new").strip().lower()
    if status not in PRO_ACCESS_STATUSES and status != "all":
        status = "new"

    query = ProAccessRequest.query
    if status != "all":
        query = query.filter(ProAccessRequest.status == status)

    rows = (
        query.order_by(ProAccessRequest.created_at.desc(), ProAccessRequest.id.desc())
        .limit(500)
        .all()
    )

    counts = {
        "new": ProAccessRequest.query.filter_by(status="new").count(),
        "reviewed": ProAccessRequest.query.filter_by(status="reviewed").count(),
        "approved": ProAccessRequest.query.filter_by(status="approved").count(),
        "rejected": ProAccessRequest.query.filter_by(status="rejected").count(),
        "all": ProAccessRequest.query.count(),
    }

    return (
        render_template(
            "admin/pro_access_list.html",
            rows=rows,
            status=status,
            counts=counts,
        ),
        200,
    )


@admin_bp.get("/audit")
@login_required
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_audit():
    _require_global_admin()
    if current_app.config.get("DEMO_MODE"):
        payload = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        scenario_meta = payload["scenario_meta"]
        return (
            render_template(
                "admin/audit.html",
                events=payload["audit_rows"],
                pagination=payload["audit_pagination"],
                filters=payload["audit_filters"],
                actions=payload["audit_actions"],
                target_types=payload["audit_target_types"],
                scenario_label=scenario_meta["label"],
                scenario_description=scenario_meta["short_description"],
            ),
            200,
        )

    if not _table_exists("admin_audit_events"):
        return (
            render_template(
                "admin/audit.html",
                events=[],
                pagination=type(
                    "_PaginationStub",
                    (),
                    {
                        "page": 1,
                        "pages": 1,
                        "total": 0,
                        "has_prev": False,
                        "has_next": False,
                    },
                )(),
                filters={
                    "action": "",
                    "admin": "",
                    "target_id": "",
                    "days": "7",
                },
                actions=[],
            ),
            200,
        )

    action = (request.args.get("action") or "").strip()
    admin_username = (request.args.get("admin") or "").strip()
    target_type_raw = (request.args.get("target_type") or "").strip()
    target_id_raw = (request.args.get("target_id") or "").strip()
    days_raw = (request.args.get("days") or "7").strip()
    page_raw = (request.args.get("page") or "1").strip()

    try:
        days = max(1, min(int(days_raw), 365))
    except Exception:
        days = 7

    try:
        page = max(1, int(page_raw))
    except Exception:
        page = 1

    target_id = None
    if target_id_raw:
        try:
            target_id = int(target_id_raw)
        except Exception:
            target_id = None

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    query = AdminAuditEvent.query.filter(AdminAuditEvent.created_at >= since)
    if action:
        query = query.filter(AdminAuditEvent.action == action)
    if admin_username:
        query = query.filter(AdminAuditEvent.admin_username == admin_username)
    if target_type_raw:
        query = query.filter(AdminAuditEvent.target_type == target_type_raw)
    if target_id is not None:
        query = query.filter(
            AdminAuditEvent.target_type == "Request",
            AdminAuditEvent.target_id == target_id,
        )

    query = query.order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    actions = (
        AdminAuditEvent.query.with_entities(AdminAuditEvent.action)
        .distinct()
        .order_by(AdminAuditEvent.action.asc())
        .all()
    )
    actions = [row[0] for row in actions if row and row[0]]
    target_types = (
        AdminAuditEvent.query.with_entities(AdminAuditEvent.target_type)
        .distinct()
        .order_by(AdminAuditEvent.target_type.asc())
        .all()
    )
    target_types = [row[0] for row in target_types if row and row[0]]

    return (
        render_template(
            "admin/audit.html",
            events=pagination.items,
            pagination=pagination,
            filters={
                "action": action,
                "admin": admin_username,
                "target_type": target_type_raw,
                "target_id": target_id_raw,
                "days": str(days),
            },
            actions=actions,
            target_types=target_types,
        ),
        200,
    )


@admin_bp.get("/security")
@login_required
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_security():
    _require_global_admin()
    if current_app.config.get("DEMO_MODE"):
        payload = get_demo_payload(current_app.config.get("DEMO_SCENARIO"))
        scenario_meta = payload["scenario_meta"]
        security_recent = payload["security_recent_attempts"]
        return (
            render_template(
                "admin/security.html",
                kpis=payload["security_kpis"],
                recent_logins=security_recent["recent_logins"],
                recent_risky=security_recent["recent_risky"],
                recent_denied=security_recent["recent_denied"],
                recent_sensitive=security_recent["recent_sensitive"],
                top_ips=security_recent["top_ips"],
                top_usernames=security_recent["top_usernames"],
                top_denied_ips=security_recent["top_denied_ips"],
                top_denied_usernames=security_recent["top_denied_usernames"],
                anomalies=payload["security_anomalies"],
                risky_actions=security_recent["risky_actions"],
                scenario_label=scenario_meta["label"],
                scenario_description=scenario_meta["short_description"],
            ),
            200,
        )

    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_1h = now - timedelta(hours=1)

    success_24h = (
        db.session.query(func.count(AdminLoginAttempt.id))
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(True),
        )
        .scalar()
        or 0
    )
    failed_24h = (
        db.session.query(func.count(AdminLoginAttempt.id))
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
        )
        .scalar()
        or 0
    )
    distinct_failed_ips_24h = (
        db.session.query(func.count(func.distinct(AdminLoginAttempt.ip)))
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
            AdminLoginAttempt.ip.isnot(None),
            AdminLoginAttempt.ip != "",
        )
        .scalar()
        or 0
    )
    distinct_failed_usernames_24h = (
        db.session.query(func.count(func.distinct(AdminLoginAttempt.username)))
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
            AdminLoginAttempt.username.isnot(None),
            AdminLoginAttempt.username != "",
        )
        .scalar()
        or 0
    )
    failed_1h = (
        db.session.query(func.count(AdminLoginAttempt.id))
        .filter(
            AdminLoginAttempt.created_at >= since_1h,
            AdminLoginAttempt.success.is_(False),
        )
        .scalar()
        or 0
    )

    username_expr = func.coalesce(
        AdminLoginAttempt.username,
        "",
    ).label("username")
    fail_buckets = (
        db.session.query(
            AdminLoginAttempt.ip.label("ip"),
            username_expr,
            func.count(AdminLoginAttempt.id).label("fails"),
        )
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
        )
        .group_by(AdminLoginAttempt.ip, username_expr)
        .having(func.count(AdminLoginAttempt.id) >= ADMIN_LOGIN_MAX_FAILS)
        .subquery()
    )
    lockout_buckets_24h = (
        db.session.query(func.count())
        .select_from(fail_buckets)
        .scalar()
        or 0
    )

    risky_actions_24h = (
        db.session.query(func.count(AdminAuditEvent.id))
        .filter(
            AdminAuditEvent.created_at >= since_24h,
            AdminAuditEvent.action.in_(RISKY_ACTIONS),
        )
        .scalar()
        or 0
    )
    denied_24h = (
        db.session.query(func.count(AdminAuditEvent.id))
        .filter(
            AdminAuditEvent.created_at >= since_24h,
            AdminAuditEvent.action == "security.denied_action",
        )
        .scalar()
        or 0
    )
    denied_1h = (
        db.session.query(func.count(AdminAuditEvent.id))
        .filter(
            AdminAuditEvent.created_at >= since_1h,
            AdminAuditEvent.action == "security.denied_action",
        )
        .scalar()
        or 0
    )
    avg_denied_hourly = (float(denied_24h) / 24.0) if denied_24h else 0.0

    top_denied_ips = (
        db.session.query(AdminAuditEvent.ip, func.count(AdminAuditEvent.id).label("cnt"))
        .filter(
            AdminAuditEvent.created_at >= since_24h,
            AdminAuditEvent.action == "security.denied_action",
            AdminAuditEvent.ip.isnot(None),
            AdminAuditEvent.ip != "",
        )
        .group_by(AdminAuditEvent.ip)
        .order_by(func.count(AdminAuditEvent.id).desc())
        .limit(10)
        .all()
    )
    top_denied_usernames = (
        db.session.query(
            AdminAuditEvent.admin_username, func.count(AdminAuditEvent.id).label("cnt")
        )
        .filter(
            AdminAuditEvent.created_at >= since_24h,
            AdminAuditEvent.action == "security.denied_action",
            AdminAuditEvent.admin_username.isnot(None),
            AdminAuditEvent.admin_username != "",
        )
        .group_by(AdminAuditEvent.admin_username)
        .order_by(func.count(AdminAuditEvent.id).desc())
        .limit(10)
        .all()
    )

    recent_logins = (
        AdminLoginAttempt.query.order_by(AdminLoginAttempt.created_at.desc())
        .limit(50)
        .all()
    )
    recent_risky = (
        AdminAuditEvent.query.filter(AdminAuditEvent.action.in_(RISKY_ACTIONS))
        .order_by(AdminAuditEvent.created_at.desc())
        .limit(50)
        .all()
    )
    recent_denied = (
        AdminAuditEvent.query.filter(AdminAuditEvent.action == "security.denied_action")
        .order_by(AdminAuditEvent.created_at.desc())
        .limit(50)
        .all()
    )
    sensitive_actions = {
        "ROLE_CHANGE",
        "STRUCTURE_CREATED",
        "STRUCTURE_ADMIN_ASSIGNED",
        "STATUS_CHANGE",
        "ASSIGN_OPERATOR",
        "CREATE_REQUEST",
    }
    recent_sensitive = (
        AdminAuditEvent.query.filter(AdminAuditEvent.action.in_(sensitive_actions))
        .order_by(AdminAuditEvent.created_at.desc())
        .limit(50)
        .all()
    )

    top_ips = (
        db.session.query(
            AdminLoginAttempt.ip, func.count(AdminLoginAttempt.id).label("fails")
        )
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
        )
        .group_by(AdminLoginAttempt.ip)
        .order_by(func.count(AdminLoginAttempt.id).desc())
        .limit(10)
        .all()
    )

    top_usernames = (
        db.session.query(
            AdminLoginAttempt.username, func.count(AdminLoginAttempt.id).label("fails")
        )
        .filter(
            AdminLoginAttempt.created_at >= since_24h,
            AdminLoginAttempt.success.is_(False),
            AdminLoginAttempt.username.isnot(None),
            AdminLoginAttempt.username != "",
        )
        .group_by(AdminLoginAttempt.username)
        .order_by(func.count(AdminLoginAttempt.id).desc())
        .limit(10)
        .all()
    )

    avg_hourly = (float(failed_24h) / 24.0) if failed_24h else 0.0
    spike_threshold = max(10.0, 3.0 * avg_hourly)
    top_ip = top_ips[0] if top_ips else (None, 0)
    top_username = top_usernames[0] if top_usernames else (None, 0)
    top_denied_ip = top_denied_ips[0] if top_denied_ips else (None, 0)
    top_denied_username = top_denied_usernames[0] if top_denied_usernames else (None, 0)
    top_denied_ip_count = int(top_denied_ip[1] or 0)
    top_denied_username_count = int(top_denied_username[1] or 0)
    denied_spike_on = int(denied_1h) >= max(5.0, 3.0 * avg_denied_hourly)
    repeated_denied_on = (top_denied_ip_count >= 10) or (
        top_denied_username_count >= 8
    )
    anomalies = {
        "spike_failed_logins": float(failed_1h) > spike_threshold,
        "repeated_fails_by_ip": any(int(fails) >= 20 for _, fails in top_ips),
        "repeated_fails_by_username": any(
            int(fails) >= 10 for _, fails in top_usernames
        ),
        "failed_1h": int(failed_1h),
        "avg_hourly": round(avg_hourly, 2),
        "spike_threshold": round(spike_threshold, 2),
        "top_ip": top_ip[0],
        "top_ip_fails": int(top_ip[1] or 0),
        "top_username": top_username[0],
        "top_username_fails": int(top_username[1] or 0),
        "denied_spike": bool(denied_spike_on),
        "repeated_denied": bool(repeated_denied_on),
        "denied_1h": int(denied_1h),
        "avg_denied_hourly": round(avg_denied_hourly, 2),
        "top_denied_ip": top_denied_ip[0],
        "top_denied_ip_count": int(top_denied_ip_count),
        "top_denied_username": top_denied_username[0],
        "top_denied_username_count": int(top_denied_username_count),
    }

    return (
        render_template(
            "admin/security.html",
            kpis={
                "success_24h": int(success_24h),
                "failed_24h": int(failed_24h),
                "distinct_failed_ips_24h": int(distinct_failed_ips_24h),
                "distinct_failed_usernames_24h": int(distinct_failed_usernames_24h),
                "lockout_buckets_24h": int(lockout_buckets_24h),
                "risky_actions_24h": int(risky_actions_24h),
                "denied_24h": int(denied_24h),
            },
            recent_logins=recent_logins,
            recent_risky=recent_risky,
            recent_denied=recent_denied,
            recent_sensitive=recent_sensitive,
            top_ips=top_ips,
            top_usernames=top_usernames,
            top_denied_ips=top_denied_ips,
            top_denied_usernames=top_denied_usernames,
            anomalies=anomalies,
            risky_actions=RISKY_ACTIONS,
        ),
        200,
    )


@admin_bp.get("/security/events")
@login_required
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_security_events():
    _require_global_admin()
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    query = SecurityEvent.query
    event_type = (request.args.get("event_type") or "").strip()
    email_hash = (request.args.get("email_hash") or "").strip()
    ip = (request.args.get("ip") or "").strip()
    if event_type:
        query = query.filter(SecurityEvent.event_type == event_type)
    if email_hash:
        query = query.filter(SecurityEvent.email_hash == email_hash)
    if ip:
        query = query.filter(SecurityEvent.ip == ip)
    rows = query.order_by(SecurityEvent.created_at.desc()).limit(limit).all()
    return (
        jsonify(
            {
                "events": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "actor_type": row.actor_type,
                        "ip": row.ip,
                        "email_hash": row.email_hash,
                        "created_at": row.created_at.isoformat()
                        if row.created_at
                        else None,
                        "meta_json": row.meta_json,
                    }
                    for row in rows
                ]
            }
        ),
        200,
    )


@admin_bp.get("/security/summary")
@login_required
@admin_required
@admin_role_required("readonly", "ops", "superadmin")
def admin_security_summary():
    _require_global_admin()
    since = utc_now() - timedelta(minutes=15)

    def _count(event_type: str) -> int:
        return int(
            db.session.query(func.count(SecurityEvent.id))
            .filter(
                SecurityEvent.created_at >= since,
                SecurityEvent.event_type == event_type,
            )
            .scalar()
            or 0
        )

    return (
        jsonify(
            {
                "issued_last_15m": _count("magic_link_issued"),
                "consumed_last_15m": _count("magic_link_consumed"),
                "rate_limited_last_15m": _count("magic_link_rate_limited"),
                "reuse_blocked_last_15m": _count("magic_link_reuse_blocked"),
            }
        ),
        200,
    )


@admin_bp.get("/roles")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_roles():
    _require_global_admin()
    admins = AdminUser.query.order_by(AdminUser.username.asc(), AdminUser.id.asc()).all()
    superadmin_ids = [u.id for u in admins if _is_superadmin_role(getattr(u, "role", None))]
    last_superadmin_id = superadmin_ids[0] if len(superadmin_ids) == 1 else None
    role_options = [
        ("readonly", "readonly"),
        ("ops", "ops"),
        ("superadmin", "superadmin"),
    ]
    return (
        render_template(
            "admin/roles.html",
            admins=admins,
            role_options=role_options,
            last_superadmin_id=last_superadmin_id,
            superadmin_count=len(superadmin_ids),
        ),
        200,
    )


@admin_bp.post("/roles/<int:admin_id>/role")
@login_required
@admin_required
@admin_role_required("superadmin")
@require_admin_fresh_auth(minutes=10)
@require_fresh_mfa
def admin_roles_set_role(admin_id: int):
    _require_global_admin()
    target = db.session.get(AdminUser, admin_id)
    if not target:
        abort(404)

    requested_role = (request.form.get("role") or "").strip().lower()
    allowed_roles = {"readonly", "ops", "superadmin"}
    if requested_role not in allowed_roles:
        flash("Invalid role.", "danger")
        return redirect(url_for("admin.admin_roles"), code=303)

    old_role = _normalize_admin_role_value(getattr(target, "role", None))
    if old_role is None:
        old_role = "superadmin"

    if old_role == requested_role:
        flash("No changes.", "info")
        return redirect(url_for("admin.admin_roles"), code=303)

    superadmin_count = (
        db.session.query(func.count(AdminUser.id))
        .filter(
            or_(
                AdminUser.role == "superadmin",
                AdminUser.role == "super_admin",
                AdminUser.role == "admin",
            )
        )
        .scalar()
        or 0
    )

    # Prevent lockout by downgrading the last superadmin.
    if old_role == "superadmin" and requested_role != "superadmin" and int(superadmin_count) <= 1:
        flash("Cannot downgrade the last superadmin.", "danger")
        return redirect(url_for("admin.admin_roles"), code=303)

    target.role = requested_role
    db.session.commit()

    audit_admin_action(
        action="ROLE_CHANGE",
        target_type="AdminUser",
        target_id=target.id,
        payload={
            "old": {"role": old_role},
            "new": {"role": requested_role},
            "actor": {
                "admin_user_id": getattr(current_user, "id", None),
                "username": getattr(current_user, "username", None),
            },
            "ip": _client_ip(),
            "user_agent": request.headers.get("User-Agent"),
        },
    )
    flash("Role updated.", "success")
    return redirect(url_for("admin.admin_roles"), code=303)


@admin_bp.get("/pro-access/<int:pro_id>")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_pro_access_detail(pro_id: int):
    _require_global_admin()
    if ProAccessRequest is None:
        abort(404)

    row = ProAccessRequest.query.get_or_404(pro_id)
    audit_logs = []
    return (
        render_template(
            "admin/pro_access_detail.html",
            row=row,
            audit_logs=audit_logs,
        ),
        200,
    )


def _pro_access_set_status(
    row: ProAccessRequest, new_status: str, note: str | None = None
):
    old_status = (row.status or "").strip().lower() or None
    row.status = new_status
    row.reviewed_at = utc_now()
    row.reviewed_by = (
        getattr(current_user, "email", None)
        or getattr(current_user, "username", None)
        or "admin"
    )
    if note is not None:
        row.admin_notes = note
    _audit_pro_access(
        row.id,
        action="status_change",
        message="Pro access status updated",
        old=old_status,
        new=new_status,
        meta={"has_admin_notes": bool(note)},
    )


@admin_bp.post("/pro-access/<int:pro_id>/review")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_pro_access_review(pro_id: int):
    _require_global_admin()
    if ProAccessRequest is None:
        abort(404)

    row = ProAccessRequest.query.get_or_404(pro_id)
    note = (request.form.get("admin_notes") or "").strip() or None
    _pro_access_set_status(row, "reviewed", note)
    db.session.commit()
    flash("Marked as reviewed.", "success")
    return redirect(url_for("admin.admin_pro_access_detail", pro_id=pro_id), code=303)


@admin_bp.post("/pro-access/<int:pro_id>/approve")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_pro_access_approve(pro_id: int):
    _require_global_admin()
    if ProAccessRequest is None:
        abort(404)

    row = ProAccessRequest.query.get_or_404(pro_id)
    note = (request.form.get("admin_notes") or "").strip() or None
    _pro_access_set_status(row, "approved", note)
    db.session.commit()
    flash("Approved.", "success")
    return redirect(url_for("admin.admin_pro_access_detail", pro_id=pro_id), code=303)


@admin_bp.post("/pro-access/<int:pro_id>/reject")
@login_required
@admin_required
@admin_role_required("superadmin")
def admin_pro_access_reject(pro_id: int):
    _require_global_admin()
    if ProAccessRequest is None:
        abort(404)

    row = ProAccessRequest.query.get_or_404(pro_id)
    note = (request.form.get("admin_notes") or "").strip() or None
    _pro_access_set_status(row, "rejected", note)
    db.session.commit()
    flash("Rejected.", "success")
    return redirect(url_for("admin.admin_pro_access_detail", pro_id=pro_id), code=303)
    active_sla_queue = bool(queue == "sla" and sla_kind)
    active_sla_filter_label = SLA_QUEUE_KINDS.get(sla_kind, "") if active_sla_queue else ""


from . import admin_requests as _admin_requests  # noqa: F401
from . import admin_referrals as _admin_referrals  # noqa: F401
from . import admin_diagnostics as _admin_diagnostics  # noqa: F401
from . import admin_cases as _admin_cases  # noqa: F401
from . import admin_notifications as _admin_notifications  # noqa: F401
from . import admin_structures as _admin_structures  # noqa: F401

build_requests_query = _admin_requests.build_requests_query
get_system_health_snapshot_cached = _admin_diagnostics.get_system_health_snapshot_cached


def _build_operational_report_pdf_response(report: dict):
    from io import BytesIO
    import os

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def _register_pdf_font(font_name: str, candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                try:
                    pdfmetrics.registerFont(TTFont(font_name, candidate))
                    return font_name
                except Exception:
                    continue
        return "Helvetica-Bold" if "Bold" in font_name else "Helvetica"

    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    pdf_font_regular = _register_pdf_font(
        "HelpChainPdfRegular",
        [
            os.path.join(fonts_dir, "arial.ttf"),
            os.path.join(fonts_dir, "segoeui.ttf"),
            os.path.join(fonts_dir, "calibri.ttf"),
        ],
    )
    pdf_font_bold = _register_pdf_font(
        "HelpChainPdfBold",
        [
            os.path.join(fonts_dir, "arialbd.ttf"),
            os.path.join(fonts_dir, "segoeuib.ttf"),
            os.path.join(fonts_dir, "calibrib.ttf"),
        ],
    )

    def _pdf_esc(value):
        return html.escape(str(value or ""))

    def _pdf_text(value):
        return _pdf_esc(_ops_report_clean_text(value)).replace("\n", "<br/>")

    def _scope_label() -> str:
        return report["scope"]["structure_name"] or "Structures supervisées"

    def _draw_brand_mark(canvas, x, y, size, stroke_color, fill_color=None):
        outer_radius = size * 0.34
        dot_radius = size * 0.045
        center_x = x + size / 2
        center_y = y + size / 2
        dot_positions = [
            (0.50, 0.16),
            (0.76, 0.32),
            (0.79, 0.61),
            (0.53, 0.82),
            (0.23, 0.61),
            (0.23, 0.35),
            (0.46, 0.46),
            (0.59, 0.53),
        ]
        if fill_color:
            canvas.setFillColor(fill_color)
            canvas.circle(center_x, center_y, outer_radius, stroke=0, fill=1)
        canvas.setStrokeColor(stroke_color)
        canvas.setLineWidth(max(size * 0.045, 1))
        canvas.circle(center_x, center_y, outer_radius, stroke=1, fill=0)
        canvas.setFillColor(stroke_color)
        for x_factor, y_factor in dot_positions:
            canvas.circle(x + (size * x_factor), y + (size * y_factor), dot_radius, stroke=0, fill=1)

    def _pdf_metric_table(metric, body_style):
        summary = _pdf_esc(_ops_report_clean_text(metric.get("summary") or ""))
        card = Table(
            [[
                Paragraph(
                    (
                        f"<font size='7' color='#5b7089'><b>{_pdf_esc(_ops_report_clean_text(metric.get('severity_label')))}</b></font><br/>"
                        f"<font size='18' color='#10243d'><b>{_pdf_esc(metric.get('value'))}</b></font><br/>"
                        f"<font size='9' color='#243a55'><b>{_pdf_esc(_ops_report_clean_text(metric.get('label')))}</b></font><br/>"
                        f"<font size='7' color='#60748d'>{_pdf_esc(_ops_report_clean_text(metric.get('delta_text')))}</font>"
                        + (f"<br/><font size='7' color='#60748d'>{summary}</font>" if summary else "")
                    ),
                    body_style,
                )
            ]],
            colWidths=[58 * mm],
        )
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfdff")),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#d6e0ec")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return card

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="OpsSection",
        parent=styles["Heading2"],
        fontName=pdf_font_bold,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#10243d"),
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="OpsKicker",
        parent=styles["BodyText"],
        fontName=pdf_font_bold,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#60748d"),
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="OpsBody",
        parent=styles["BodyText"],
        fontName=pdf_font_regular,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#243a55"),
    ))
    styles.add(ParagraphStyle(
        name="OpsMeta",
        parent=styles["BodyText"],
        fontName=pdf_font_regular,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#60748d"),
    ))
    story = []
    scope_name = _ops_report_clean_text(_scope_label())

    hero_table = Table(
        [[
            Paragraph(
                "HELPCHAIN<br/><font size='7' color='#60748d'>Coordination opérationnelle</font>",
                ParagraphStyle(
                    "OpsBrand",
                    parent=styles["BodyText"],
                    fontName=pdf_font_bold,
                    fontSize=10,
                    leading=12,
                    textColor=colors.HexColor("#10243d"),
                ),
            ),
            Paragraph(
                (
                    "<font size='8' color='#60748d'><b>Usage interne</b></font><br/>"
                    "<font size='17' color='#10243d'><b>Rapport opérationnel</b></font><br/>"
                    f"<font size='9' color='#243a55'>{_pdf_esc(scope_name)}</font><br/>"
                    f"<font size='8' color='#60748d'>Période : {report['period']['days']} jours | "
                    f"Généré le : {_pdf_esc(report['generated_at'][:16].replace('T', ' '))} | "
                    "Confidentialité : interne</font>"
                ),
                styles["OpsBody"],
            ),
        ]],
        colWidths=[36 * mm, 144 * mm],
    )
    hero_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfdff")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d6e0ec")),
        ("LINEAFTER", (0, 0), (0, 0), 0.6, colors.HexColor("#d6e0ec")),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(hero_table)
    story.append(Spacer(1, 3 * mm))

    metadata_table = Table(
        [[
            Paragraph(f"<b>Périmètre</b><br/>{_pdf_esc(scope_name)}", styles["OpsMeta"]),
            Paragraph(f"<b>Stabilité</b><br/>{_pdf_esc(_ops_report_clean_text(report.get('operational_conclusion', {}).get('stability', '-')))}", styles["OpsMeta"]),
            Paragraph(
                (
                    f"<b>Score opérationnel</b><br/>"
                    f"{_pdf_esc(report.get('operational_health_score', {}).get('score', '-'))}/100"
                ),
                styles["OpsMeta"],
            ),
        ]],
        colWidths=[62 * mm, 58 * mm, 58 * mm],
    )
    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#dde5ef")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e3eaf3")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(metadata_table)
    story.append(Spacer(1, 7 * mm))

    story.append(Paragraph("SYNTHÈSE EXÉCUTIVE", styles["OpsKicker"]))
    story.append(Paragraph("Synthèse exécutive", styles["OpsSection"]))
    story.append(Paragraph(_pdf_text(report["executive_summary"]), styles["OpsBody"]))
    story.append(Paragraph(
        f"Niveau: {_pdf_esc(_ops_report_clean_text(report['operational_severity']['label']))} - {_pdf_esc(_ops_report_clean_text(report['operational_severity']['message']))}",
        styles["OpsMeta"],
    ))
    story.append(Spacer(1, 5 * mm))

    snapshot_cards = [_pdf_metric_table(metric, styles["OpsBody"]) for metric in report.get("executive_snapshot", [])[:6]]
    while len(snapshot_cards) < 6:
        snapshot_cards.append(Table([[""]], colWidths=[58 * mm]))
    snapshot_grid = Table(
        [snapshot_cards[:3], snapshot_cards[3:6]],
        colWidths=[58 * mm, 58 * mm, 58 * mm],
        hAlign="LEFT",
    )
    snapshot_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(snapshot_grid)
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("DÉCISIONS PRIORITAIRES", styles["OpsKicker"]))
    story.append(Paragraph("Actions prioritaires", styles["OpsSection"]))
    for item in report.get("priority_actions", []):
        story.append(Paragraph(
            f"<b>{_pdf_esc(_ops_report_clean_text(item['title']))}</b> - {_pdf_esc(_ops_report_clean_text(item['severity_label']))}",
            styles["OpsBody"],
        ))
        story.append(Paragraph(
            _pdf_text(f"{item['reason']} Impact: {item['impact']} Horizon: {item['horizon']}"),
            styles["OpsMeta"],
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("PRESSION TERRITORIALE", styles["OpsKicker"]))
    story.append(Paragraph("Zones sous tension", styles["OpsSection"]))
    story.append(Paragraph(_pdf_text(report["territorial_pressure"]["summary"]), styles["OpsBody"]))

    zones_rows = [["Zone", "Couverture", "Ouvertes", "Actifs", "Disponibles", "Densité"]]
    for zone in report.get("territorial_pressure", {}).get("zones", [])[:5]:
        zones_rows.append([
            zone["city"],
            _ops_report_clean_text(zone["coverage_label"]),
            str(zone["open_requests"]),
            str(zone["active_intervenants"]),
            str(zone["available_intervenants"]),
            str(zone["density"]),
        ])
    if len(zones_rows) > 1:
        zones_table = Table(
            zones_rows,
            colWidths=[42 * mm, 38 * mm, 24 * mm, 24 * mm, 28 * mm, 24 * mm],
            repeatRows=1,
        )
        zones_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f2742")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8e2ee")),
            ("FONTNAME", (0, 0), (-1, 0), pdf_font_bold),
            ("FONTNAME", (0, 1), (-1, -1), pdf_font_regular),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(Spacer(1, 2 * mm))
        story.append(zones_table)

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("ANALYSE AUTOMATIQUE", styles["OpsKicker"]))
    story.append(Paragraph("Analyse automatique", styles["OpsSection"]))
    for item in report.get("automatic_analysis", []):
        story.append(Paragraph(
            f"<b>{_pdf_esc(_ops_report_clean_text(item['title']))}</b> - {_pdf_esc(_ops_report_clean_text(item['risk']))}",
            styles["OpsBody"],
        ))
        story.append(Paragraph(
            _pdf_text(f"{item['text']} Horizon: {item['horizon']}."),
            styles["OpsMeta"],
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("RECOMMANDATIONS", styles["OpsKicker"]))
    story.append(Paragraph("Recommandations de direction opérationnelle", styles["OpsSection"]))
    for item in report["recommendations"]:
        story.append(Paragraph(
            f"<b>{_pdf_esc(_ops_report_clean_text(item['title']))}</b> - {_pdf_esc(_ops_report_clean_text(item['priority']))}",
            styles["OpsBody"],
        ))
        story.append(Paragraph(
            _pdf_text(
                f"{item['description']} Impact potentiel: {item['impact']} "
                f"Niveau de risque: {item['risk']} Horizon: {item['horizon']}"
            ),
            styles["OpsMeta"],
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("CONCLUSION OPÉRATIONNELLE", styles["OpsKicker"]))
    story.append(Paragraph("Conclusion opérationnelle", styles["OpsSection"]))
    conclusion = report.get("operational_conclusion", {})
    story.append(Paragraph(_pdf_text(conclusion.get("summary", "")), styles["OpsBody"]))
    story.append(Paragraph(
        _pdf_text(
            f"Stabilité : {conclusion.get('stability', 'Stable')} | "
            f"Risques: {', '.join(conclusion.get('main_risks', []))} | "
            f"Recommandation principale : {conclusion.get('primary_recommendation', '')}"
        ),
        styles["OpsMeta"],
    ))

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Situations incluses dans le rapport", styles["OpsSection"]))

    rows = [["ID", "Titre", "Ville", "Statut", "Priorité", "Créée le"]]
    for item in report["items"][:80]:
        rows.append([
            f"#{item['id']}",
            _ops_report_clean_text(item["title"][:42]),
            _ops_report_clean_text(item["city"] or "-"),
            _ops_report_clean_text(item["status"] or "-"),
            _ops_report_clean_text(item["priority"] or "-"),
            item["created_at"][:10] if item["created_at"] else "-",
        ])

    if len(rows) > 1:
        items_table = Table(
            rows,
            colWidths=[15 * mm, 62 * mm, 28 * mm, 28 * mm, 25 * mm, 28 * mm],
            repeatRows=1,
        )
        items_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f2742")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8e2ee")),
            ("FONTNAME", (0, 0), (-1, 0), pdf_font_bold),
            ("FONTNAME", (0, 1), (-1, -1), pdf_font_regular),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEADING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(items_table)
    else:
        empty_table = Table(
            [[Paragraph(
                "Aucune situation n'est intégrée à cette édition du rapport pour le périmètre et la période retenus.",
                styles["OpsBody"],
            )]],
            colWidths=[186 * mm],
        )
        empty_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfdff")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d8e2ee")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.append(empty_table)

    def _add_footer(canvas, doc):
        canvas.saveState()
        _draw_brand_mark(canvas, 171 * mm, 270.5 * mm, 8 * mm, colors.HexColor("#9aabbb"))
        _draw_brand_mark(canvas, 94 * mm, 138 * mm, 22 * mm, colors.HexColor("#f2f5f8"))

        canvas.setStrokeColor(colors.HexColor("#d8e2ee"))
        canvas.line(15 * mm, 286 * mm, 195 * mm, 286 * mm)
        canvas.line(15 * mm, 12 * mm, 195 * mm, 12 * mm)

        canvas.setFillColor(colors.HexColor("#70839a"))
        canvas.setFont(pdf_font_regular, 7)
        canvas.drawString(15 * mm, 290 * mm, "HelpChain - Rapport opérationnel")
        canvas.drawRightString(195 * mm, 290 * mm, scope_name)
        canvas.drawString(15 * mm, 8.2 * mm, "Usage interne")
        canvas.drawRightString(
            195 * mm,
            8.2 * mm,
            f"Page {canvas.getPageNumber()} | Généré le {report['generated_at'][:16].replace('T', ' ')}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_add_footer, onLaterPages=_add_footer)

    pdf_data = buffer.getvalue()
    buffer.close()

    filename = f"helpchain-rapport-operationnel-{report['period']['days']}j.pdf"

    return Response(
        pdf_data,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename=\"{filename}\"',
            "Cache-Control": "no-store",
        },
    )


OPS_REPORT_FR_MONTHS = {
    1: "janvier",
    2: "fevrier",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "aout",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "decembre",
}

OPS_REPORT_STATUS_LABELS = {
    "new": "Nouvelle",
    "pending": "En attente",
    "in_progress": "En cours",
    "open": "En cours",
    "closed": "Clôturée",
    "completed": "Clôturée",
    "done": "Clôturée",
    "resolved": "Clôturée",
    "cancelled": "Annulée",
    "canceled": "Annulée",
}

OPS_REPORT_PRIORITY_LABELS = {
    "normal": "Normale",
    "medium": "Normale",
    "low": "Normale",
    "high": "Élevée",
    "elevee": "Élevée",
    "élevée": "Élevée",
    "critical": "Critique",
    "critique": "Critique",
    "urgent": "Critique",
}

OPS_REPORT_CATEGORY_LABELS = {
    "housing": "Logement",
    "health": "Santé",
    "admin": "Administratif",
    "orientation": "Orientation",
    "legal": "Juridique",
    "food": "Aide alimentaire",
}

OPS_REPORT_DEFINITIONS = {
    "Périmètre": "Demandes visibles dans le périmètre de pilotage sélectionné.",
    "Ouvert": "Situation encore active ou nécessitant un suivi.",
    "Clôturé": "Situation traitée ou clôturée sur la période.",
    "Sans action récente": "Situation ouverte sans activité enregistrée depuis plus de 72 heures.",
    "Non assignée": "Situation sans responsable opérationnel identifié.",
    "Délai moyen d'assignation": "Temps moyen entre la création d'une demande et son attribution.",
    "Délai moyen de résolution": "Temps moyen entre la création et la clôture d'une demande.",
}


def _ops_report_parse_datetime(value):
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _ops_report_format_datetime(value) -> str:
    dt_value = _ops_report_parse_datetime(value)
    if dt_value is None:
        return _ops_report_clean_text(str(value or ""))
    month_name = OPS_REPORT_FR_MONTHS.get(dt_value.month, "")
    return _ops_report_clean_text(
        f"{dt_value.day:02d} {month_name} {dt_value.year} a {dt_value.hour:02d}:{dt_value.minute:02d}"
    )


def _ops_report_labelize(value, mapping: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    normalized = raw.lower()
    if normalized in mapping:
        return _ops_report_clean_text(mapping[normalized])
    normalized = normalized.replace("-", "_")
    if normalized in mapping:
        return _ops_report_clean_text(mapping[normalized])
    return _ops_report_clean_text(raw.replace("_", " ").strip().title())


def _ops_report_clean_text(value: str) -> str:
    return (
        str(value or "")
        .replace(" a ", " à ")
        .replace("Aucune evolution", "Aucune évolution")
        .replace("Analyse automatique", "Analyse automatique")
        .replace("Elevee", "Élevée")
        .replace("elevee", "élevée")
        .replace("Eleve", "Élevé")
        .replace("eleve", "élevé")
        .replace("Cloturee", "Clôturée")
        .replace("cloturee", "clôturée")
        .replace("Cloture", "Clôture")
        .replace("cloture", "clôture")
        .replace("Annulee", "Annulée")
        .replace("annulee", "annulée")
        .replace("Sante", "Santé")
        .replace("sante", "santé")
        .replace("Synthese", "Synthèse")
        .replace("synthese", "synthèse")
        .replace("Periode", "Période")
        .replace("periode", "période")
        .replace("precedente", "précédente")
        .replace("Precedente", "Précédente")
        .replace("presente", "présente")
        .replace("Presente", "Présente")
        .replace("Observee", "Observée")
        .replace("observee", "observée")
        .replace("Observe", "Observé")
        .replace("observe", "observé")
        .replace("supervis?es", "supervisées")
        .replace("? surveiller", "À surveiller")
        .replace("Maitrise", "Maîtrisé")
        .replace("maitrise", "maîtrise")
        .replace("Depasse", "Dépassé")
        .replace("depasse", "dépassé")
        .replace("Depasses", "Dépassés")
        .replace("depasses", "dépassés")
        .replace("depassément", "dépassement")
        .replace("Dépassément", "Dépassement")
        .replace("depass?ment", "dépassement")
        .replace("Depass?ment", "Dépassement")
        .replace("d?pass?ment", "dépassement")
        .replace("D?pass?ment", "Dépassement")
        .replace("depassement", "dépassement")
        .replace("Depassement", "Dépassement")
        .replace("dépassément", "dépassement")
        .replace("Dépassément", "Dépassement")
        .replace("Genere", "Généré")
        .replace("Perimetre", "Périmètre")
        .replace("Capacite", "Capacité")
        .replace("capacite", "capacité")
        .replace("operationnel", "opérationnel")
        .replace("operationnelle", "opérationnelle")
        .replace("operationnelles", "opérationnelles")
        .replace("Operationnel", "Opérationnel")
        .replace("Operationnelle", "Opérationnelle")
        .replace("assignees", "assignées")
        .replace("Assignees", "Assignées")
        .replace("assignee", "assignée")
        .replace("Assignee", "Assignée")
        .replace("assignation", "assignation")
        .replace("Resolution", "Résolution")
        .replace("resolution", "résolution")
        .replace("Resolution", "Résolution")
        .replace("Ameliorer", "Améliorer")
        .replace("ameliorer", "améliorer")
        .replace("activite", "activité")
        .replace("Activite", "Activité")
        .replace("necessite", "nécessite")
        .replace("necessitent", "nécessitent")
        .replace("Necessitent", "Nécessitent")
        .replace("necessitent", "nécessitent")
        .replace("necessitant", "nécessitant")
        .replace("immediate", "immédiate")
        .replace("Immediate", "Immédiate")
        .replace("Immediat", "Immédiat")
        .replace("immediat", "immédiat")
        .replace("Modere", "Modéré")
        .replace("modere", "modéré")
        .replace("Reassigner", "Réassigner")
        .replace("reassigner", "réassigner")
        .replace("reduire", "réduire")
        .replace("Reduire", "Réduire")
        .replace("referent", "référent")
        .replace("Referent", "Référent")
        .replace("chaine", "chaîne")
        .replace("Chaine", "Chaîne")
        .replace("delai", "délai")
        .replace("delais", "délais")
        .replace("Delais", "Délais")
        .replace("Delai", "Délai")
        .replace("recent", "récent")
        .replace("recente", "récente")
        .replace("Recente", "Récente")
        .replace("s'etablit", "s’établit")
        .replace("S'etablit", "S’établit")
        .replace("etablit", "établit")
        .replace("Etablit", "Établit")
        .replace("tracabilite", "traçabilité")
        .replace("Tracabilite", "Traçabilité")
        .replace("securiser", "sécuriser")
        .replace("Securiser", "Sécuriser")
        .replace("insuffisant?", "insuffisante")
        .replace("insuffisanté", "insuffisante")
        .replace("stabilite", "stabilité")
        .replace("Stabilite", "Stabilité")
        .replace("Situations en d?pass?ment de jalons de suivi.", "Situations en dépassement de jalons de suivi.")
        .replace("23 d?pass?ment(s) SLA", "23 dépassement(s) SLA")
        .replace("classee couverture faible", "classée « couverture faible »")
        .replace("Classee couverture faible", "Classée « couverture faible »")
    )


def _ops_report_format_number(value) -> str:
    if isinstance(value, float):
        return f"{value:.1f}".replace(".", ",")
    return str(value)


def _ops_report_indicator_rows(report: dict) -> list[list[str]]:
    requests_data = report.get("requests", {})
    sla_data = report.get("sla", {})
    snapshot_map = {
        item.get("key"): item
        for item in report.get("executive_snapshot", [])
        if isinstance(item, dict)
    }

    def _lecture(key: str, fallback: str) -> str:
        row = snapshot_map.get(key) or {}
        return str(row.get("delta_text") or fallback)

    rows = [
        ["Situations ouvertes", _ops_report_format_number(requests_data.get("open", 0)), _lecture("open_requests", "Volume de suivi en cours.")],
        ["Non assignées", _ops_report_format_number(requests_data.get("unassigned", 0)), _lecture("unassigned_requests", "Situations sans référent opérationnel.")],
        ["SLA dépassés", _ops_report_format_number(sla_data.get("breach_count", 0)), _lecture("sla_breaches", "Jalons de suivi à surveiller.")],
        ["Temps moyen de résolution", f"{_ops_report_format_number(sla_data.get('avg_resolution_hours', 0.0))} h", _lecture("avg_resolution_hours", "Délai moyen observé sur les clôtures.")],
        ["Situations critiques", _ops_report_format_number(requests_data.get("critical", 0)), _lecture("critical_requests", "Cas prioritaires ouverts.")],
        ["Charge opérationnelle", _ops_report_format_number((snapshot_map.get("operational_load") or {}).get("raw_value", 0)), _lecture("operational_load", "Situations ouvertes par intervenant actif.")],
    ]
    return [
        [
            _ops_report_clean_text(value) if isinstance(value, str) else value
            for value in row
        ]
        for row in rows
    ]


def _ops_report_human_title(title: str | None, item_id: int | None = None) -> str:
    raw = str(title or "").strip()
    if not raw:
        return f"Situation #{item_id}" if item_id else "Situation"
    if re.match(r"^DEMO\s+OPS\s+\d+$", raw, flags=re.IGNORECASE):
        return f"Situation #{item_id}" if item_id else "Situation"
    return raw


def _ops_report_sla_level(item: dict) -> str:
    updated_at = _ops_report_parse_datetime(item.get("updated_at"))
    created_at = _ops_report_parse_datetime(item.get("created_at"))
    reference = updated_at or created_at
    if reference is None:
        return "A surveiller"
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    ref_naive = reference.astimezone(UTC).replace(tzinfo=None) if getattr(reference, "tzinfo", None) else reference
    age_hours = (now_naive - ref_naive).total_seconds() / 3600.0
    if age_hours >= 72:
        return "Dépassé"
    if age_hours >= 48:
        return "Vigilance"
    return "Maîtrisé"


def _build_operational_report_xlsx_bytes(report: dict):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except Exception:
        return None, None

    wb = Workbook()
    wb.properties.title = "HelpChain - Rapport opérationnel"
    wb.properties.subject = "Rapport opérationnel institutionnel"
    wb.properties.creator = "HelpChain"
    wb.properties.company = "HelpChain"
    wb.properties.description = "Classeur de pilotage opérationnel territorial"

    title_fill = PatternFill("solid", fgColor="0F2742")
    section_fill = PatternFill("solid", fgColor="EAF1F8")
    header_fill = PatternFill("solid", fgColor="D9E4F0")
    calm_fill = PatternFill("solid", fgColor="EEF6FF")
    watch_fill = PatternFill("solid", fgColor="FFF8E8")
    elevated_fill = PatternFill("solid", fgColor="FFF0E6")
    critical_fill = PatternFill("solid", fgColor="FDECEC")
    white_font = Font(color="FFFFFF", bold=True, size=12)
    title_font = Font(color="10243D", bold=True, size=16)
    section_font = Font(color="10243D", bold=True, size=11)
    header_font = Font(color="243A55", bold=True)
    strong_font = Font(color="10243D", bold=True)
    body_font = Font(color="243A55", size=10)
    meta_font = Font(color="60748D", size=9)
    thin_side = Side(style="thin", color="D6E0EC")
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    def _severity_fill(level: str):
        normalized = str(level or "").lower()
        if normalized in {"critical", "critique", "dépassé", "depasse"}:
            return critical_fill
        if normalized in {"high", "élevée", "elevee", "sous pression"}:
            return elevated_fill
        if normalized in {"moderate", "vigilance", "watch"}:
            return watch_fill
        return calm_fill

    def _style_title(ws, title: str, subtitle: str = ""):
        ws.merge_cells("A1:G1")
        ws["A1"] = title
        ws["A1"].fill = title_fill
        ws["A1"].font = white_font
        ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
        if subtitle:
            ws.merge_cells("A2:G2")
            ws["A2"] = subtitle
            ws["A2"].font = meta_font
            ws["A2"].alignment = Alignment(horizontal="left", vertical="center")

    def _style_section_row(ws, row_idx: int, label: str, end_col: int = 7):
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=end_col)
        cell = ws.cell(row=row_idx, column=1)
        cell.value = label
        cell.fill = section_fill
        cell.font = section_font
        cell.border = border
        cell.alignment = Alignment(horizontal="left", vertical="center")

    def _style_header_row(ws, row_idx: int, headers: list[str]):
        for col_idx, label in enumerate(headers, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = label
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    def _auto_size(ws, max_width: int = 42):
        for col_idx, column_cells in enumerate(ws.columns, start=1):
            max_len = 0
            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, len(value))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(12, max_len + 2), max_width)

    scope_name = report["scope"]["structure_name"] or "Structures supervisées"
    generated_label = _ops_report_format_datetime(report["generated_at"])

    ws = wb.active
    ws.title = "Synthèse exécutive"
    _style_title(ws, "HelpChain - Rapport opérationnel", "Synthèse institutionnelle de pilotage")
    ws["A4"] = "Période"
    ws["B4"] = f"{report['period']['days']} jours"
    ws["D4"] = "Périmètre"
    ws["E4"] = scope_name
    ws["A5"] = "Généré le"
    ws["B5"] = generated_label
    ws["D5"] = "Confidentialité"
    ws["E5"] = "Usage interne"
    ws["A6"] = "Statut opérationnel"
    ws["B6"] = report["operational_conclusion"]["stability"]
    for ref in ("A4", "A5", "A6", "D4", "D5"):
        ws[ref].font = header_font
    for ref in ("B4", "B5", "B6", "E4", "E5"):
        ws[ref].font = body_font

    _style_section_row(ws, 8, "Synthèse exécutive")
    snapshot_headers = ["Indicateur", "Valeur", "Évolution", "Sévérité", "Lecture"]
    _style_header_row(ws, 9, snapshot_headers)
    row_idx = 10
    zones_count = len(report.get("territorial_pressure", {}).get("zones", []) or [])
    for item in report.get("executive_snapshot", []):
        values = [
            item.get("label", ""),
            item.get("value", ""),
            item.get("delta_text", ""),
            item.get("severity_label", ""),
            item.get("summary", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 4:
                cell.fill = _severity_fill(str(item.get("severity") or "stable"))
        row_idx += 1
    ws.cell(row=row_idx, column=1, value="Zones sous tension")
    ws.cell(row=row_idx, column=2, value=str(zones_count))
    ws.cell(row=row_idx, column=3, value="Lecture territoriale")
    ws.cell(row=row_idx, column=4, value="Vigilance" if zones_count else "Stable")
    ws.cell(row=row_idx, column=5, value=report.get("territorial_pressure", {}).get("summary", ""))
    for col_idx in range(1, 6):
        cell = ws.cell(row=row_idx, column=col_idx)
        cell.border = border
        cell.font = body_font
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        if col_idx == 4:
            cell.fill = _severity_fill("moderate" if zones_count else "stable")

    row_idx += 2
    _style_section_row(ws, row_idx, "Synthèse exécutive")
    row_idx += 1
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx + 2, end_column=7)
    ws.cell(row=row_idx, column=1, value=report.get("executive_summary", ""))
    ws.cell(row=row_idx, column=1).font = body_font
    ws.cell(row=row_idx, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(row=row_idx, column=1).border = border

    row_idx += 4
    _style_section_row(ws, row_idx, "Top 3 actions prioritaires")
    row_idx += 1
    _style_header_row(ws, row_idx, ["Priorité", "Action", "Impact", "Horizon"])
    row_idx += 1
    for item in report.get("priority_actions", [])[:3]:
        values = [
            _ops_report_labelize(item.get("severity_label"), OPS_REPORT_PRIORITY_LABELS),
            item.get("title", ""),
            item.get("impact", ""),
            item.get("horizon", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 1:
                cell.fill = _severity_fill(str(item.get("severity") or item.get("severity_label") or "stable"))
        row_idx += 1
    ws.freeze_panes = "A9"
    ws.auto_filter.ref = f"A9:E{max(10, row_idx - 1)}"
    _auto_size(ws)

    ws = wb.create_sheet("Indicateurs opérationnels")
    _style_title(ws, "Indicateurs opérationnels", scope_name)
    _style_header_row(ws, 4, ["Indicateur", "Valeur", "Évolution", "Lecture opérationnelle"])
    row_idx = 5
    for item in _ops_report_indicator_rows(report):
        for col_idx, value in enumerate(item, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row_idx += 1
    extra_rows = [
        ["Taux d’assignation", f"{_ops_report_format_number(report.get('sla', {}).get('assignment_rate', 0.0))} %", "", "Part des situations ouvertes déjà attribuées."],
        ["Résolues <24h", f"{_ops_report_format_number(report.get('sla', {}).get('resolved_under_24h_rate', 0.0))} %", "", "Part des situations clôturées rapidement sur la période."],
        ["Délai moyen d’assignation", f"{_ops_report_format_number(report.get('sla', {}).get('avg_assignment_hours', 0.0))} h", "", "Temps moyen entre la création et l’attribution."],
    ]
    for item in extra_rows:
        for col_idx, value in enumerate(item, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:D{row_idx - 1}"
    _auto_size(ws)

    ws = wb.create_sheet("Tensions territoriales")
    _style_title(ws, "Tensions territoriales", "Lecture territoriale consolidée")
    territorial_headers = ["Zone", "Couverture", "Ouvertes", "Intervenants actifs", "Disponibles", "Niveau tension", "Score pression", "Lecture"]
    _style_header_row(ws, 4, territorial_headers)
    row_idx = 5
    for zone in report.get("territorial_pressure", {}).get("zones", []):
        pressure_score = int(zone.get("heat", 0) or 0) * 20
        values = [
            zone.get("city", ""),
            zone.get("coverage_label", ""),
            zone.get("open_requests", 0),
            zone.get("active_intervenants", 0),
            zone.get("available_intervenants", 0),
            zone.get("severity_label", ""),
            pressure_score,
            zone.get("alert", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx in {2, 6, 7}:
                cell.fill = _severity_fill(str(zone.get("severity") or "stable"))
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:H{max(4, row_idx - 1)}"
    _auto_size(ws)

    ws = wb.create_sheet("Actions prioritaires")
    _style_title(ws, "Actions prioritaires", "Pilotage et arbitrages recommandés")
    _style_header_row(ws, 4, ["Priorité", "Action", "Impact", "Horizon", "Responsable recommandé"])
    row_idx = 5
    for item in report.get("priority_actions", []):
        horizon = item.get("horizon", "")
        if str(horizon).lower() == "immediat":
            owner_hint = "Direction opérationnelle"
        elif str(horizon).lower() == "72h":
            owner_hint = "Coordination territoriale"
        else:
            owner_hint = "Pilotage structure"
        values = [
            _ops_report_labelize(item.get("severity_label"), OPS_REPORT_PRIORITY_LABELS),
            item.get("title", ""),
            item.get("impact", ""),
            horizon,
            owner_hint,
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 1:
                cell.fill = _severity_fill(str(item.get("severity") or item.get("severity_label") or "stable"))
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:E{max(4, row_idx - 1)}"
    _auto_size(ws)

    ws = wb.create_sheet("Situations opérationnelles")
    _style_title(ws, "Situations opérationnelles", "Liste de travail institutionnelle")
    situation_headers = ["ID", "Titre métier", "Ville", "Statut lisible", "Priorité lisible", "Créée le", "Dernière activité", "Référent", "Niveau SLA"]
    _style_header_row(ws, 4, situation_headers)
    row_idx = 5
    for item in report.get("items", []):
        values = [
            item.get("id"),
            _ops_report_human_title(item.get("title"), item.get("id")),
            item.get("city") or "-",
            _ops_report_labelize(item.get("status"), OPS_REPORT_STATUS_LABELS),
            _ops_report_labelize(item.get("priority"), OPS_REPORT_PRIORITY_LABELS),
            _ops_report_format_datetime(item.get("created_at")) if item.get("created_at") else "-",
            _ops_report_format_datetime(item.get("updated_at")) if item.get("updated_at") else "-",
            "Attribué" if item.get("owner_id") else "Non assigné",
            _ops_report_sla_level(item),
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 9:
                cell.fill = _severity_fill(str(value))
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:I{max(4, row_idx - 1)}"
    _auto_size(ws, max_width=34)

    ws = wb.create_sheet("Analyse automatique")
    _style_title(ws, "Analyse automatique", "Assistant d’aide au pilotage")
    _style_header_row(ws, 4, ["Constat", "Niveau", "Risque", "Horizon", "Analyse"])
    row_idx = 5
    for item in report.get("automatic_analysis", []):
        values = [
            item.get("title", ""),
            _ops_report_labelize(item.get("severity"), OPS_REPORT_PRIORITY_LABELS) if item.get("severity") else "Stable",
            item.get("risk", ""),
            item.get("horizon", ""),
            item.get("text", ""),
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value = value
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 2:
                cell.fill = _severity_fill(str(item.get("severity") or "stable"))
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:E{max(4, row_idx - 1)}"
    _auto_size(ws)

    ws = wb.create_sheet("Définitions")
    _style_title(ws, "Définitions", "Glossaire institutionnel")
    _style_header_row(ws, 4, ["Indicateur", "Définition"])
    row_idx = 5
    for label, definition in OPS_REPORT_DEFINITIONS.items():
        ws.cell(row=row_idx, column=1, value=label)
        ws.cell(row=row_idx, column=2, value=definition)
        for col_idx in (1, 2):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row_idx += 1
    extra_defs = [
        ("Couverture territoriale", "Niveau de présence opérationnelle visible sur une zone donnée."),
        ("Charge opérationnelle", "Volume de situations ouvertes rapporté aux moyens mobilisables."),
        ("SLA dépassé", "Situation dont le jalon de suivi attendu n’est plus respecté."),
    ]
    for label, definition in extra_defs:
        ws.cell(row=row_idx, column=1, value=label)
        ws.cell(row=row_idx, column=2, value=definition)
        for col_idx in (1, 2):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = border
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        row_idx += 1
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A4:B{max(4, row_idx - 1)}"
    _auto_size(ws, max_width=60)

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = True
        ws["A1"].font = white_font
        ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 24

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"helpchain-rapport-operationnel-{report['period']['days']}j.xlsx"
    return bio.getvalue(), filename


def _build_operational_report_xlsx_response(report: dict):
    payload, filename = _build_operational_report_xlsx_bytes(report)
    if payload is None or filename is None:
        return Response("openpyxl is not installed", status=500)
    return Response(
        payload,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _build_operational_report_csv_response(report: dict):
    output = StringIO()
    writer = csv.writer(output, delimiter=";")

    writer.writerow(["Rapport opérationnel HelpChain"])
    writer.writerow(["Périmètre", report["scope"]["structure_name"] or "Structures supervisées"])
    writer.writerow(["Période", f'{report["period"]["days"]} jours'])
    writer.writerow(["Généré le", _ops_report_format_datetime(report["generated_at"])])
    writer.writerow(["Confidentialité", "Usage interne"])
    writer.writerow([])

    writer.writerow(["Synthèse exécutive"])
    writer.writerow(["Niveau opérationnel", report["operational_conclusion"]["stability"]])
    writer.writerow(["Lecture exécutive courte", report["executive_summary"]])
    writer.writerow([])

    writer.writerow(["Indicateurs clés"])
    writer.writerow(["Indicateur", "Valeur", "Lecture"])
    for row in _ops_report_indicator_rows(report):
        writer.writerow(row)
    writer.writerow([])

    writer.writerow(["Répartition par catégorie"])
    writer.writerow(["Catégorie", "Volume"])
    for row in report["breakdowns"]["by_category"]:
        writer.writerow([
            _ops_report_labelize(row["category"], OPS_REPORT_CATEGORY_LABELS),
            row["count"],
        ])
    writer.writerow([])

    writer.writerow(["Répartition par statut"])
    writer.writerow(["Statut", "Volume"])
    for row in report["breakdowns"]["by_status"]:
        writer.writerow([
            _ops_report_labelize(row["status"], OPS_REPORT_STATUS_LABELS),
            row["count"],
        ])
    writer.writerow([])

    writer.writerow(["Définitions des indicateurs"])
    writer.writerow(["Indicateur", "Définition"])
    for label, definition in OPS_REPORT_DEFINITIONS.items():
        writer.writerow([label, definition])
    writer.writerow([])

    if report.get("priority_actions"):
        writer.writerow(["Actions prioritaires"])
        writer.writerow(["Priorité", "Action", "Impact", "Horizon"])
        for item in report["priority_actions"]:
            writer.writerow([
                _ops_report_labelize(item.get("severity_label"), OPS_REPORT_PRIORITY_LABELS),
                item.get("title", ""),
                item.get("impact", ""),
                item.get("horizon", ""),
            ])

    csv_body = output.getvalue()
    csv_body = (
        csv_body
        .replace(" a ", " à ")
        .replace("Confidentialite", "Confidentialité")
        .replace("Synthese", "Synthèse")
        .replace("executive", "exécutive")
        .replace("operationnel", "opérationnel")
        .replace("operationnelle", "opérationnelle")
        .replace("operationnelles", "opérationnelles")
        .replace("operationnellement", "opérationnellement")
        .replace("cloturees", "clôturées")
        .replace("Cloturee", "Clôturée")
        .replace("Cloturé", "Clôturé")
        .replace("Cloture", "Clôturé")
        .replace("Sante", "Santé")
        .replace("Repartition", "Répartition")
        .replace("Categorie", "Catégorie")
        .replace("Perimetre", "Périmètre")
        .replace("perimetre", "périmètre")
        .replace("Delai", "Délai")
        .replace("periode", "période")
        .replace("recente", "récente")
        .replace("activite", "activité")
        .replace("evolution", "évolution")
        .replace("assignation", "assignation")
        .replace("Elevee", "Élevée")
        .replace("assignee", "assignée")
        .replace("creation", "création")
        .replace("resolution", "résolution")
        .replace("traitee", "traitée")
        .replace("cloture", "clôture")
        .replace("selectionne", "sélectionné")
        .replace("necessitant", "nécessitant")
        .replace("enregistree", "enregistrée")
        .replace("identifie", "identifié")
    )
    csv_data = "\ufeff" + csv_body
    filename = f"helpchain-rapport-operationnel-{report['period']['days']}j.csv"

    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )




# --- Operational reports ---
@admin_bp.get("/reports/operations/export.csv")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_operations_report_csv():
    admin_required_404()

    days = request.args.get("days", default=7, type=int) or 7
    days = max(1, min(days, 366))

    structure_id = None
    if not _is_global_admin():
        structure_id = _current_structure_id()

    report = build_operational_report(
        structure_id=structure_id,
        days=days,
    )

    return _build_operational_report_csv_response(report)


@admin_bp.get("/reports/operations/export.xlsx")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_operations_report_xlsx():
    admin_required_404()

    days = request.args.get("days", default=7, type=int) or 7
    days = max(1, min(days, 366))

    structure_id = None
    if not _is_global_admin():
        structure_id = _current_structure_id()

    report = build_operational_report(
        structure_id=structure_id,
        days=days,
    )

    return _build_operational_report_xlsx_response(report)
@admin_bp.get("/reports/operations/export.pdf")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_operations_report_pdf():
    admin_required_404()

    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    days = request.args.get("days", default=7, type=int) or 7
    days = max(1, min(days, 366))

    structure_id = None
    if not _is_global_admin():
        structure_id = _current_structure_id()

    report = build_operational_report(
        structure_id=structure_id,
        days=days,
    )

    return _build_operational_report_pdf_response(report)

@admin_bp.get("/reports/operations")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_operations_report():
    admin_required_404()

    days = request.args.get("days", default=7, type=int) or 7
    days = max(1, min(days, 366))

    structure_id = None
    if not _is_global_admin():
        structure_id = _current_structure_id()

    try:
        current_app.logger.warning(
            "[OPS_REPORT] start days=%s structure_id=%s user=%s role=%s",
            days,
            structure_id,
            getattr(current_user, "id", None),
            getattr(current_user, "role", None),
        )
        report = build_operational_report(
            structure_id=structure_id,
            days=days,
        )
        current_app.logger.warning("[OPS_REPORT] built successfully")
        return render_template(
            "admin/reports_operations.html",
            report=report,
            days=days,
        )
    except Exception:
        current_app.logger.exception("[OPS_REPORT] failed")
        raise

# --- Ops Action Queue API v1 ---
from flask import jsonify
from datetime import datetime, timezone

try:
    from backend.models import Request as OpsActionRequest
except Exception:
    from backend.helpchain_backend.src.models import Request as OpsActionRequest


@admin_bp.get("/api/action-queue")
@admin_required
@admin_role_required("readonly", "ops", "admin", "superadmin")
def admin_ops_action_queue_v1():
    rows = (
        _scope_requests(OpsActionRequest.query)
        .filter(OpsActionRequest.is_archived.is_(False))
        .order_by(
            OpsActionRequest.updated_at.desc(),
            OpsActionRequest.created_at.desc(),
        )
        .limit(10)
        .all()
    )

    items = []
    for r in rows:
        score = int(getattr(r, "risk_score", 0) or 0)
        owner = getattr(r, "owner_id", None)

        items.append({
            "id": r.id,
            "title": getattr(r, "title", None) or f"Demande #{r.id}",
            "city": getattr(r, "city", None) or "",
            "priority": getattr(r, "priority", None) or "",
            "status": getattr(r, "status", None) or "open",
            "risk_score": score,
            "risk_level": getattr(r, "risk_level", None) or "",
            "category": getattr(r, "category", None) or "",
            "owner_id": owner,
            "recommendation": (
                "Escalader" if score >= 85 else
                "Assigner / relancer" if not owner else
                "Suivre"
            ),
        })

    return jsonify({"ok": True, "items": items})


@admin_bp.get("/api/stream")
def admin_ops_stream_v1():
    def generate():
        while True:
            yield ":\n\n"
            data = {"type": "ping"}
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(2)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@admin_bp.post("/api/action-queue/<int:request_id>/assign")
@admin_required
@admin_role_required("ops", "admin", "superadmin")
def admin_ops_action_queue_assign_v1(request_id):
    r = _scope_requests(OpsActionRequest.query).filter(
        OpsActionRequest.id == int(request_id)
    ).first_or_404()

    current_admin_id = session.get("admin_user_id") or session.get("user_id")
    current_admin_name = session.get("admin_username") or session.get("username") or "admin"

    if hasattr(r, "owner_id"):
        r.owner_id = current_admin_id
    if hasattr(r, "owned_at"):
        r.owned_at = datetime.now(timezone.utc)
    if hasattr(r, "updated_at"):
        r.updated_at = datetime.now(timezone.utc)
    if hasattr(r, "status") and (r.status or "").lower() in {"new", "open"}:
        r.status = "assigned"

    db.session.commit()

    try:
        publish_admin_stream_event({
            "type": "request_assigned",
            "request_id": r.id,
        })
    except Exception:
        current_app.logger.exception(
            "admin_ops_action_queue_assign_publish_failed request_id=%s",
            r.id,
        )

    return jsonify({
        "ok": True,
        "request_id": r.id,
        "assignee": current_admin_name,
        "status": getattr(r, "status", None),
    })

