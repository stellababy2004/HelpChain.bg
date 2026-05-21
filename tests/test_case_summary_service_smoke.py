from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.helpchain_backend.src.services.case_summary import build_case_summary

pytestmark = pytest.mark.spine


def test_case_summary_critical_no_owner():
    req = SimpleNamespace(
        title="Situation logement",
        description="Besoin d'hébergement",
        risk_level="critical",
        risk_signals='["logement","no_owner"]',
        owner_id=None,
    )
    summary = build_case_summary(req, {"recommended_action": "assign_immediately"})
    assert "Situation liée au logement." in summary
    assert "Aucun responsable n’est actuellement assigné." in summary
    assert "affectation rapide" in summary.lower()


def test_case_summary_not_seen_72h():
    req = SimpleNamespace(
        title="Suivi social",
        description="Dossier en attente",
        risk_level="standard",
        risk_signals='["not_seen_72h"]',
        owner_id=12,
    )
    summary = build_case_summary(req, {"recommended_action": "manager_review_today"})
    assert "72 heures" in summary
    assert "revue managériale" in summary.lower()


def test_case_summary_attention_follow_up():
    req = SimpleNamespace(
        title="Coordination territoriale",
        description="Suivi à planifier",
        risk_level="attention",
        risk_signals="[]",
        owner_id=5,
    )
    summary = build_case_summary(req, {"recommended_action": "routine_queue"})
    assert "suivi rapproché" in summary.lower()
    assert "suivi courant structuré" in summary.lower()
