from datetime import UTC, datetime, timedelta

from backend.helpchain_backend.src.services.founder_memory_engine import (
    build_founder_memory_timeline,
    detect_relationship_temperature,
    detect_stalled_opportunities,
    summarize_founder_memory,
)


def test_build_founder_memory_timeline_orders_events_chronologically():
    now = datetime.now(UTC).replace(tzinfo=None)
    timeline = build_founder_memory_timeline(
        {
            "created_at": now - timedelta(days=6),
            "last_activity": now - timedelta(days=1),
            "timeline_paths": ["/offre", "/deploiement"],
            "contacted_at": now - timedelta(days=3),
        },
        manual_notes=[
            {
                "timestamp": now - timedelta(days=2),
                "label": "founder note added",
                "note": "Pilot territory confirmed",
            }
        ],
        now=now,
    )

    labels = [event["label"] for event in timeline]

    assert labels[0] == "visited /offre"
    assert "viewed /deploiement" in labels
    assert "founder outreach sent" in labels
    assert "founder note added" in labels
    assert labels.index("founder outreach sent") < labels.index("founder note added")


def test_detect_stalled_opportunities_flags_delayed_high_intent_accounts():
    now = datetime.now(UTC).replace(tzinfo=None)
    stalled = detect_stalled_opportunities(
        [
            {
                "uid": "lead-1",
                "organization": "CCAS Paris",
                "territory": "Paris",
                "intent_score": 82,
                "contacted_at": now - timedelta(days=11),
                "timeline_events": [
                    {
                        "timestamp": (now - timedelta(days=11)).isoformat(),
                        "event_type": "founder_outreach_sent",
                        "label": "founder outreach sent",
                        "source": "outreach",
                    }
                ],
            }
        ],
        now=now,
    )

    assert len(stalled) == 1
    assert stalled[0]["organization"] == "CCAS Paris"
    assert stalled[0]["stalled_days"] >= 11


def test_relationship_temperature_transitions_are_stable():
    now = datetime.now(UTC).replace(tzinfo=None)

    assert detect_relationship_temperature({}, timeline=[], now=now) == "cold"
    assert (
        detect_relationship_temperature(
            {"intent_score": 48, "repeated_engagement_detected": True},
            timeline=[
                {
                    "timestamp": (now - timedelta(days=3)).isoformat(),
                    "event_type": "page_view",
                    "label": "viewed /deploiement",
                    "source": "telemetry",
                }
            ],
            now=now,
        )
        == "warming"
    )
    assert (
        detect_relationship_temperature(
            {"intent_score": 72},
            timeline=[
                {
                    "timestamp": (now - timedelta(days=4)).isoformat(),
                    "event_type": "founder_outreach_sent",
                    "label": "founder outreach sent",
                    "source": "outreach",
                },
                {
                    "timestamp": (now - timedelta(days=1)).isoformat(),
                    "event_type": "demo_submission",
                    "label": "submitted demo request",
                    "source": "submission",
                },
            ],
            now=now,
        )
        == "active"
    )
    assert (
        detect_relationship_temperature(
            {"intent_score": 88, "repeated_engagement_detected": True, "territory": "Paris"},
            timeline=[
                {
                    "timestamp": (now - timedelta(days=1)).isoformat(),
                    "event_type": "deployment_pilot_cta_clicked",
                    "label": "clicked deployment CTA",
                    "source": "telemetry",
                }
            ],
            now=now,
        )
        == "strategic"
    )


def test_summarize_founder_memory_tracks_last_founder_touch():
    now = datetime.now(UTC).replace(tzinfo=None)
    timeline = [
        {
            "timestamp": (now - timedelta(days=5)).isoformat(),
            "event_type": "page_view",
            "label": "visited /offre",
            "source": "telemetry",
        },
        {
            "timestamp": (now - timedelta(days=2)).isoformat(),
            "event_type": "founder_manual_note",
            "label": "founder note added",
            "source": "manual_note",
        },
    ]

    summary = summarize_founder_memory(timeline, row={"intent_score": 42}, now=now)

    assert summary["timeline_event_count"] == 2
    assert summary["last_timeline_event"] == "founder note added"
    assert summary["has_manual_note"] is True
    assert summary["days_since_founder_touch"] == 2
