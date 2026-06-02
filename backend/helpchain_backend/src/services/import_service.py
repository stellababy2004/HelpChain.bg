from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from io import StringIO
from pathlib import Path


IMPORT_TARGET_PROFESSIONAL_LEADS = "professional_leads"
IMPORT_SOURCE_CSV = "csv"
DEFAULT_PROFESSION = "Non renseigne"
SYNTHETIC_EMAIL_DOMAIN = "esante-fhir.local"
PREVIEW_SAMPLE_LIMIT = 20
MAX_ERROR_ITEMS = 100
IMPORT_ROW_STATUS_SKIPPED = "skipped"
IMPORT_ROW_STATUS_VALID = "valid"
IMPORT_ROW_STATUS_WARNING = "warning"
IMPORT_ROW_STATUS_REJECTED = "rejected"
IMPORT_RESULT_CREATED = "created"
IMPORT_RESULT_UPDATED = "updated"
IMPORT_RESULT_SKIPPED_DUPLICATE = "skipped_duplicate"
IMPORT_RESULT_REJECTED = "rejected"
MIN_SYNTHETIC_MEANINGFUL_FIELDS = 2
ENRICHABLE_IMPORT_FIELDS = (
    "full_name",
    "phone",
    "city",
    "profession",
    "organization",
    "availability",
    "message",
)

