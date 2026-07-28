"""
Phase-1 compatibility exports for routes importing `..models`.

We re-export the canonical monolith models from `backend.models` plus a few
split-out models that are not present there yet.
"""

from backend.extensions import db

# Other canonical/legacy names live in `backend.models`
from backend.models import (  # noqa: F401
    AdminAuditEvent,
    AdminLoginAttempt,
    Assignment,
    CaseReferral,
    Intervenant,
    IntervenantActivity,
    IntegrationConnector,
    OrganizationConnection,
    NotificationSubscription,
    NotificationJob,
    ReferralActivity,
    RelayEvent,
    Request,
    RequestActivity,
    RequestMetric,
    SecurityEvent,
    current_structure,
    default_referral_shared_scope,
    get_default_structure,
    User,
    canonical_role,
    utc_now,
    Structure,
    UiTranslation,
    UiTranslationEvent,
    UiLocaleLock,
    UiTranslationFreeze,
)

# Local wrappers for canonical/legacy models (defined in `backend.models`)
from .admin_user import AdminUser  # noqa: F401
from .email_send_event import EmailSendEvent  # noqa: F401
from .guardrail_counter import GuardrailCounter  # noqa: F401
from .magic_link_token import MagicLinkToken  # noqa: F401
from .notification import Notification  # noqa: F401
from .organization_access_request import OrganizationAccessRequest  # noqa: F401
from .import_batch import ImportBatch  # noqa: F401
from .metric_registry import MetricDefinition, MetricRun  # noqa: F401
from .professional_lead import ProfessionalLead  # noqa: F401
from .professional_lead_activity import ProfessionalLeadActivity  # noqa: F401
from .score_explanation import ScoreExplanation  # noqa: F401
from .case import Case  # noqa: F401
from .case_event import CaseEvent  # noqa: F401
from .case_participant import CaseParticipant  # noqa: F401
from .case_collaborator import CaseCollaborator  # noqa: F401

# Split models (not present in `backend.models`)
from .refresh_token import RefreshToken  # noqa: F401
from .request_log import RequestLog  # noqa: F401
from .volunteer import Volunteer  # noqa: F401
from .volunteer_action import VolunteerAction  # noqa: F401
from .volunteer_interest import VolunteerInterest  # noqa: F401
from .volunteer_match_feedback import VolunteerMatchFeedback  # noqa: F401
from .volunteer_request_state import VolunteerRequestState  # noqa: F401

__all__ = [
    "db",
    "utc_now",
    "canonical_role",
    "get_default_structure",
    "current_structure",
    "User",
    "Volunteer",
    "AdminUser",
    "AdminLoginAttempt",
    "AdminAuditEvent",
    "Assignment",
    "CaseReferral",
    "default_referral_shared_scope",
    "Intervenant",
    "IntervenantActivity",
    "IntegrationConnector",
    "OrganizationConnection",
    "Request",
    "Structure",
    "RequestLog",
    "RequestActivity",
    "RequestMetric",
    "SecurityEvent",
    "UiTranslation",
    "UiTranslationEvent",
    "UiLocaleLock",
    "UiTranslationFreeze",
    "Notification",
    "NotificationJob",
    "NotificationSubscription",
    "RelayEvent",
    "ReferralActivity",
    "OrganizationAccessRequest",
    "ImportBatch",
    "MetricDefinition",
    "MetricRun",
    "RefreshToken",
    "VolunteerInterest",
    "VolunteerAction",
    "MagicLinkToken",
    "ProfessionalLead",
    "ProfessionalLeadActivity",
    "ScoreExplanation",
    "Case",
    "CaseEvent",
    "CaseParticipant",
    "CaseCollaborator",
    "VolunteerRequestState",
    "VolunteerMatchFeedback",
    "EmailSendEvent",
    "GuardrailCounter",
]
