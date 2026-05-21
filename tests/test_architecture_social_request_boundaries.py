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
