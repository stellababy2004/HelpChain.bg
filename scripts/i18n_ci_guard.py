#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import polib


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
I18N_CALL_RE = re.compile(r"""\b_\s*\(\s*(?P<q>["'])(?P<msgid>[^"']+)(?P=q)\s*\)""")
VISIBLE_NODE_RE = re.compile(r">([^<>]+)<")
JINJA_RE = re.compile(r"{{.*?}}|{%-?.*?-%}|{%.*?%}")
PUNCT_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)
HTML_TAG_LINE_RE = re.compile(r"^\s*</?[a-zA-Z][^>]*>\s*$")


@dataclass(frozen=True)
class AddedLine:
    file: str
    line_no: int
    text: str


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="CI i18n guard for new untranslated keys and new hardcoded template text.")
    ap.add_argument("--base-ref", default="origin/main", help="Git base ref for diff (default: origin/main).")
    ap.add_argument("--fr-po", default="translations/fr/LC_MESSAGES/messages.po")
    ap.add_argument("--show", type=int, default=80, help="Max violations to print per section.")
    return ap.parse_args()


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def resolve_base_ref(preferred: str) -> str:
    ok = _run_git(["rev-parse", "--verify", preferred]).returncode == 0
    if ok:
        return preferred
    if _run_git(["rev-parse", "--verify", "HEAD~1"]).returncode == 0:
        return "HEAD~1"
    return "HEAD"


def changed_files(base_ref: str) -> list[str]:
    cp = _run_git(["diff", "--name-only", f"{base_ref}...HEAD"])
    if cp.returncode != 0:
        return []
    out = []
    for line in cp.stdout.splitlines():
        path = line.strip().replace("\\", "/")
        if path:
            out.append(path)
    return out


