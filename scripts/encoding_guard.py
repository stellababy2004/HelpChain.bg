from pathlib import Path
import sys

BAD_PATTERNS = [
    "Ã", "Â", "â—", "â¬", "â†", "ï»¿", "�"
]

WATCH_DIRS = [
    Path("templates/admin"),
    Path("templates/public"),
    Path("templates/partials"),
    Path("templates/layouts"),
    Path("static/css"),
    Path("static/js"),
    Path("backend/helpchain_backend/src"),
]

WATCH_FILES = [
    Path("templates/base.html"),
    Path("templates/index.html"),
    Path("templates/home_new_slim.html"),
    Path("templates/offre.html"),
    Path("templates/deploiement.html"),
    Path("templates/professionnels.html"),
    Path("templates/securite.html"),
    Path("templates/contact.html"),
]

SKIP_PARTS = {
    ".git", ".venv", "__pycache__", "_archive",
    ".pytest_cache", ".ruff_cache", "node_modules"
}

SKIP_SUFFIXES = (
    ".mojibake_bak",
    ".encoding_before_clean",
    ".encoding_round2",
    ".before_targeted_encoding_fix",
    ".bak",
    ".encoding_guard_bak",
)

SKIP_NAME_PREFIXES = (
    "styles_before_",
)

LEGACY_MOJIBAKE_FILES = {
    Path("backend/helpchain_backend/src/routes/admin.py"),
    Path("backend/helpchain_backend/src/routes/admin_structures.py"),
}

EXTS = {".html", ".css", ".js", ".py", ".md", ".txt", ".yml", ".yaml"}

def should_scan(path: Path) -> bool:
    if not path.is_file():
        return False
    if path in LEGACY_MOJIBAKE_FILES:
        return False
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if path.suffix not in EXTS:
        return False
    if str(path).endswith(SKIP_SUFFIXES):
        return False
    if path.name.startswith(SKIP_NAME_PREFIXES):
        return False
    if path in WATCH_FILES:
        return True
    return any(path.is_relative_to(d) for d in WATCH_DIRS if d.exists())

paths = []
for f in WATCH_FILES:
    if f.exists():
        paths.append(f)

for d in WATCH_DIRS:
    if d.exists():
        paths.extend([p for p in d.rglob("*") if should_scan(p)])

paths = sorted(set(paths))
hits = []

for path in paths:
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if any(p in line for p in BAD_PATTERNS):
            hits.append((path, i, line[:220]))

if hits:
    print("\n[ENCODING GUARD] Mojibake detected in protected production files. Commit/deploy blocked.\n")
    for path, line_no, line in hits[:200]:
        print(f"{path}:{line_no}: {line}")
    if len(hits) > 200:
        print(f"\n...and {len(hits) - 200} more matches.")
    sys.exit(1)

print("[ENCODING GUARD] OK — protected production files are clean.")


