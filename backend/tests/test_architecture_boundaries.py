"""Architecture boundary tests keep reusable layers independent of orchestration."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "mia_dpp"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_reusable_layers_do_not_depend_on_orchestration() -> None:
    forbidden = ("mia_dpp.workflow", "mia_dpp.orchestration")
    violations: list[str] = []
    for layer in ("capabilities", "services"):
        for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
            for module in sorted(_imports(path)):
                if module.startswith(forbidden):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert not violations, "reusable layers depend on orchestration:\n" + "\n".join(violations)


def test_runtime_does_not_depend_on_workflow_state() -> None:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / "runtime").rglob("*.py")):
        for module in sorted(_imports(path)):
            if module.startswith("mia_dpp.workflow"):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert not violations, "runtime depends on workflow:\n" + "\n".join(violations)
