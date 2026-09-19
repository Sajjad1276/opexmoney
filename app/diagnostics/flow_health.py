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
    "founder",
    "governance",
    "market",
    "nation",
    "nation_management",
    "onboarding_fix",
    "sections",
    "start",
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
    for path in _files(KEYBOARDS_DIR):
        tree = _parse(path)
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
                literal = "".join(
                    part.value
                    for part in callback.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                specs.append(("dynamic:" + literal, literal, path))
    return specs


def _callback_handler_specs() -> list[tuple[str, str, Path]]:
    specs: list[tuple[str, str, Path]] = []
    for path in _files(HANDLERS_DIR) + [ROOT / "ai" / "__init__.py"]:
        if not path.exists():
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _dotted(node.func).endswith(".callback_query"):
                continue
            for filter_node in node.args:
                if isinstance(filter_node, ast.Compare) and len(filter_node.ops) == 1:
                    if _dotted(filter_node.left) == "F.data" and isinstance(
                        filter_node.comparators[0], ast.Constant
                    ):
                        value = filter_node.comparators[0].value
                        if isinstance(value, str) and isinstance(filter_node.ops[0], ast.Eq):
                            specs.append(("exact", value, path))
                if isinstance(filter_node, ast.Call):
                    func = _dotted(filter_node.func)
                    if func in {"F.data.regexp", "F.data.startswith", "F.data.in_"}:
                        if filter_node.args:
                            arg = filter_node.args[0]
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                specs.append((func.split(".")[-1], arg.value, path))
                            elif isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
                                for element in arg.elts:
                                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                                        specs.append(("exact", element.value, path))
    return specs


def _reply_button_texts() -> set[str]:
    values: set[str] = set()
    for path in _files(KEYBOARDS_DIR):
        if path.name != "reply.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _dotted(node.func) != "KeyboardButton":
                continue
            text_kw = next((kw.value for kw in node.keywords if kw.arg == "text"), None)
            if isinstance(text_kw, ast.Constant) and isinstance(text_kw.value, str):
                values.add(text_kw.value)
    return values


def _reply_handler_texts() -> set[str]:
    values: set[str] = set()
    for path in _files(HANDLERS_DIR):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                if _dotted(node.left) == "F.text" and isinstance(node.comparators[0], ast.Constant):
                    if isinstance(node.comparators[0].value, str):
                        values.add(node.comparators[0].value)
            elif isinstance(node, ast.Call):
                func = _dotted(node.func)
                if func in {"F.text.in_", "F.text.casefold", "F.text.func"}:
                    values.update(_literal_strings(node))
    return values


def _state_names() -> set[str]:
    names: set[str] = set()
    for path in _files(STATES_DIR):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call):
                        if _dotted(item.value.func) == "State":
                            for target in item.targets:
                                if isinstance(target, ast.Name):
                                    names.add(target.id)
    return names


def _state_mentions() -> set[str]:
    mentions: set[str] = set()
    for path in _files(HANDLERS_DIR):
        text = _read(path)
        for state in _state_names():
            if re.search(rf"\b{re.escape(state)}\b", text):
                mentions.add(state)
    return mentions


def _registered_router_modules() -> set[str]:
    tree = _parse(MAIN_FILE)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) == "dp.include_router":
            if node.args and isinstance(node.args[0], ast.Name):
                result.add(node.args[0].id.replace("_router", ""))
    return result


def _regex_matches(pattern: str, value: str) -> bool:
    try:
        return re.fullmatch(pattern, value) is not None
    except re.error:
        return False


def _dynamic_candidates(template: str) -> list[str]:
    candidates = [template + "1"]
    return candidates


