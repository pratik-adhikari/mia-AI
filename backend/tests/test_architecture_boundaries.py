from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_PREFIXES = ("mia_dpp.workflow", "langgraph")


def _module_name(path: Path, services_root: Path) -> str:
    relative = path.relative_to(services_root)
    parts = list(relative.with_suffix("").parts)
    return ".".join(("mia_dpp", "services", *parts))


def _resolve_from_import(
    node: ast.ImportFrom,
    *,
    current_module: str,
) -> tuple[str, ...]:
    """Resolve every target of one from-import statement."""

    if node.level == 0:
        base = node.module or ""
    else:
        package_parts = current_module.split(".")[:-1]
        keep = len(package_parts) - (node.level - 1)
        if keep < 0:
            return ()
        prefix = package_parts[:keep]
        suffix = node.module.split(".") if node.module else []
        base = ".".join((*prefix, *suffix))

    targets: list[str] = []
    for alias in node.names:
        if alias.name == "*":
            targets.append(base)
        elif base:
            targets.append(f"{base}.{alias.name}")
        else:
            targets.append(alias.name)
    return tuple(targets)


def _import_targets(tree: ast.AST, *, current_module: str) -> tuple[tuple[int, str], ...]:
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.extend(
                (node.lineno, target)
                for target in _resolve_from_import(node, current_module=current_module)
            )
    return tuple(targets)


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in _FORBIDDEN_PREFIXES
    )


def test_services_do_not_depend_on_workflow_or_langgraph() -> None:
    """Reusable services must not depend upward on a concrete orchestrator."""

    services_root = Path(__file__).resolve().parents[1] / "src" / "mia_dpp" / "services"
    violations: list[str] = []

    for path in sorted(services_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        current_module = _module_name(path, services_root)
        for lineno, module in _import_targets(tree, current_module=current_module):
            if _is_forbidden(module):
                violations.append(f"{path.name}:{lineno} imports {module}")

    assert violations == [], "\n".join(violations)


def test_architecture_guard_catches_supported_import_forms() -> None:
    """Keep the boundary test itself from regressing to syntax-specific matching."""

    examples = (
        ("from mia_dpp.workflow.nodes_product import merge_packages", "mia_dpp.services.sample"),
        ("import mia_dpp.workflow.state", "mia_dpp.services.sample"),
        ("from mia_dpp import workflow", "mia_dpp.services.sample"),
        ("from ..workflow import state", "mia_dpp.services.sample"),
        ("from ..workflow.nodes_product import merge_packages", "mia_dpp.services.sample"),
        ("from langgraph.types import Command", "mia_dpp.services.sample"),
        ("import langgraph", "mia_dpp.services.sample"),
    )

    for source, current_module in examples:
        tree = ast.parse(source)
        targets = _import_targets(tree, current_module=current_module)
        assert any(_is_forbidden(module) for _, module in targets), source


_PHASE3_NODE_FILES = (
    "nodes_product.py",
    "nodes_semantic.py",
    "nodes_mapping.py",
    "nodes_research.py",
    "nodes_aas.py",
    "nodes_promotion.py",
)

_PHASE3_FORBIDDEN_NODE_PREFIXES = (
    "mia_dpp.persistence",
    "mia_dpp.normalization",
    "mia_dpp.semantic",
    "mia_dpp.storage",
    "mia_dpp.tools.mapping",
    "mia_dpp.aas.build",
)


def test_extracted_workflow_nodes_do_not_reimport_business_implementations() -> None:
    """Phase-3 graph nodes should compose services instead of owning backend algorithms."""

    workflow_root = Path(__file__).resolve().parents[1] / "src" / "mia_dpp" / "workflow"
    violations: list[str] = []

    for name in _PHASE3_NODE_FILES:
        path = workflow_root / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        current_module = f"mia_dpp.workflow.{path.stem}"
        for lineno, module in _import_targets(tree, current_module=current_module):
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in _PHASE3_FORBIDDEN_NODE_PREFIXES
            ):
                violations.append(f"{name}:{lineno} imports {module}")

    assert violations == [], "\n".join(violations)
