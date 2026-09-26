from __future__ import annotations

import ast
from pathlib import Path


def test_services_do_not_import_workflow_package() -> None:
    """Reusable services must not depend upward on a concrete orchestrator."""

    services_root = Path(__file__).resolve().parents[1] / "src" / "mia_dpp" / "services"
    violations: list[str] = []

    for path in sorted(services_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "mia_dpp.workflow" or module.startswith("mia_dpp.workflow."):
                    violations.append(f"{path.name}:{node.lineno} imports {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "mia_dpp.workflow" or alias.name.startswith(
                        "mia_dpp.workflow."
                    ):
                        violations.append(f"{path.name}:{node.lineno} imports {alias.name}")

    assert violations == [], "\n".join(violations)