def run_flow_health_test() -> FlowHealthReport:
    report = FlowHealthReport()

    handler_modules = {p.stem for p in _files(HANDLERS_DIR) if p.name != "__init__.py"}
    missing_registration = sorted(
        (handler_modules & EXPECTED_HANDLER_MODULES) - _registered_router_modules()
    )
    if missing_registration:
        report.errors.append(
            "Unregistered handler routers: " + ", ".join(missing_registration)
        )

    unexpected = sorted(_registered_router_modules() - EXPECTED_HANDLER_MODULES)
    if unexpected:
        report.warnings.append(
            "Router registrations not in health registry: " + ", ".join(unexpected)
        )

    button_specs = _callback_button_specs()
    handler_specs = _callback_handler_specs()
    exact_handlers = {
        value for kind, value, _ in handler_specs if kind == "exact"
    }
    regex_handlers = [
        (kind, value)
        for kind, value, _ in handler_specs
        if kind in {"regexp", "startswith"}
    ]

    duplicate_exact: dict[str, list[str]] = {}
    for kind, value, path in handler_specs:
        if kind == "exact":
            duplicate_exact.setdefault(value, []).append(str(path.relative_to(ROOT)))
    duplicated = {
        value: paths
        for value, paths in duplicate_exact.items()
        if len(paths) > 1
    }
    for value, paths in sorted(duplicated.items()):
        report.errors.append(
            f"Duplicate callback handler '{value}' in: " + ", ".join(paths)
        )

    orphan_buttons: list[str] = []
    dynamic_buttons = 0
    for kind, value, path in button_specs:
        if kind == "exact":
            covered = value in exact_handlers or any(
                pattern == value
                or (pattern.startswith("^") and _regex_matches(pattern, value))
                or (handler_kind == "startswith" and value.startswith(pattern))
                for handler_kind, pattern in regex_handlers
            )
            if not covered:
                orphan_buttons.append(
                    f"{path.relative_to(ROOT)} -> {value}"
                )
        else:
            dynamic_buttons += 1
            literal_prefix = value.split("{", 1)[0]
            # F-strings such as buyq_100_{nation_id} are concrete variants
            # of a handler family like ^buyq_(\\d+|all)_\\d+$.
            family_prefix = re.split(r"\\d", literal_prefix, maxsplit=1)[0]
            if family_prefix and not any(
                (handler_kind == "startswith" and pattern.startswith(family_prefix))
                or (handler_kind == "regexp" and family_prefix in pattern)
                or (handler_kind == "exact" and pattern.startswith(family_prefix))
                for handler_kind, pattern in handler_specs
            ):
                orphan_buttons.append(
                    f"{path.relative_to(ROOT)} -> dynamic:{value}"
                )

    if orphan_buttons:
        report.errors.extend(
            "Button without matching callback handler: " + item
            for item in orphan_buttons
        )

    reply_buttons = _reply_button_texts()
    reply_handlers = _reply_handler_texts()
    orphan_reply = sorted(reply_buttons - reply_handlers)
    if orphan_reply:
        report.errors.extend(
            "Reply button without matching message handler: " + item
            for item in orphan_reply
        )

    states = _state_names()
    mentions = _state_mentions()
    unused_states = sorted(states - mentions)
    if unused_states:
        report.warnings.extend(
            "State has no handler-source mention: " + state
            for state in unused_states
        )

    for path in _files(HANDLERS_DIR) + [ROOT / "ai" / "__init__.py"]:
        if path.exists():
            try:
                _parse(path)
            except SyntaxError as exc:
                report.errors.append(
                    f"Syntax error in {path.relative_to(ROOT)}: {exc}"
                )

    report.metrics = {
        "handler_modules": len(handler_modules),
        "registered_routers": len(_registered_router_modules()),
        "callback_buttons": len(button_specs),
        "dynamic_callback_buttons": dynamic_buttons,
        "callback_handlers": len(handler_specs),
        "reply_buttons": len(reply_buttons),
        "reply_handlers": len(reply_handlers),
        "states": len(states),
        "state_mentions": len(mentions),
    }
    return report


def assert_flow_health() -> FlowHealthReport:
    report = run_flow_health_test()
    if not report.ok:
        details = "\n".join(f"- {error}" for error in report.errors)
        raise AssertionError("FLOW_HEALTH_FAILED\n" + details)
    return report
