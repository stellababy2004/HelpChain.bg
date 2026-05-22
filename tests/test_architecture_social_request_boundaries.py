from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.spine


REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_SYMBOLS = {"SocialRequest", "SocialRequestEvent"}
FORBIDDEN_MODULE_FRAGMENTS = ("social_request",)

PROTECTED_MODULES = {
    "reporting": [
        "backend/helpchain_backend/src/services/reporting/operations_report.py",
        "backend/helpchain_backend/src/services/weekly_operations_report.py",
        "backend/helpchain_backend/src/services/daily_health_report.py",
    ],
    "sla": [
        "backend/helpchain_backend/src/services/request_sla.py",
        "backend/helpchain_backend/src/services/sla_alerts.py",
    ],
    "assignment": [
        "backend/helpchain_backend/src/routes/admin.py",
        "backend/helpchain_backend/src/routes/admin_requests.py",
        "backend/helpchain_backend/src/routes/admin_cases.py",
    ],
}

REQUEST_CANONICAL_STATUSES = {"open", "in_progress", "done", "cancelled"}
REQUEST_COMPATIBILITY_ALIASES = {
    "pending",
    "approved",
    "rejected",
    "completed",
    "resolved",
    "closed",
    "active",
    "canceled",
}
REQUEST_STATUS_VOCABULARY = REQUEST_CANONICAL_STATUSES | REQUEST_COMPATIBILITY_ALIASES

REQUEST_STATUS_PROTECTED_MODULES = {
    "admin": [
        "backend/helpchain_backend/src/routes/admin_requests.py",
    ],
    "reporting": [
        "backend/helpchain_backend/src/services/reporting/operations_report.py",
    ],
    "sla": [
        "backend/helpchain_backend/src/services/request_sla.py",
    ],
    "ops": [
        "backend/admin/ops_api.py",
    ],
    "tests": [
        "tests/test_request_statuses.py",
        "tests/test_request_sla_engine.py",
        "tests/test_ops_metrics_status_compat.py",
    ],
}


def _parse_module(relative_path: str) -> ast.AST:
    path = REPO_ROOT / relative_path
    source = path.read_text(encoding="utf-8-sig")
    return ast.parse(source, filename=str(path))


def _forbidden_references(relative_path: str) -> list[str]:
    tree = _parse_module(relative_path)
    hits: set[str] = set()

    class ForbiddenReferenceVisitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if any(fragment in alias.name.lower() for fragment in FORBIDDEN_MODULE_FRAGMENTS):
                    hits.add(f"import {alias.name}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module_name = (node.module or "").lower()
            if any(fragment in module_name for fragment in FORBIDDEN_MODULE_FRAGMENTS):
                hits.add(f"from {node.module} import ...")
            for alias in node.names:
                if alias.name in FORBIDDEN_SYMBOLS:
                    hits.add(f"from {node.module} import {alias.name}")
                if any(fragment in alias.name.lower() for fragment in FORBIDDEN_MODULE_FRAGMENTS):
                    hits.add(f"from {node.module} import {alias.name}")
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if node.id in FORBIDDEN_SYMBOLS:
                hits.add(f"name {node.id}")
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in FORBIDDEN_SYMBOLS:
                hits.add(f"attribute {node.attr}")
            self.generic_visit(node)

    ForbiddenReferenceVisitor().visit(tree)
    return sorted(hits)


def _contains_request_status_reference(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and child.attr == "status"
            and isinstance(child.value, ast.Name)
            and child.value.id == "Request"
        ):
            return True
    return False


def _string_literals(node: ast.AST | None) -> set[str]:
    values: set[str] = set()
    if node is None:
        return values
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value.strip().lower())
    return values


def _assignment_target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.Assign):
        targets = node.targets
    else:
        targets = [node.target]

    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _direct_string_literals(node: ast.AST | None) -> set[str]:
    values: set[str] = set()

    def visit(current: ast.AST | None) -> None:
        if current is None:
            return
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            values.add(current.value.strip().lower())
            return
        if isinstance(current, (ast.List, ast.Tuple, ast.Set)):
            for elt in current.elts:
                if isinstance(elt, ast.Starred):
                    continue
                visit(elt)
            return
        if isinstance(current, ast.Dict):
            for key in current.keys:
                visit(key)
            for value in current.values:
                visit(value)

    visit(node)
    return values


