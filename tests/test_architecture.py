"""Executable dependency rules, checked without importing Qt or PC/SC."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "attendance_lab"
LAYERS = ("domain", "application", "infrastructure", "presentation")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = ("attendance_lab", *path.relative_to(SOURCE).parts[:-1])
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                module = ".".join((*base, *((node.module or "").split("."))))
            else:
                module = node.module or ""
            imports.add(module.rstrip("."))
            imports.update(f"{module.rstrip('.')}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            function = node.func
            dynamic_import = (isinstance(function, ast.Name) and function.id == "__import__") or (
                isinstance(function, ast.Attribute) and function.attr == "import_module"
            )
            if dynamic_import and isinstance(node.args[0].value, str):
                imports.add(node.args[0].value)
    return imports


def has_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        (
            "domain",
            (
                "attendance_lab.application",
                "attendance_lab.infrastructure",
                "attendance_lab.presentation",
                "attendance_lab.bootstrap",
                "PySide6",
                "smartcard",
                "sqlite3",
            ),
        ),
        (
            "application",
            (
                "attendance_lab.infrastructure",
                "attendance_lab.presentation",
                "attendance_lab.bootstrap",
                "PySide6",
                "smartcard",
                "sqlite3",
            ),
        ),
        (
            "infrastructure",
            ("attendance_lab.presentation", "attendance_lab.bootstrap", "PySide6"),
        ),
        (
            "presentation",
            ("attendance_lab.infrastructure", "attendance_lab.bootstrap", "smartcard", "sqlite3"),
        ),
    ],
)
def test_dependencies_point_towards_domain(layer: str, forbidden: tuple[str, ...]) -> None:
    violations = [
        f"{path.relative_to(SOURCE)} imports {module}"
        for path in (SOURCE / layer).rglob("*.py")
        for module in imported_modules(path)
        if any(has_prefix(module, prefix) for prefix in forbidden)
    ]
    assert not violations, "Forbidden layer dependencies:\n" + "\n".join(sorted(violations))


def test_public_window_cannot_access_service_controller_or_private_windows() -> None:
    forbidden = (
        "attendance_lab.application",
        "attendance_lab.infrastructure",
        "attendance_lab.presentation.admin",
        "attendance_lab.presentation.students",
        "attendance_lab.presentation.logs",
        "attendance_lab.presentation.dialogs",
    )
    violations = [
        module
        for module in imported_modules(SOURCE / "presentation" / "public.py")
        if any(has_prefix(module, prefix) for prefix in forbidden)
    ]
    assert not violations, f"Public presentation received private capabilities: {violations}"


def test_bootstrap_composes_adapters_without_direct_device_or_database_access() -> None:
    modules = imported_modules(SOURCE / "bootstrap.py")
    assert any(has_prefix(module, "attendance_lab.application") for module in modules)
    assert any(has_prefix(module, "attendance_lab.infrastructure") for module in modules)
    assert any(has_prefix(module, "attendance_lab.presentation") for module in modules)
    assert not any(
        has_prefix(module, prefix) for module in modules for prefix in ("sqlite3", "smartcard")
    )
