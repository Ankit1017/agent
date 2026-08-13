"""Architecture fitness tests for inward dependency rules."""

from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_domain_has_no_outward_dependencies() -> None:
    """Domain modules remain independent from every outer layer."""
    root = Path("src/local_harness/domain")
    forbidden = (
        "local_harness.application",
        "local_harness.infrastructure",
        "local_harness.interfaces",
    )
    violations = [
        f"{path}: {name}"
        for path in root.glob("*.py")
        for name in _imports(path)
        if name.startswith(forbidden)
    ]
    assert violations == []


def test_application_does_not_import_infrastructure_or_interfaces() -> None:
    """Use cases depend on ports rather than concrete adapters."""
    root = Path("src/local_harness/application")
    forbidden = ("local_harness.infrastructure", "local_harness.interfaces")
    violations = [
        f"{path}: {name}"
        for path in root.glob("*.py")
        for name in _imports(path)
        if name.startswith(forbidden)
    ]
    assert violations == []