def _request_status_vocabulary_violations(relative_path: str) -> list[str]:
    tree = _parse_module(relative_path)
    hits: set[str] = set()
    allow_compatibility_aliases = relative_path.startswith("tests/")

    class RequestStatusVocabularyVisitor(ast.NodeVisitor):
        def visit_Compare(self, node: ast.Compare) -> None:
            if not _contains_request_status_reference(node.left):
                self.generic_visit(node)
                return

            raw_values: set[str] = set()
            for comparator in node.comparators:
                raw_values.update(_string_literals(comparator) & REQUEST_STATUS_VOCABULARY)

            disallowed = raw_values - REQUEST_CANONICAL_STATUSES
            if disallowed and not allow_compatibility_aliases:
                hits.add(
                    "raw Request.status comparison uses compatibility/legacy values: "
                    + ", ".join(sorted(disallowed))
                )
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"in_", "notin_"}
                and _contains_request_status_reference(node.func.value)
            ):
                raw_values: set[str] = set()
                for arg in node.args:
                    raw_values.update(_string_literals(arg) & REQUEST_STATUS_VOCABULARY)

                disallowed = raw_values - REQUEST_CANONICAL_STATUSES
                if disallowed and not allow_compatibility_aliases:
                    hits.add(
                        f"raw Request.status {node.func.attr} uses compatibility/legacy values: "
                        + ", ".join(sorted(disallowed))
                    )
            self.generic_visit(node)

    RequestStatusVocabularyVisitor().visit(tree)

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target_names = _assignment_target_names(node)
        if not any("STATUS" in name.upper() for name in target_names):
            continue

        literal_values = _direct_string_literals(node.value) & REQUEST_STATUS_VOCABULARY
        if len(literal_values) >= 2:
            hits.add(
                "scattered status vocabulary assignment outside statuses.py: "
                + ", ".join(sorted(literal_values))
            )

    return sorted(hits)


@pytest.mark.parametrize("relative_path", PROTECTED_MODULES["reporting"])
def test_reporting_modules_do_not_depend_on_social_request(relative_path: str):
    assert _forbidden_references(relative_path) == [], (
        "Canonical reporting modules must stay on the Request/Case operational spine; "
        f"found SocialRequest dependency in {relative_path}: {_forbidden_references(relative_path)}"
    )


@pytest.mark.parametrize("relative_path", PROTECTED_MODULES["sla"])
def test_sla_modules_do_not_depend_on_social_request(relative_path: str):
    assert _forbidden_references(relative_path) == [], (
        "Canonical SLA modules must stay on the Request/Case operational spine; "
        f"found SocialRequest dependency in {relative_path}: {_forbidden_references(relative_path)}"
    )


@pytest.mark.parametrize("relative_path", PROTECTED_MODULES["assignment"])
def test_assignment_modules_do_not_depend_on_social_request(relative_path: str):
    assert _forbidden_references(relative_path) == [], (
        "Canonical assignment orchestration must stay on the Request/Case operational spine; "
        f"found SocialRequest dependency in {relative_path}: {_forbidden_references(relative_path)}"
    )


@pytest.mark.parametrize(
    "relative_path",
    REQUEST_STATUS_PROTECTED_MODULES["admin"]
    + REQUEST_STATUS_PROTECTED_MODULES["reporting"]
    + REQUEST_STATUS_PROTECTED_MODULES["sla"]
    + REQUEST_STATUS_PROTECTED_MODULES["ops"]
    + REQUEST_STATUS_PROTECTED_MODULES["tests"],
)
def test_canonical_request_modules_do_not_introduce_new_status_vocabularies(
    relative_path: str,
):
    assert _request_status_vocabulary_violations(relative_path) == [], (
        "Canonical Request lifecycle modules must keep raw status vocabularies inside "
        "backend/helpchain_backend/src/statuses.py (tests may cover compatibility aliases "
        "explicitly); found violations in "
        f"{relative_path}: {_request_status_vocabulary_violations(relative_path)}"
    )
