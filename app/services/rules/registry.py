from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from config import settings


@dataclass(frozen=True)
class RuleDefinition:
    key: str
    title_fa: str
    value_type: str
    min_value: Decimal | int | bool
    max_value: Decimal | int | bool
    default_value: Decimal | int | bool
    target_scope: str


RULE_REGISTRY: dict[str, RuleDefinition] = {
    "tax.wealth_rate": RuleDefinition(
        key="tax.wealth_rate",
        title_fa="نرخ مالیات ثروت",
        value_type="percent",
        min_value=Decimal("0"),
        max_value=Decimal("25"),
        default_value=Decimal("0"),
        target_scope="global",
    ),
    "tax.wealth_threshold": RuleDefinition(
        key="tax.wealth_threshold",
        title_fa="آستانه ثروت مشمول مالیات",
        value_type="int",
        min_value=0,
        max_value=10_000_000,
        default_value=5000,
        target_scope="global",
    ),
    "trade.export_tariff": RuleDefinition(
        key="trade.export_tariff",
        title_fa="تعرفه صادرات",
        value_type="percent",
        min_value=Decimal("0"),
        max_value=Decimal("30"),
        default_value=Decimal("0"),
        target_scope="global",
    ),
    "trade.import_tariff": RuleDefinition(
        key="trade.import_tariff",
        title_fa="تعرفه واردات",
        value_type="percent",
        min_value=Decimal("0"),
        max_value=Decimal("30"),
        default_value=Decimal("0"),
        target_scope="global",
    ),
    "newbie.grant_amount": RuleDefinition(
        key="newbie.grant_amount",
        title_fa="هدیه تازه‌واردها",
        value_type="int",
        min_value=0,
        max_value=500,
        default_value=0,
        target_scope="global",
    ),
    "market.tx_fee": RuleDefinition(
        key="market.tx_fee",
        title_fa="کارمزد معامله بازار",
        value_type="percent",
        min_value=Decimal("0"),
        max_value=Decimal("10"),
        default_value=Decimal("0.5"),
        target_scope="global",
    ),
    "rate.volatility_multiplier": RuleDefinition(
        key="rate.volatility_multiplier",
        title_fa="ضریب نوسان نرخ ملت",
        value_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("3.0"),
        default_value=Decimal("1.0"),
        target_scope="nation",
    ),
}


def get_rule(key: str) -> RuleDefinition:
    try:
        return RULE_REGISTRY[key]
    except KeyError as exc:
        raise ValueError("قانون اقتصادی مجاز نیست.") from exc


def _coerce(value: Any, rule: RuleDefinition) -> Decimal | int | bool:
    if rule.value_type == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in {"1", "true", "yes", "on", "بله", "روشن"}:
            return True
        if str(value).strip().lower() in {"0", "false", "no", "off", "خیر", "خاموش"}:
            return False
        raise ValueError("مقدار بولی معتبر نیست.")

    if rule.value_type == "int":
        decimal_value = Decimal(str(value))
        if decimal_value != decimal_value.to_integral_value():
            raise ValueError("این قانون فقط عدد صحیح می‌پذیره.")
        return int(decimal_value)

    decimal_value = Decimal(str(value))
    return decimal_value


def clamp_rule_value(value: Any, rule: RuleDefinition) -> Decimal | int | bool:
    typed = _coerce(value, rule)
    if rule.value_type == "bool":
        return typed

    minimum = Decimal(str(rule.min_value))
    maximum = Decimal(str(rule.max_value))
    result = Decimal(str(typed))
    result = max(minimum, min(maximum, result))

    if rule.value_type == "int":
        return int(result)
    return result


def parse_rule_input(raw: str, rule: RuleDefinition) -> Decimal | int | bool:
    text = raw.strip()
    if not text:
        raise ValueError("مقدار قانون رو وارد کن.")

    if rule.value_type == "percent":
        text = text.replace("٪", "").replace("%", "").replace(",", ".")
    elif rule.value_type in {"decimal", "duration_hours"}:
        text = text.replace(",", ".")

    return clamp_rule_value(text, rule)


def validate_registry() -> list[str]:
    errors: list[str] = []
    allowed_types = {"percent", "int", "bool", "duration_hours", "decimal"}
    allowed_scopes = {"global", "nation", "player"}

    if len(RULE_REGISTRY) != len(set(RULE_REGISTRY)):
        errors.append("کلیدهای رجیستری تکراری هستند.")

    for key, rule in RULE_REGISTRY.items():
        if key != rule.key:
            errors.append(f"کلید ناهماهنگ: {key}")
        if rule.value_type not in allowed_types:
            errors.append(f"نوع مقدار نامعتبر: {key}")
        if rule.target_scope not in allowed_scopes:
            errors.append(f"scope نامعتبر: {key}")

        if rule.value_type != "bool":
            minimum = Decimal(str(rule.min_value))
            maximum = Decimal(str(rule.max_value))
            default = Decimal(str(rule.default_value))
            if minimum > maximum:
                errors.append(f"بازه نامعتبر: {key}")
            if default < minimum or default > maximum:
                errors.append(f"default خارج از بازه: {key}")
        elif not isinstance(rule.default_value, bool):
            errors.append(f"default بولی نامعتبر: {key}")

    if settings.governance_max_active_rules < 1:
        errors.append("MAX_ACTIVE_RULES باید حداقل ۱ باشد.")
    if not (Decimal("0") < settings.governance_proposal_top_percent <= Decimal("100")):
        errors.append("درصد واجدین پیشنهاد نامعتبر است.")
    if settings.governance_min_quorum_weight <= 0:
        errors.append("کوروم باید مثبت باشد.")

    return errors