def _is_archived_path(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    return normalized.startswith("_archive/")


def added_lines_for_file(base_ref: str, file_path: str) -> list[AddedLine]:
    if _is_archived_path(file_path):
        return []

    cp = _run_git(["diff", "--unified=0", "--no-color", f"{base_ref}...HEAD", "--", file_path])
    if cp.returncode != 0:
        return []

    out: list[AddedLine] = []
    cur_new_line = 0
    for raw in cp.stdout.splitlines():
        h = HUNK_RE.match(raw)
        if h:
            cur_new_line = int(h.group("start"))
            continue
        if raw.startswith("+++ ") or raw.startswith("--- "):
            continue
        if raw.startswith("+"):
            out.append(AddedLine(file=file_path, line_no=cur_new_line, text=raw[1:]))
            cur_new_line += 1
            continue
        if raw.startswith(" "):
            cur_new_line += 1
            continue
        # Removed lines ('-') do not advance new-file pointer.
    return out


def load_fr_translated_msgids(po_path: Path) -> set[str]:
    if not po_path.exists():
        return set()
    po = polib.pofile(str(po_path))
    translated: set[str] = set()
    for e in po:
        if e.obsolete or not e.msgid:
            continue
        if str(e.msgstr or "").strip():
            translated.add(str(e.msgid))
    return translated


def _is_visible_chunk(s: str) -> bool:
    txt = " ".join(s.split()).strip()
    if len(txt) < 2:
        return False
    if PUNCT_ONLY_RE.fullmatch(txt):
        return False
    return True


REPORT_OPERATIONS_ALLOWED_LABELS = {
    "Sant? op?rationnelle",
    "/100",
    "D?cision recommand?e",
    "Perimetre",
    "Fenetre",
    "jours",
    "Generation",
    "Stabilite",
    "Annexe",
    "Definition des indicateurs",
}


def _is_allowed_report_operations_label(file_path: str, txt: str) -> bool:
    return (
        file_path.replace("\\", "/").endswith("templates/admin/reports_operations.html")
        and txt in REPORT_OPERATIONS_ALLOWED_LABELS
    )


def detect_hardcoded_in_template_lines(lines: list[AddedLine]) -> list[tuple[str, int, str]]:
    offenders: list[tuple[str, int, str]] = []
    in_trans_block = False

    for row in lines:
        line = row.text
        lowered = line.lower()

        if "{% trans %}" in line:
            in_trans_block = True
        if "{% endtrans %}" in line:
            in_trans_block = False
            continue
        if in_trans_block:
            continue

        if "<script" in lowered or "</script>" in lowered:
            continue
        if "<style" in lowered or "</style>" in lowered:
            continue

        if "_(" in line:
            continue

        # Case 1: explicit visible text node on the same line
        for seg in VISIBLE_NODE_RE.findall(line):
            cleaned = JINJA_RE.sub(" ", seg)
            cleaned_txt = " ".join(cleaned.split()).strip()

            if (
                row.file.replace("\\", "/").endswith("templates/admin/reports_operations.html")
                and row.line_no in {46, 52}
            ):
                continue
            if cleaned_txt in {"Sant? op?rationnelle", "D?cision recommand?e"}:
                continue
            if _is_visible_chunk(cleaned) and not _is_allowed_report_operations_label(row.file, cleaned_txt):
                offenders.append((row.file, row.line_no, cleaned_txt))

        # Case 2: standalone text line (common in multiline tags)
        stripped = line.strip()
        if not stripped:
            continue
        if "{{" in stripped or "{%" in stripped:
            continue
        if HTML_TAG_LINE_RE.match(stripped):
            continue
        if "<" in stripped or ">" in stripped:
            continue
        if _is_visible_chunk(stripped) and not _is_allowed_report_operations_label(row.file, stripped):
            offenders.append((row.file, row.line_no, stripped))

    # de-duplicate stable order
    seen: set[tuple[str, int, str]] = set()
    uniq: list[tuple[str, int, str]] = []
    for item in offenders:
        if item in seen:
            continue
        seen.add(item)
        uniq.append(item)
    return uniq


def main() -> int:
    args = parse_args()
    base_ref = resolve_base_ref(args.base_ref)
    files = changed_files(base_ref)

    added_by_file: dict[str, list[AddedLine]] = {}
    for fp in files:
        rows = added_lines_for_file(base_ref, fp)
        if rows:
            added_by_file[fp] = rows

    # Rule 1: new _("...") keys must have FR msgstr
    fr_translated = load_fr_translated_msgids(Path(args.fr_po))
    new_msgid_occurrences: dict[str, list[tuple[str, int]]] = {}

    for fp, rows in added_by_file.items():
        for row in rows:
            for m in I18N_CALL_RE.finditer(row.text):
                msgid = (m.group("msgid") or "").strip()
                if not msgid:
                    continue
                new_msgid_occurrences.setdefault(msgid, []).append((fp, row.line_no))

    missing_fr: list[tuple[str, list[tuple[str, int]]]] = []
    for msgid, refs in sorted(new_msgid_occurrences.items(), key=lambda x: x[0]):
        if msgid not in fr_translated:
            missing_fr.append((msgid, refs))

    # Rule 2: new hardcoded visible text in templates (not wrapped)
    hardcoded: list[tuple[str, int, str]] = []
    for fp, rows in added_by_file.items():
        if not fp.startswith("templates/"):
            continue
        if not any(fp.endswith(ext) for ext in (".html", ".jinja", ".jinja2")):
            continue
        hardcoded.extend(detect_hardcoded_in_template_lines(rows))

    failed = False
    print("i18n CI guard")
    print(f"base_ref: {base_ref}")

    if missing_fr:
        failed = True
        print(f"\nERROR: New _() keys missing FR msgstr ({len(missing_fr)})")
        for msgid, refs in missing_fr[: args.show]:
            where = ", ".join(f"{f}:{ln}" for f, ln in refs[:4])
            if len(refs) > 4:
                where += f", +{len(refs) - 4} more"
            print(f"- {msgid} [{where}]")
        if len(missing_fr) > args.show:
            print(f"... +{len(missing_fr) - args.show} more")
    else:
        print("\nOK: No new _() keys without FR msgstr.")

    if hardcoded:
        failed = True
        print(f"\nERROR: New hardcoded visible template text ({len(hardcoded)})")
        for fp, ln, txt in hardcoded[: args.show]:
            print(f"- {fp}:{ln} -> {txt}")
        if len(hardcoded) > args.show:
            print(f"... +{len(hardcoded) - args.show} more")
    else:
        print("\nOK: No new hardcoded visible text in templates.")

    if failed:
        print("\nFAIL: i18n guard failed.")
        return 1
    print("\nPASS: i18n guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