IMPORTABLE_FIELDS = (
    "full_name",
    "email",
    "phone",
    "city",
    "profession",
    "organization",
    "availability",
    "message",
)
IMPORTABLE_FIELD_LABELS = {
    "full_name": "Nom complet",
    "email": "Email",
    "phone": "Telephone",
    "city": "Ville",
    "profession": "Profession",
    "organization": "Organisation",
    "availability": "Disponibilite",
    "message": "Message",
}
TARGET_LABELS = {
    IMPORT_TARGET_PROFESSIONAL_LEADS: "Leads professionnels",
}
SOURCE_LABELS = {
    IMPORT_SOURCE_CSV: "CSV",
}
REJECTION_REASON_LABELS = {
    "empty_row": "Ligne vide ou inexploitable",
    "missing_contact_identity": "Identite de contact insuffisante",
    "sentence_like_name": "Nom semblable a une phrase ou a un titre",
    "placeholder_organization": "Organisation absente ou generique",
    "insufficient_fields_for_synthetic_email": "Informations insuffisantes pour creer un contact sans email",
    "invalid_email": "Email invalide",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_WHITESPACE_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\d")
_NAME_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'-]+")
_SENTENCE_START_RE = re.compile(r"^\s*(?:\d+\s*[\.\)]|[A-Za-z]\.|[-•])")
_HEADER_ALIASES = {
    "nom": "full_name",
    "name": "full_name",
    "contact": "full_name",
    "contact name": "full_name",
    "contactname": "full_name",
    "contact person": "full_name",
    "contactperson": "full_name",
    "prenom nom": "full_name",
    "prenomnom": "full_name",
    "nom complet": "full_name",
    "nomcomplet": "full_name",
    "nom du contact": "full_name",
    "full name": "full_name",
    "fullname": "full_name",
    "email": "email",
    "courriel": "email",
    "mail": "email",
    "e mail": "email",
    "e-mail": "email",
    "telephone": "phone",
    "tel": "phone",
    "mobile": "phone",
    "portable": "phone",
    "ville": "city",
    "commune": "city",
    "city": "city",
    "fonction": "profession",
    "role": "profession",
    "profession": "profession",
    "metier": "profession",
    "organisation": "organization",
    "organization": "organization",
    "structure": "organization",
    "etablissement": "organization",
    "disponibilite": "availability",
    "availability": "availability",
    "message": "message",
    "notes": "message",
    "note": "message",
    "contexte": "message",
    "commentaire": "message",
    "commentaires": "message",
}
_PLACEHOLDER_VALUES = {
    "",
    "-",
    "n a",
    "na",
    "non renseigne",
    "organisation non renseignee",
}


@dataclass(slots=True)
class ParsedImportFile:
    headers: list[str]
    rows: list[dict[str, str]]
    encoding: str


@dataclass(slots=True)
class PreviewRow:
    row_number: int
    values: dict[str, str]
    status: str
    warnings: list[str]
    reasons: list[str]


@dataclass(slots=True)
class PreviewPayload:
    headers: list[str]
    mapping: dict[str, str]
    sample_rows: list[PreviewRow]
    rows_detected: int
    created_rows: int
    updated_rows: int
    skipped_duplicate_rows: int
    rejected_rows: int
    preview_errors: list[str]
    rejection_reasons: list[tuple[str, int]]


@dataclass(slots=True)
class ImportRowResult:
    is_empty: bool
    is_valid: bool
    is_duplicate: bool
    status: str
    warnings: list[str]
    errors: list[str]
    reason_codes: list[str]
    payload: dict[str, str]
    normalized_email: str


@dataclass(slots=True)
class ImportPlan:
    mode: str
    updated_fields: list[str]
    previous_values: dict[str, str | None]


@dataclass(slots=True)
class ImportOutcome:
    created_count: int
    updated_count: int
    skipped_duplicate_count: int
    rejected_count: int
    errors: list[dict[str, object]]

    @property
    def imported_count(self) -> int:
        return int(self.created_count + self.updated_count)

    @property
    def skipped_count(self) -> int:
        return int(self.skipped_duplicate_count)

    @property
    def error_count(self) -> int:
        return int(self.rejected_count)


def available_target_options() -> list[tuple[str, str]]:
    return [(key, TARGET_LABELS[key]) for key in TARGET_LABELS]


def available_field_options() -> list[tuple[str, str]]:
    return [("", "Ignorer")] + [
        (field_name, IMPORTABLE_FIELD_LABELS[field_name]) for field_name in IMPORTABLE_FIELDS
    ]


def source_label(source_type: str | None) -> str:
    key = (source_type or "").strip().lower()
    return SOURCE_LABELS.get(key, key or "-")


def target_label(target_type: str | None) -> str:
    key = (target_type or "").strip().lower()
    return TARGET_LABELS.get(key, key or "-")


def import_batch_source(batch_id: int) -> str:
    return f"excel_import:{int(batch_id)}"


def rejection_reason_label(code: str) -> str:
    return REJECTION_REASON_LABELS.get(code, code)


def batch_errors_to_text(errors_json: str | None) -> str:
    items = parse_batch_errors(errors_json)
    return "; ".join(item.get("message", "") for item in items if item.get("message"))


def parse_batch_errors(errors_json: str | None) -> list[dict[str, object]]:
    try:
        payload = json.loads(errors_json or "[]")
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def parse_batch_mapping(mapping_json: str | None) -> dict[str, str]:
    try:
        payload = json.loads(mapping_json or "{}")
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in payload.items():
        if key:
            normalized[str(key)] = str(value or "")
    return normalized


def encode_json_payload(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_csv_bytes(raw_bytes: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_csv_bytes(raw_bytes: bytes) -> ParsedImportFile:
    text, encoding = decode_csv_bytes(raw_bytes)
    stream = StringIO(text, newline="")
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except Exception:
        dialect = csv.excel
        dialect.delimiter = ","

    reader = csv.DictReader(stream, dialect=dialect)
    raw_headers = list(reader.fieldnames or [])
    header_pairs = [
        (str(raw_header), _clean_cell(raw_header))
        for raw_header in raw_headers
        if _clean_cell(raw_header)
    ]
    headers = [clean_header for _, clean_header in header_pairs]
    rows: list[dict[str, str]] = []
    for row in reader:
        normalized_row: dict[str, str] = {}
        for raw_header, clean_header in header_pairs:
            normalized_row[clean_header] = _clean_cell((row or {}).get(raw_header))
        rows.append(normalized_row)
    return ParsedImportFile(headers=headers, rows=rows, encoding=encoding)


def infer_mapping(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for header in headers:
        mapping[header] = _HEADER_ALIASES.get(_normalize_header(header), "")
    return mapping


def sanitize_mapping(headers: list[str], raw_mapping: dict[str, str] | None) -> dict[str, str]:
    raw_mapping = raw_mapping or {}
    normalized: dict[str, str] = {}
    for header in headers:
        candidate = (raw_mapping.get(header) or "").strip()
        normalized[header] = candidate if candidate in IMPORTABLE_FIELDS else ""
    return normalized


def build_preview(
    parsed_file: ParsedImportFile,
    *,
    mapping: dict[str, str],
    existing_leads_by_email: dict[str, object] | None = None,
    batch_id: int = 0,
) -> PreviewPayload:
    lead_index = build_existing_lead_index(existing_leads_by_email or {})
    rows_detected = 0
    created_rows = 0
    updated_rows = 0
    skipped_duplicate_rows = 0
    rejected_rows = 0
    preview_errors: list[str] = []
    sample_rows: list[PreviewRow] = []
    rejection_counts: dict[str, int] = {}

    for index, row in enumerate(parsed_file.rows, start=1):
        result = normalize_import_row(
            row,
            mapping=mapping,
            batch_id=batch_id,
            row_number=index,
        )
        rows_detected += 1
        plan = plan_import_row(result, lead_index.get(result.normalized_email))
        row_messages = list(result.warnings)
        if plan.mode == IMPORT_RESULT_CREATED:
            created_rows += 1
            lead_index[result.normalized_email] = apply_import_plan_to_snapshot(
                lead_index.get(result.normalized_email),
                result.payload,
                plan,
            )
        elif plan.mode == IMPORT_RESULT_UPDATED:
            updated_rows += 1
            row_messages.append(
                "Champs mis a jour: "
                + ", ".join(IMPORTABLE_FIELD_LABELS.get(field, field) for field in plan.updated_fields)
            )
            lead_index[result.normalized_email] = apply_import_plan_to_snapshot(
                lead_index.get(result.normalized_email),
                result.payload,
                plan,
            )
        elif plan.mode == IMPORT_RESULT_SKIPPED_DUPLICATE:
            skipped_duplicate_rows += 1
            row_messages.append("Doublon detecte sans nouveau champ exploitable")
        else:
            rejected_rows += 1
            for reason_code in result.reason_codes:
                rejection_counts[reason_code] = rejection_counts.get(reason_code, 0) + 1

        for item in [*row_messages, *result.errors]:
            preview_errors.append(f"Ligne {index}: {item}")

        if len(sample_rows) < PREVIEW_SAMPLE_LIMIT:
            sample_rows.append(
                PreviewRow(
                    row_number=index,
                    values={header: row.get(header, "") for header in parsed_file.headers},
                    status=plan.mode,
                    warnings=[*row_messages, *result.errors],
                    reasons=[rejection_reason_label(code) for code in result.reason_codes],
                )
            )

    return PreviewPayload(
        headers=parsed_file.headers,
        mapping=sanitize_mapping(parsed_file.headers, mapping),
        sample_rows=sample_rows,
        rows_detected=rows_detected,
        created_rows=created_rows,
        updated_rows=updated_rows,
        skipped_duplicate_rows=skipped_duplicate_rows,
        rejected_rows=rejected_rows,
        preview_errors=preview_errors[:MAX_ERROR_ITEMS],
        rejection_reasons=sorted(
            (
                (rejection_reason_label(code), count)
                for code, count in rejection_counts.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )[:10],
    )


def normalize_import_row(
    row: dict[str, str],
    *,
    mapping: dict[str, str],
    batch_id: int,
    row_number: int,
) -> ImportRowResult:
    payload = {field_name: "" for field_name in IMPORTABLE_FIELDS}
    warnings: list[str] = []
    errors: list[str] = []
    reason_codes: list[str] = []

    for header, target_field in mapping.items():
        if not target_field or target_field not in payload:
            continue
        current_value = payload[target_field]
        incoming = _clean_cell(row.get(header))
        if incoming and not current_value:
            payload[target_field] = incoming

    is_empty = not any(payload.values())
    if is_empty:
        return ImportRowResult(
            is_empty=False,
            is_valid=False,
            is_duplicate=False,
            status=IMPORT_ROW_STATUS_REJECTED,
            warnings=[],
            errors=["Ligne vide ou sans donnees exploitables"],
            reason_codes=["empty_row"],
            payload=payload,
            normalized_email="",
        )

    full_name = _clean_cell(payload.get("full_name"))
    phone_value = _clean_cell(payload.get("phone"))
    city_value = _clean_cell(payload.get("city"))
    profession_value = _clean_cell(payload.get("profession"))
    organization_value = _clean_cell(payload.get("organization"))

    full_name_is_sentence_like = _is_sentence_like_name(full_name)
    full_name_is_weak = _is_weak_full_name(full_name)
    organization_is_placeholder = _is_placeholder_value(organization_value)
    profession_is_placeholder = _is_placeholder_value(profession_value)
    phone_is_meaningful = _is_meaningful_phone(phone_value)

    if full_name_is_sentence_like:
        errors.append("Nom / contact non exploitable (phrase, titre ou paragraphe)")
        reason_codes.append("sentence_like_name")

    email_value = _normalize_email(payload.get("email"))
    if email_value and not _EMAIL_RE.match(email_value):
        errors.append("Email invalide")
        reason_codes.append("invalid_email")

    if organization_value and organization_is_placeholder:
        errors.append("Organisation absente ou generique")
        reason_codes.append("placeholder_organization")

    if (
        not email_value
        and not phone_is_meaningful
        and organization_is_placeholder
        and profession_is_placeholder
        and full_name_is_weak
    ):
        errors.append("Identite de contact insuffisante")
        reason_codes.append("missing_contact_identity")

    if not email_value:
        meaningful_fields = sum(
            1
            for is_meaningful in (
                _is_meaningful_name_for_identity(full_name),
                phone_is_meaningful,
                _is_meaningful_city(city_value),
                not profession_is_placeholder,
                not organization_is_placeholder,
            )
            if is_meaningful
        )
        if meaningful_fields < MIN_SYNTHETIC_MEANINGFUL_FIELDS:
            errors.append("Informations insuffisantes pour generer un contact sans email")
            reason_codes.append("insufficient_fields_for_synthetic_email")
        else:
            email_value = synthetic_email_for_row(
                payload=payload,
                batch_id=batch_id,
                row_number=row_number,
            )
            payload["email"] = email_value
            warnings.append("Email manquant, adresse synthetique generee")
    else:
        payload["email"] = email_value

    if not profession_value:
        payload["profession"] = DEFAULT_PROFESSION

    normalized_email = email_value.strip().lower()
    status = IMPORT_ROW_STATUS_VALID
    if errors:
        status = IMPORT_ROW_STATUS_REJECTED
    elif warnings:
        status = IMPORT_ROW_STATUS_WARNING

    return ImportRowResult(
        is_empty=False,
        is_valid=not errors,
        is_duplicate=False,
        status=status,
        warnings=warnings,
        errors=errors,
        reason_codes=reason_codes,
        payload=payload,
        normalized_email=normalized_email,
    )


def synthetic_email_for_row(*, payload: dict[str, str], batch_id: int, row_number: int) -> str:
    fingerprint = "|".join(
        [
            str(batch_id or 0),
            str(row_number or 0),
            _normalize_for_hash(payload.get("full_name")),
            _normalize_for_hash(payload.get("phone")),
            _normalize_for_hash(payload.get("city")),
            _normalize_for_hash(payload.get("profession")),
            _normalize_for_hash(payload.get("organization")),
        ]
    )
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"no-email+{digest}@{SYNTHETIC_EMAIL_DOMAIN}"


def save_preview_upload(*, instance_path: str, filename: str, raw_bytes: bytes) -> dict[str, str]:
    preview_dir = Path(instance_path) / "import_express_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    suffix = Path(filename or "upload.csv").suffix or ".csv"
    stored_name = f"{token}{suffix}"
    stored_path = preview_dir / stored_name
    stored_path.write_bytes(raw_bytes)
    return {"token": token, "path": str(stored_path)}


def load_preview_upload(preview_path: str) -> bytes:
    return Path(preview_path).read_bytes()


def cleanup_preview_upload(preview_path: str | None) -> None:
    if not preview_path:
        return
    try:
        path = Path(preview_path)
        if path.exists():
            path.unlink()
    except Exception:
        return


def import_professional_leads(
    *,
    parsed_file: ParsedImportFile,
    mapping: dict[str, str],
    batch_id: int,
    existing_leads_by_email: dict[str, object],
    create_row,
    update_row,
    record_activity=None,
    source_filename: str | None = None,
) -> ImportOutcome:
    created_count = 0
    updated_count = 0
    skipped_duplicate_count = 0
    rejected_count = 0
    errors: list[dict[str, object]] = []
    lead_index = {
        email: lead
        for email, lead in (existing_leads_by_email or {}).items()
        if str(email or "").strip()
    }

    for index, row in enumerate(parsed_file.rows, start=1):
        result = normalize_import_row(
            row,
            mapping=mapping,
            batch_id=batch_id,
            row_number=index,
        )
        plan = plan_import_row(result, lead_index.get(result.normalized_email))
        if plan.mode == IMPORT_RESULT_REJECTED:
            rejected_count += 1
            if len(errors) < MAX_ERROR_ITEMS:
                errors.append(
                    {
                        "row_number": index,
                        "message": "; ".join(result.errors),
                        "reason_codes": list(result.reason_codes),
                    }
                )
            continue

        try:
            if plan.mode == IMPORT_RESULT_CREATED:
                lead = create_row(result.payload)
                created_count += 1
            elif plan.mode == IMPORT_RESULT_UPDATED:
                lead = update_row(
                    lead_index[result.normalized_email],
                    result.payload,
                    updated_fields=plan.updated_fields,
                )
                updated_count += 1
            else:
                lead = lead_index[result.normalized_email]
                skipped_duplicate_count += 1
        except Exception as exc:
            rejected_count += 1
            if len(errors) < MAX_ERROR_ITEMS:
                errors.append({"row_number": index, "message": str(exc)})
            continue

        if record_activity and getattr(lead, "id", None):
            record_activity(
                lead,
                action=f"import_{plan.mode}",
                payload={
                    "import_batch_id": int(batch_id),
                    "source_filename": source_filename or "",
                    "imported_at": "",
                    "updated_fields": list(plan.updated_fields),
                    "previous_values": dict(plan.previous_values),
                    "email": result.normalized_email,
                },
            )

        lead_index[result.normalized_email] = lead

    return ImportOutcome(
        created_count=created_count,
        updated_count=updated_count,
        skipped_duplicate_count=skipped_duplicate_count,
        rejected_count=rejected_count,
        errors=errors,
    )


def build_existing_lead_index(existing_leads_by_email: dict[str, object]) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for email, lead in (existing_leads_by_email or {}).items():
        normalized_email = _normalize_email(email)
        if not normalized_email:
            continue
        output[normalized_email] = snapshot_existing_lead(lead)
    return output


def snapshot_existing_lead(lead: object | None) -> dict[str, object]:
    if lead is None:
        return {}
    if isinstance(lead, dict):
        source = lead
        get_value = lambda key: source.get(key)
    else:
        get_value = lambda key: getattr(lead, key, None)
    snapshot: dict[str, object] = {
        "id": get_value("id"),
        "email": _normalize_email(get_value("email")),
    }
    for field_name in ENRICHABLE_IMPORT_FIELDS:
        snapshot[field_name] = _clean_cell(get_value(field_name)) or None
    return snapshot


def plan_import_row(result: ImportRowResult, existing_lead: object | None) -> ImportPlan:
    if not result.is_valid:
        return ImportPlan(
            mode=IMPORT_RESULT_REJECTED,
            updated_fields=[],
            previous_values={},
        )
    if existing_lead is None:
        return ImportPlan(
            mode=IMPORT_RESULT_CREATED,
            updated_fields=[],
            previous_values={},
        )

    existing_snapshot = snapshot_existing_lead(existing_lead)
    updated_fields: list[str] = []
    previous_values: dict[str, str | None] = {}
    for field_name in ENRICHABLE_IMPORT_FIELDS:
        incoming = _clean_cell(result.payload.get(field_name)) or None
        current = _clean_cell(existing_snapshot.get(field_name)) or None
        if not incoming or incoming == current:
            continue
        updated_fields.append(field_name)
        previous_values[field_name] = existing_snapshot.get(field_name)

    return ImportPlan(
        mode=IMPORT_RESULT_UPDATED if updated_fields else IMPORT_RESULT_SKIPPED_DUPLICATE,
        updated_fields=updated_fields,
        previous_values=previous_values,
    )


def apply_import_plan_to_snapshot(
    existing_lead: object | None,
    payload: dict[str, str],
    plan: ImportPlan,
) -> dict[str, object]:
    snapshot = snapshot_existing_lead(existing_lead)
    snapshot["email"] = _normalize_email(payload.get("email"))
    if plan.mode == IMPORT_RESULT_CREATED:
        for field_name in ENRICHABLE_IMPORT_FIELDS:
            snapshot[field_name] = _clean_cell(payload.get(field_name)) or None
        return snapshot
    if plan.mode == IMPORT_RESULT_UPDATED:
        for field_name in plan.updated_fields:
            snapshot[field_name] = _clean_cell(payload.get(field_name)) or None
    return snapshot


def _clean_cell(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text)


def _normalize_email(value: str | None) -> str:
    return _clean_cell(value).lower()


def _normalize_header(value: str | None) -> str:
    text = _clean_cell(value).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _normalize_for_hash(value: str | None) -> str:
    return _normalize_header(value or "")


def _is_placeholder_value(value: str | None) -> bool:
    normalized = _normalize_header(value or "")
    return normalized in _PLACEHOLDER_VALUES


def _is_meaningful_phone(value: str | None) -> bool:
    digits = "".join(_DIGIT_RE.findall(_clean_cell(value)))
    return len(digits) >= 6


def _is_meaningful_city(value: str | None) -> bool:
    text = _clean_cell(value)
    return len(text) >= 2 and not _is_placeholder_value(text)


def _is_sentence_like_name(value: str | None) -> bool:
    text = _clean_cell(value)
    if not text:
        return False
    if _SENTENCE_START_RE.match(text):
        return True
    words = _NAME_WORD_RE.findall(text)
    if len(words) > 5:
        return True
    if text.endswith((".", ":", ";", "!")):
        return True
    if any(marker in text for marker in ("?", "(", ")", " / ", " - ")):
        return True
    punctuation_count = sum(text.count(marker) for marker in (".", ",", ";", ":", "!", "?"))
    if punctuation_count >= 2:
        return True
    if text and text[0].islower() and len(words) >= 3:
        return True
    return False


def _is_weak_full_name(value: str | None) -> bool:
    text = _clean_cell(value)
    if not text:
        return True
    if _is_sentence_like_name(text):
        return True
    words = _NAME_WORD_RE.findall(text)
    if len(words) >= 2:
        return False
    return len(words) == 0 or len(words[0]) < 4


def _is_meaningful_name_for_identity(value: str | None) -> bool:
    text = _clean_cell(value)
    if not text:
        return False
    if _is_sentence_like_name(text):
        return False
    words = _NAME_WORD_RE.findall(text)
    return bool(words) and (len(words) >= 2 or len(words[0]) >= 4)
