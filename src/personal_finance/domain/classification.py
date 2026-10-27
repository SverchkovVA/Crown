"""Объяснимые подсказки категории; не создают и не изменяют финансовые факты."""

from dataclasses import dataclass
from enum import StrEnum

from personal_finance.domain.errors import ValidationError
from personal_finance.domain.ledger import OperationKind, OperationPart, require_identifier
from personal_finance.domain.models import Category, CategoryKind, validate_name

MAX_CATEGORY_RULES = 1000


@dataclass(frozen=True, slots=True)
class CategoryRule:
    id: str
    name: str
    kind: OperationKind
    contains: str
    category_id: str
    priority: int = 0
    account_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        require_identifier(self.id)
        require_identifier(self.category_id)
        if self.account_id is not None:
            require_identifier(self.account_id)
        if not isinstance(self.name, str):
            raise ValidationError("Укажите название правила.")
        object.__setattr__(self, "name", validate_name(self.name))
        if not isinstance(self.kind, OperationKind) or self.kind not in (
            OperationKind.EXPENSE,
            OperationKind.INCOME,
        ):
            raise ValidationError("Правило категории предназначено для дохода или расхода.")
        if (
            not isinstance(self.contains, str)
            or not self.contains.strip()
            or len(self.contains) > 200
            or any(ord(c) < 32 for c in self.contains)
        ):
            raise ValidationError("Текст правила: от 1 до 200 печатных символов.")
        object.__setattr__(self, "contains", self.contains.strip())
        if type(self.priority) is not int or not 0 <= self.priority <= 999:
            raise ValidationError("Приоритет правила должен быть целым от 0 до 999.")
        if type(self.enabled) is not bool:
            raise ValidationError("Некорректный признак включения правила.")


@dataclass(frozen=True, slots=True)
class CategoryRuleVersion:
    rule: CategoryRule
    revision_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    kind: OperationKind
    account_id: str
    text: str
    manual_category_id: str | None = None
    manual_parts: tuple[OperationPart, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OperationKind):
            raise ValidationError("Неизвестный вид операции для подсказки категории.")
        require_identifier(self.account_id)
        if self.manual_category_id is not None:
            require_identifier(self.manual_category_id)
        if (
            not isinstance(self.text, str)
            or len(self.text) > 500
            or any(ord(c) < 32 and c != "\n" for c in self.text)
        ):
            raise ValidationError("Текст для подсказки должен содержать не более 500 символов.")
        if (
            not isinstance(self.manual_parts, tuple)
            or len(self.manual_parts) > 100
            or any(not isinstance(p, OperationPart) for p in self.manual_parts)
            or (self.manual_category_id is not None and self.manual_parts)
        ):
            raise ValidationError("Укажите ручную категорию либо части операции.")


class ClassificationStatus(StrEnum):
    SUGGESTED = "suggested"
    MANUAL = "manual"
    CONFLICT = "conflict"
    NO_MATCH = "no_match"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CategorySuggestion:
    status: ClassificationStatus
    category_id: str | None = None
    rule_ids: tuple[str, ...] = ()
    priority: int | None = None


def suggest_category(
    value: ClassificationInput, rules: tuple[CategoryRule, ...], categories: tuple[Category, ...]
) -> CategorySuggestion:
    """Больше приоритет — раньше; равный приоритет разных категорий требует выбора.

    Сопоставление — буквальная подстрока casefold, без regex/сетевых сервисов.
    Результат используется только в предпросмотре; ручной выбор не заменяется.
    """
    if not isinstance(value, ClassificationInput):
        raise ValidationError("Некорректные сведения для подсказки категории.")
    if value.manual_category_id is not None or value.manual_parts:
        return CategorySuggestion(ClassificationStatus.MANUAL, value.manual_category_id)
    if value.kind not in (OperationKind.INCOME, OperationKind.EXPENSE):
        return CategorySuggestion(ClassificationStatus.UNSUPPORTED)
    if (
        not isinstance(rules, tuple)
        or len(rules) > MAX_CATEGORY_RULES
        or any(not isinstance(r, CategoryRule) for r in rules)
        or len({r.id for r in rules}) != len(rules)
    ):
        raise ValidationError("Некорректный набор правил категории.")
    if not isinstance(categories, tuple) or any(not isinstance(c, Category) for c in categories):
        raise ValidationError("Некорректный справочник категорий.")
    available = {c.id: c for c in categories}
    if len(available) != len(categories):
        raise ValidationError("Категория повторяется в справочнике.")
    matches = []
    normalized_text = value.text.casefold()
    for rule in rules:
        if (
            not rule.enabled
            or rule.kind != value.kind
            or (rule.account_id is not None and rule.account_id != value.account_id)
            or rule.contains.casefold() not in normalized_text
        ):
            continue
        category = available.get(rule.category_id)
        if category is None or category.kind != CategoryKind(rule.kind.value):
            raise ValidationError("Категория совпавшего правила отсутствует или имеет другой вид.")
        matches.append(rule)
    if not matches:
        return CategorySuggestion(ClassificationStatus.NO_MATCH)
    priority = max(r.priority for r in matches)
    winners = tuple(sorted((r for r in matches if r.priority == priority), key=lambda r: r.id))
    identifiers = tuple(r.id for r in winners)
    if len({r.category_id for r in winners}) > 1:
        return CategorySuggestion(
            ClassificationStatus.CONFLICT, rule_ids=identifiers, priority=priority
        )
    return CategorySuggestion(
        ClassificationStatus.SUGGESTED, winners[0].category_id, identifiers, priority
    )


@dataclass(frozen=True, slots=True)
class ClassificationSnapshot:
    rules: tuple[CategoryRule, ...]
    categories: tuple[Category, ...]

    def suggest(self, value: ClassificationInput) -> CategorySuggestion:
        return suggest_category(value, self.rules, self.categories)
