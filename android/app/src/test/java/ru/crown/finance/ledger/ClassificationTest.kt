package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.io.*

class ClassificationTest {
    private val food = Category("food", "Продукты", OperationKind.Expense)
    private val home = Category("home", "Дом", OperationKind.Expense)
    private val categories = listOf(food, home)
    private val rule = CategoryRule("one", "Магазин", OperationKind.Expense, "магазин", food.id, 10)
    private fun suggest(rules: List<CategoryRule>, account: String = "main", manual: String? = null, parts: Boolean = false) =
        suggestCategory(OperationKind.Expense, account, "УЧЕБНЫЙ МАГАЗИН", rules, categories, manual, parts)

    @Test fun priorityScopeAndDisabledRulesAreRespected() {
        val specific = rule.copy(id = "two", name = "Другой", categoryId = home.id, priority = 20, accountId = "second")
        assertEquals(food.id, suggest(listOf(rule, specific)).categoryId)
        assertEquals(home.id, suggest(listOf(rule, specific), "second").categoryId)
        assertEquals(food.id, suggest(listOf(rule, specific.copy(enabled = false)), "second").categoryId)
        assertEquals(SuggestionStatus.NoMatch, suggest(emptyList()).status)
    }

    @Test fun equalPriorityConflictsOnlyForDifferentCategories() {
        val other = rule.copy(id = "two", name = "Второй", categoryId = home.id)
        assertEquals(CategorySuggestion(SuggestionStatus.Conflict, ruleIds = listOf("one", "two"), priority = 10), suggest(listOf(other, rule)))
        val same = other.copy(categoryId = food.id)
        assertEquals(food.id, suggest(listOf(same, rule)).categoryId)
    }

    @Test fun manualChoicesAndUnsupportedMovementsArePreserved() {
        assertEquals(SuggestionStatus.Manual, suggest(listOf(rule), manual = home.id).status)
        assertEquals(SuggestionStatus.Manual, suggest(listOf(rule), parts = true).status)
        for (kind in listOf(OperationKind.Opening, OperationKind.Transfer, OperationKind.Refund)) {
            assertEquals(SuggestionStatus.Unsupported, suggestCategory(kind, "main", "магазин", listOf(rule), categories).status)
        }
    }

    @Test fun historyCodecAndLinksAreStrict() {
        val event = rule.event(1).copy(sequence = 1)
        val disabled = rule.copy(enabled = false).event(2).copy(sequence = 2)
        val output = ByteArrayOutputStream()
        DataOutputStream(output).use { it.ruleEvents(listOf(event, disabled)) }
        val decoded = DataInputStream(ByteArrayInputStream(output.toByteArray())).ruleEvents()
        assertEquals(listOf(event, disabled), decoded)
        validateRuleEvents(decoded, setOf("main"), categories)
        assertEquals(listOf(disabled), currentRules(decoded))
        assertThrows(IllegalArgumentException::class.java) { validateRuleEvents(decoded, setOf("main"), emptyList()) }
        assertThrows(IllegalArgumentException::class.java) { validateRuleEvents(listOf(event, event), setOf("main"), categories) }
    }

    @Test fun invalidConditionsAndCategoryKindAreRejected() {
        assertThrows(IllegalArgumentException::class.java) { rule.copy(priority = 1000) }
        assertThrows(IllegalArgumentException::class.java) { rule.copy(contains = " ") }
        assertThrows(IllegalArgumentException::class.java) { rule.copy(contains = "a\nb") }
        assertThrows(IllegalArgumentException::class.java) { rule.copy(kind = OperationKind.Transfer) }
        assertThrows(IllegalArgumentException::class.java) { suggest(listOf(rule, rule)) }
        assertThrows(IllegalArgumentException::class.java) {
            suggestCategory(OperationKind.Expense, "main", "магазин", listOf(rule), listOf(food.copy(kind = OperationKind.Income)))
        }
    }

    @Test fun statementSuggestionsKeepManualAndDuplicateReviewFlags() {
        val operation = Operation("op", OperationKind.Expense, LocalDate.of(2026, 11, 22), "main", 725, "Магазин")
        val choices = listOf(
            ImportChoice(1, operation), ImportChoice(2, operation.copy(id = "manual", categoryId = home.id)),
            ImportChoice(3, operation.copy(id = "reviewed"), reviewed = true),
            ImportChoice(4, operation.copy(id = "linked"), linkExisting = true),
        )
        val result = classifyStatement(choices, listOf(rule), categories)
        assertEquals(1, result.suggested)
        assertEquals(food.id, result.choices[0].operation.categoryId)
        assertFalse(result.choices[0].reviewed)
        assertEquals(choices.drop(1), result.choices.drop(1))
        val conflicting = classifyStatement(choices, listOf(rule, rule.copy(id = "two", name = "Другой", categoryId = home.id)), categories)
        assertEquals(setOf(1), conflicting.conflicts)
        assertEquals(choices, conflicting.choices)
    }
}
