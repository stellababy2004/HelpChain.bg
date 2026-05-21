from backend.helpchain_backend.src.statuses import (
    canonical_request_status,
    is_terminal_request_status,
    request_status_read_values,
    request_terminal_status_read_values,
)


def test_canonical_request_status_maps_legacy_request_aliases():
    assert canonical_request_status("pending") == "open"
    assert canonical_request_status("approved") == "in_progress"
    assert canonical_request_status("completed") == "done"
    assert canonical_request_status("resolved") == "done"
    assert canonical_request_status("closed") == "done"
    assert canonical_request_status("rejected") == "cancelled"
    assert canonical_request_status("canceled") == "cancelled"


def test_canonical_request_status_resolves_active_by_context():
    assert canonical_request_status("active", active_as="open") == "open"
    assert canonical_request_status("active", active_as="in_progress") == "in_progress"


def test_canonical_request_status_preserves_unknown_values_and_supports_fallback():
    assert canonical_request_status("queued_review") == "queued_review"
    assert canonical_request_status("queued_review", fallback="open") == "open"
    assert canonical_request_status(None) == ""
    assert canonical_request_status(None, fallback="open") == "open"


def test_request_status_read_values_include_legacy_aliases():
    assert request_status_read_values("open", active_as="open") == ("open", "pending", "active")
    assert request_status_read_values("in_progress", active_as="in_progress") == (
        "in_progress",
        "approved",
        "active",
    )
    assert request_status_read_values("done") == ("done", "completed", "resolved", "closed")
    assert request_status_read_values("cancelled") == (
        "cancelled",
        "canceled",
        "rejected",
    )


def test_terminal_status_helpers_cover_compatibility_values():
    assert is_terminal_request_status("resolved")
    assert is_terminal_request_status("rejected")
    assert not is_terminal_request_status("approved")
    assert request_terminal_status_read_values() == (
        "done",
        "completed",
        "resolved",
        "closed",
        "cancelled",
        "canceled",
        "rejected",
    )
