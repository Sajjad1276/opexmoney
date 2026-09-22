from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HANDLERS_DIR = ROOT / "app" / "handlers"
KEYBOARDS_DIR = ROOT / "app" / "keyboards"
STATES_DIR = ROOT / "app" / "states"
MAIN_FILE = ROOT / "main.py"

EXPECTED_HANDLER_MODULES = {
    "academy",
    "chart",
    "founder",
    "governance",
    "market",
    "membership",
    "missions",
    "nation",
    "nation_management",
    "onboarding",
    "portfolio",
    "ranking",
    "sections",
    "settings",
    "start_flow",
    "support",
    "start",
    "treasury",
}

_DYNAMIC_VALUE_HINTS = {
    "side": ("buy", "sell"),
    "choice": ("for", "against", "abstain"),
    "status": ("active", "pending"),
    "role": ("founder", "minister", "trader", "citizen"),
}


@dataclass
class FlowHealthReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if p.is_file())


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.AST:
    return ast.parse(_read(path), filename=str(path))


def _literal_strings(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            values.add(item.value)
    return values


def _dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _callback_button_specs() -> list[tuple[str, str, Path]]:
    specs: list[tuple[str, str, Path]] = []
    # Scan every location that can build an inline button. Buttons declared\n    # inside handlers are just as real as keyboard-module buttons.\n    for path in _files(KEYBOARDS_DIR) + _files(HANDLERS_DIR):\n        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _dotted(node.func) != "InlineKeyboardButton":
                continue
            callback = next(
                (kw.value for kw in node.keywords if kw.arg == "callback_data"),
                None,
            )
            if isinstance(callback, ast.Constant) and isinstance(callback.value, str):
                specs.append(("exact", callback.value, path))
            elif isinstance(callback, ast.JoinedStr):
                parts: list[str] = []
                for part in callback.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        parts.append(part.value)
                    elif isinstance(part, ast.FormattedValue):
                        expression = _dotted(part.value) or "value"
                        parts.append("{" + expression + "}")
                literal = "".join(parts)
                specs.append(("dynamic:" + literal, literal, path))
    return specs


def _callback_handler_specs() -> list[tuple[str, str, Path]]:
    specs: list[tuple[str, str, Path]] = []

    def _collect_filters(filter_nodes: list[ast.AST], path: Path) -> None:
        for filter_node in filter_nodes:
            if isinstance(filter_node, ast.Compare) and len(filter_node.ops) == 1:
                if _dotted(filter_node.left) != "F.data" or not isinstance(
                    filter_node.comparators[0], ast.Constant
                ):
                    continue
                value = filter_node.comparators[0].value
                if isinstance(value, str) and isinstance(filter_node.ops[0], ast.Eq):
                    specs.append(("exact", value, path))
                continue

            if not isinstance(filter_node, ast.Call):
                continue
            func = _dotted(filter_node.func)
            if func not in {
                "F.data.regexp",
                "F.data.startswith",
                "F.data.in_",
            }:
                continue
            if not filter_node.args:
                continue
            arg = filter_node.args[0]
            kind = func.split(".")[-1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                specs.append((kind, arg.value, path))
            elif isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
                for element in arg.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        specs.append(("exact", element.value, path))

    for path in _files(HANDLERS_DIR) + [ROOT / "ai" / "__init__.py"]:
        if not path.exists():
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted(node.func)
            if dotted.endswith(".callback_query"):
                _collect_filters(list(node.args), path)
            elif dotted.endswith(".callback_query.register"):
                # Telegram callback handlers registered with
                # router.callback_query.register(handler, filter, ...).
                _collect_filters(list(node.args[1:]), path)
    return specs
t