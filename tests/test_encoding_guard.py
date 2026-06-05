from pathlib import Path

FORBIDDEN_MARKERS = (
    "Ã",
    "Â",
    "\ufffd",
    "Ð",
    "Ñ",
    "â€™",
    "â€œ",
    "â€",
)

CHECKED_FILES = (
    Path("backend/helpchain_backend/src/routes/admin.py"),
)


def test_admin_routes_do_not_contain_mojibake_markers():
    for path in CHECKED_FILES:
        text = path.read_text(encoding="utf-8")
        found = [marker for marker in FORBIDDEN_MARKERS if marker in text]
        assert not found, f"{path} contains mojibake markers: {found}"