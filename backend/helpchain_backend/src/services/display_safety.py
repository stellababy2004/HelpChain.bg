from __future__ import annotations

import re
import unicodedata
from typing import Any


PLACEHOLDER_TOKENS = {
    "",
    "-",
    ".",
    "..",
    "null",
    "none",
    "undefined",
    "test",
    "sample",
    "example",
    "fake",
    "cabinet",
    "structure_locale",
    "non renseigne",
    "non renseign",
    "anonymous",
    "anonymous territory",
}


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def is_placeholder_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    folded = _fold(text)
    return folded in PLACEHOLDER_TOKENS


def safe_display_text(value: Any, fallback: str) -> str:
    if is_placeholder_value(value):
        return fallback
    return str(value).strip()


def safe_organization(value: Any, *, assigned: bool = True) -> str:
    return safe_display_text(
        value,
        "Unknown organization" if assigned else "No organization assigned",
    )


def safe_territory(value: Any, *, assigned: bool = True) -> str:
    return safe_display_text(
        value,
        "Unknown territory" if assigned else "No territory assigned",
    )


def safe_visitor(value: Any) -> str:
    return safe_display_text(value, "Unknown visitor")


def safe_summary(value: Any) -> str:
    return safe_display_text(value, "No summary available.")


def safe_recommendation(value: Any, *, confidence: str | None = None) -> str:
    # Preserve an explicit recommendation. Confidence controls only the
    # fallback shown when no usable recommendation has been produced.
    if not is_placeholder_value(value):
        return str(value).strip()

    confidence_value = _fold(confidence or "")
    if confidence_value in {"weak", "very low", "very_low"}:
        return "Insufficient evidence"
    if confidence_value == "low":
        return "Continue monitoring"
    return "No priority recommendation available."


def safe_unavailable(value: Any, fallback: str) -> str:
    return safe_display_text(value, fallback)

