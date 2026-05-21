# Status single source of truth

from flask_babel import lazy_gettext as _l

REQUEST_STATUS_META = {
    "open": {
        "label": _l("Open"),
        "icon": "bi-inbox",
        "badge_class": "badge bg-primary",
    },
    "in_progress": {
        "label": _l("In progress"),
        "icon": "bi-tools",
        "badge_class": "badge bg-warning text-dark",
    },
    "done": {
        "label": _l("Done"),
        "icon": "bi-check-circle",
        "badge_class": "badge bg-success",
    },
    "cancelled": {
        "label": _l("Cancelled"),
        "icon": "bi-x-circle",
        "badge_class": "badge bg-secondary",
    },
}

REQUEST_STATUS_ALLOWED = set(REQUEST_STATUS_META.keys())
REQUEST_STATUS_ORDER = ["open", "in_progress", "done", "cancelled"]
REQUEST_CANONICAL_STATUS_ORDER = tuple(REQUEST_STATUS_ORDER)

# Legacy write aliases -> canonical statuses (no migrations)
REQUEST_STATUS_ALIASES = {
    "approved": "in_progress",  # legacy approved behaves like in_progress
    "rejected": "cancelled",  # legacy rejected behaves like cancelled
    "pending": "open",
}

# Read-time compatibility aliases. This layer intentionally normalizes legacy
# vocabulary without rewriting stored DB values.
REQUEST_STATUS_READ_ALIASES = {
    "open": ("open", "pending"),
    "in_progress": ("in_progress", "approved"),
    "done": ("done", "completed", "resolved", "closed"),
    "cancelled": ("cancelled", "canceled", "rejected"),
}

REQUEST_ACTIVE_READ_TARGETS = {"open", "in_progress"}


def _clean_request_status(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_request_status(s: str | None) -> str:
    s = _clean_request_status(s)
    return REQUEST_STATUS_ALIASES.get(s, s)


def request_status_read_values(
    canonical_status: str,
    *,
    active_as: str | None = None,
) -> tuple[str, ...]:
    key = _clean_request_status(canonical_status)
    if key not in REQUEST_STATUS_ALLOWED:
        return ()

    values = list(REQUEST_STATUS_READ_ALIASES.get(key, (key,)))
    if key == active_as:
        values.append("active")

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean_request_status(value)
        if cleaned and cleaned not in seen:
            ordered.append(cleaned)
            seen.add(cleaned)
    return tuple(ordered)


def canonical_request_status(
    value: str | None,
    *,
    active_as: str = "open",
    fallback: str | None = None,
) -> str:
    key = _clean_request_status(value)
    if not key:
        return fallback if fallback is not None else ""

    if key == "active" and active_as in REQUEST_ACTIVE_READ_TARGETS:
        return active_as

    for canonical in REQUEST_CANONICAL_STATUS_ORDER:
        if key in request_status_read_values(canonical, active_as=active_as):
            return canonical

    return fallback if fallback is not None else key


def is_request_status(
    value: str | None,
    canonical_status: str,
    *,
    active_as: str = "open",
) -> bool:
    return canonical_request_status(value, active_as=active_as, fallback="") == _clean_request_status(
        canonical_status
    )


def is_terminal_request_status(
    value: str | None,
    *,
    active_as: str = "open",
) -> bool:
    return canonical_request_status(value, active_as=active_as, fallback="") in {
        "done",
        "cancelled",
    }


def request_terminal_status_read_values() -> tuple[str, ...]:
    values = list(request_status_read_values("done"))
    values.extend(request_status_read_values("cancelled"))

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return tuple(ordered)
