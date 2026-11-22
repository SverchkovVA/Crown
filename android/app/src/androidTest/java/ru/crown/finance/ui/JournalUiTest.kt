package ru.crown.finance.ui

import android.os.Build
import androidx.test.platform.app.InstrumentationRegistry
import android.graphics.Bitmap
import android.view.WindowManager
import androidx.activity.compose.setContent
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import ru.crown.finance.DemoState
import ru.crown.finance.MainActivity
import ru.crown.finance.ledger.*
import java.io.File
import java.time.LocalDate
import java.time.YearMonth

@RunWith(AndroidJUnit4::class)
class JournalUiTest {
    @get:Rule val rule = createAndroidComposeRule<MainActivity>()
    private val date = LocalDate.of(2026, 11, 15)
    private val food = Category("food", "Продукты", OperationKind.Expense)
    private val home = Category("home", "Дом", OperationKind.Expense)
    private val purchase = Operation(
        "buy", OperationKind.Expense, date, "main", 1_000, "Учебная покупка",
        parts = listOf(Allocation(food.id, 600), Allocation(home.id, 400)),
    )
    private val snapshot = LedgerSnapshot.from(
        YearMonth.from(date), listOf(Account("main", "Основной")), listOf(purchase),
    ).copy(categories = listOf(food, home), history = listOf(OperationHistory(listOf(Revision(purchase, null)))))

    private fun commands(
        reconcileAction: (ReconciliationPreview, Long, () -> Unit) -> Unit = { _, _, _ -> },
        importAction: (String, List<ImportChoice>) -> Unit = { _, _ -> },
        bankAction: (String, () -> Unit) -> Unit = { _, _ -> },
        accountAction: (String, Long, LocalDate, String?, AccountKind, () -> Unit) -> Unit = { _, _, _, _, _, _ -> },
        categoryAction: (String, OperationKind, String?, () -> Unit) -> Unit = { _, _, _, _ -> },
        tagAction: (String, TagEvent?, Boolean, () -> Unit) -> Unit = { _, _, _, _ -> },
        assignAction: (String, Set<String>, Set<String>, () -> Unit) -> Unit = { _, _, _, _ -> },
        filterAction: (String, JournalQuery, FilterEvent?, Boolean, () -> Unit) -> Unit = { _, _, _, _, _ -> },
        ruleAction: (CategoryRule, CategoryRuleEvent?, () -> Unit) -> Unit = { _, _, _ -> },
        depositAction: (String, String, DepositScenario, DepositEvent?, Boolean, () -> Unit) -> Unit = { _, _, _, _, _, _ -> },
        passwordAction: (CharArray, CharArray, () -> Unit) -> Unit = { first, second, _ -> first.fill(' '); second.fill(' ') },
        bonusAction: (BonusEntryEvent, BonusEntryEvent?, () -> Unit) -> Unit = { _, _, _ -> },
        closeAction: (YearMonth, Boolean, String) -> Unit = { _, _, _ -> },
        accept: (Operation) -> Unit = {},
    ) = BookCommands(
        busy = false, error = null, clearError = {}, changeMonth = {}, addAccount = accountAction,
        addOperation = { value, _ -> accept(value) }, reviseOperation = { _, _, _ -> },
        backup = {}, export = { _, _ -> }, addCategory = categoryAction, inspectBackup = {},
        setPeriodClosed = closeAction, reconcile = reconcileAction,
        chooseStatement = {}, importStatement = importAction, addBank = bankAction,
        saveTag = tagAction, assignTags = assignAction, saveFilter = filterAction, saveRule = ruleAction,
        saveBonusProgram = { _, _, _, _ -> }, saveBonusEntry = bonusAction, changePassword = passwordAction, saveDeposit = depositAction,
    )

    @Test
    fun purchaseOpensRefundAndSaveRemainsVisible() {
        var recorded: Operation? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent {
                CrownTheme { FinanceScreen(DemoState.Ready(snapshot), {}, "Учебная", {}, commands { recorded = it }) }
            }
        }
        rule.onNodeWithTag("operations-list").performScrollToNode(hasText("Учебная покупка"))
        rule.onNodeWithText("Учебная покупка").performClick()
        rule.onNodeWithText("Оформить возврат").assertIsDisplayed().performClick()
        rule.onNodeWithText("Сохранить возврат").assertIsDisplayed()
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("7.00")
        rule.onNodeWithText("Сохранить возврат").performClick()
        rule.onNodeWithText("Сумма возврата превышает доступный остаток").assertIsDisplayed()
        assertNull(recorded)
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("1.50")
        rule.onNodeWithText("Сумма возврата превышает доступный остаток").assertDoesNotExist()
        saveImage("refund-form")
        rule.onNodeWithText("Сохранить возврат").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertEquals(550L, recorded?.amountMinor)
            assertEquals(OperationKind.Refund, recorded?.kind)
            assertEquals(purchase.fingerprint(), recorded?.refundVersion)
        }
    }

    @Test
    fun splitSumMustMatchAndSaveRemainsVisible() {
        var recorded: Operation? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent {
                CrownTheme { OperationEditor(snapshot.balances, snapshot.categories, snapshot.month, null, commands { recorded = it }, {}) }
            }
        }
        rule.onNode(hasText("Сумма, ₽") and hasSetTextAction()).performTextReplacement("10.00")
        rule.onNodeWithContentDescription("Разделить по категориям").performScrollTo().performClick()
        rule.onNodeWithText("Выбрать: категория части 1").performScrollTo().performClick()
        rule.onNodeWithText("Продукты").performClick()
        rule.onNodeWithText("Выбрать: категория части 2").performScrollTo().performClick()
        rule.onNodeWithText("Дом").performClick()
        rule.onNode(hasText("Сумма части 1, ₽") and hasSetTextAction()).performScrollTo().performTextReplacement("6.00")
        rule.onNode(hasText("Сумма части 2, ₽") and hasSetTextAction()).performScrollTo().performTextReplacement("5.00")
        rule.onNodeWithText("Сохранить операцию").assertIsDisplayed().performClick()
        rule.onNodeWithText("Сумма частей должна равняться сумме операции").assertIsDisplayed()
        assertNull(recorded)
        rule.onNode(hasText("Сумма части 2, ₽") and hasSetTextAction()).performTextReplacement("4.00")
        rule.onNodeWithText("Сумма частей должна равняться сумме операции").assertDoesNotExist()
        saveImage("split-form")
        rule.onNodeWithText("Сохранить операцию").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(listOf(Allocation(food.id, 600), Allocation(home.id, 400)), recorded?.parts) }
    }

    @Test
    fun reconciliationUsesChosenDateAndKeepsSaveVisible() {
        var saved: ReconciliationPreview? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent {
                CrownTheme {
                    ReconciliationEditor(snapshot, commands(reconcileAction = { preview, _, _ -> saved = preview }), {})
                }
            }
        }
        rule.onNode(hasText("Дата сверки · ДД.ММ.ГГГГ") and hasSetTextAction()).performTextReplacement("14.11.2026")
        rule.onNode(hasText("Остаток в банке, ₽") and hasSetTextAction()).performTextReplacement("0.00")
        rule.onNodeWithText("Остатки совпадают").performScrollTo().assertIsDisplayed()
        saveImage("reconciliation-form")
        rule.onNodeWithText("Сохранить сверку").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertEquals(date.minusDays(1), saved?.date)
            assertEquals(0.toBigInteger(), saved?.calculatedMinor)
        }
    }

    @Test
    fun reopeningMonthRequiresReasonAndConfirmation() {
        var opened = false
        val locked = snapshot.copy(periods = listOf(PeriodEvent(
            1, snapshot.month.toString(), true, "", 1,
        )))
        rule.activity.runOnUiThread {
            rule.activity.setContent {
                CrownTheme {
                    PeriodsPage(locked, commands(closeAction = { month, closed, reason ->
                        opened = month == snapshot.month && !closed && reason == "Уточнение"
                    }))
                }
            }
        }
        rule.onNodeWithText("Открыть месяц").assertIsNotEnabled()
        rule.onNode(hasText("Причина открытия месяца") and hasSetTextAction()).performTextReplacement("Уточнение")
        rule.onNodeWithText("Открыть месяц").performClick()
        assertFalse(opened)
        rule.onNodeWithText("Подтвердить").performClick()
        rule.runOnIdle { assertTrue(opened) }
    }

    @Test
    fun statementPreviewExcludesBadDateAndRequiresConfirmation() {
        var selected: List<ImportChoice>? = null
        val document = parseStatement(
            "date;amount;id\n2026-11-20;120.00;one\n2026-11-20;-200.00;two\n2026-11-31;5;bad".toByteArray(),
        )
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent {
                CrownTheme { StatementReview(document, snapshot, commands(importAction = { _, values -> selected = values }), {}) }
            }
        }
        rule.onNodeWithText("Добавить операции (2)").assertIsNotEnabled()
        rule.onNodeWithTag("statement-rows").performScrollToNode(hasText("Строка 3 исключена", substring = true))
        rule.onNodeWithText("Строка 3 исключена", substring = true).assertIsDisplayed()
        saveImage("statement-preview", editor = false)
        rule.onNodeWithContentDescription("Подтвердить строки выписки").performClick()
        rule.onNodeWithText("Добавить операции (2)").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertEquals(2, selected?.size)
            assertEquals(setOf(OperationKind.Income, OperationKind.Expense), selected?.map { it.operation.kind }?.toSet())
        }
    }

    @Test
    fun statementRefundIsLinkedToPurchaseBeforeTheBatchIsWritten() {
        var written: List<ImportChoice>? = null
        val document = parseStatement(
            "date;amount;description;id\n2026-11-20;2.00;Возврат покупки;return-one".toByteArray(),
        )
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent {
                CrownTheme { StatementReview(document, snapshot, commands(importAction = { _, rows -> written = rows }), {}) }
            }
        }
        rule.onNodeWithText("Добавить операции (0)").assertIsNotEnabled()
        rule.onNodeWithTag("statement-rows").performScrollToNode(hasText("Оформить возврат строки 1"))
        rule.onNodeWithText("Оформить возврат строки 1").performScrollTo().performClick()
        rule.onNodeWithText("Выбрать: покупка").performScrollTo().performClick()
        rule.onNodeWithText("Учебная покупка", substring = true).performClick()
        rule.onNodeWithText("Продолжить").performClick()
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("2.00")
        rule.onNodeWithText("Сохранить возврат").performClick()
        rule.onNodeWithText("Добавить операции (1)").assertIsNotEnabled()
        assertNull(written)
        rule.onNodeWithTag("statement-rows").performScrollToNode(hasText("Настроить строку 1"))
        rule.onNodeWithTag("statement-rows").performScrollToNode(hasText("Настроить строку 1"))
        rule.onNodeWithText("Настроить строку 1").performScrollTo().performClick()
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("1.50")
        rule.onNodeWithText("Сохранить возврат").performClick()
        assertNull(written)
        rule.onNodeWithText("20.11.2026 · +1,50\u00A0₽").assertIsDisplayed()
        saveImage("statement-refund", editor = false)
        rule.onNodeWithContentDescription("Подтвердить строки выписки").performClick()
        rule.onNodeWithText("Добавить операции (1)").performClick()
        rule.runOnIdle {
            val refund = written!!.single().operation
            assertEquals(OperationKind.Refund, refund.kind)
            assertEquals(150L, refund.amountMinor)
            assertEquals(purchase.id, refund.refundOf)
            assertEquals(purchase.fingerprint(), refund.refundVersion)
        }
    }

    @Test
    fun bankAccountAndSubcategoryFormsUseChosenReferences() {
        var bankName: String? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                BankEditor(emptyList(), commands(bankAction = { name, _ -> bankName = name }), {})
            } }
        }
        rule.onNode(hasText("Название нового банка") and hasSetTextAction()).performTextReplacement("DemoBank")
        saveImage("bank-form")
        rule.onNodeWithText("Создать банк").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals("DemoBank", bankName) }
        val bank = Bank("demo-bank", "Учебный банк")
        var accountBank: String? = null
        var accountKind: AccountKind? = null
        rule.activity.runOnUiThread {
            rule.activity.setContent { CrownTheme {
                AccountEditor(snapshot.month, listOf(bank), commands(
                    accountAction = { _, _, _, selectedBank, selectedKind, _ ->
                        accountBank = selectedBank
                        accountKind = selectedKind
                    },
                ), {})
            } }
        }
        rule.onNode(hasText("Название счёта") and hasSetTextAction()).performTextReplacement("DemoAccount")
        rule.onNodeWithText("Вид счёта: Другой счёт").performClick()
        rule.onNodeWithText("Накопительный счёт").performClick()
        rule.onNodeWithText("Выбрать: банк").performClick()
        rule.onNodeWithText("Учебный банк").performClick()
        saveImage("account-form")
        rule.onNodeWithText("Сохранить счёт").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertEquals(bank.id, accountBank)
            assertEquals(AccountKind.Saving, accountKind)
        }
        var parentId: String? = null
        rule.activity.runOnUiThread {
            rule.activity.setContent { CrownTheme {
                CategoryEditor(listOf(food), commands(categoryAction = { _, _, parent, _ -> parentId = parent }), {})
            } }
        }
        rule.onNode(hasText("Название новой категории") and hasSetTextAction()).performTextReplacement("Cafe")
        rule.onNodeWithText("Выбрать: родительская категория").performScrollTo().performClick()
        rule.onAllNodesWithText("Продукты").onLast().performClick()
        saveImage("subcategory-form")
        rule.onNodeWithText("Создать категорию").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(food.id, parentId) }
    }

    @Test
    fun numericDateInputRejectsInvalidDayAndSavesLeapDate() {
        var savedDate: LocalDate? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                AccountEditor(snapshot.month, emptyList(), commands(
                    accountAction = { _, _, day, _, _, _ -> savedDate = day },
                ), {})
            } }
        }
        rule.onNode(hasText("Название счёта") and hasSetTextAction()).performTextReplacement("DateDemo")
        rule.onNode(hasText("Дата остатка · ДД.ММ.ГГГГ") and hasSetTextAction()).performTextReplacement("31022026")
        rule.onNodeWithText("Сохранить счёт").performClick()
        rule.onNodeWithText("Введите дату в формате ДД.ММ.ГГГГ").assertIsDisplayed()
        assertNull(savedDate)
        rule.onNode(hasText("Дата остатка · ДД.ММ.ГГГГ") and hasSetTextAction()).performTextReplacement("29022024")
        saveImage("numeric-date")
        rule.onNodeWithText("Сохранить счёт").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(LocalDate.of(2024, 2, 29), savedDate) }
    }

    @Test
    fun journalSearchAndCategoryFilterCanBeCleared() {
        val opening = Operation("opening", OperationKind.Opening, date.withDayOfMonth(1), "main", -500, "Начальный долг")
        val searchSnapshot = LedgerSnapshot.from(
            snapshot.month, snapshot.balances.map { it.account }, listOf(purchase, opening),
        ).copy(categories = snapshot.categories)
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                FinanceScreen(DemoState.Ready(searchSnapshot), {}, "Учебная", {}, commands())
            } }
        }
        rule.onNodeWithText("Поиск и фильтры").performClick()
        rule.onNode(hasText("Поиск по описанию, счёту или категории") and hasSetTextAction())
            .performTextReplacement("Ничего похожего")
        rule.onNodeWithTag("operations-list").performScrollToNode(hasText("По выбранным условиям операций нет"))
        rule.onNodeWithText("По выбранным условиям операций нет").assertIsDisplayed()
        rule.onNodeWithTag("operations-list").performScrollToNode(hasText("Сбросить поиск и фильтры"))
        rule.onNodeWithText("Сбросить поиск и фильтры").performClick()
        rule.onNodeWithText("Выбрать: категория").performScrollTo().performClick()
        rule.onNodeWithText("Продукты").performClick()
        rule.onNodeWithText("Найдено операций: 1 из 2").assertIsDisplayed()
        saveImage("journal-filters", editor = false)
        rule.onNodeWithText("Сбросить поиск и фильтры").performClick()
        rule.onNodeWithText("Поиск и фильтры").performScrollTo().assertIsDisplayed()
        rule.onNodeWithTag("operations-list").performScrollToNode(hasText("Начальный долг"))
        rule.onNodeWithText("Начальный долг").assertIsDisplayed()
        rule.onNodeWithTag("operations-list").performScrollToNode(hasText("Учебная покупка"))
        rule.onNodeWithText("Учебная покупка").assertIsDisplayed()
        rule.onNodeWithContentDescription("Счета").performClick()
        rule.onNodeWithText("Сверка остатков").assertIsDisplayed()
        rule.onNodeWithContentDescription("Отчёты").performClick()
        rule.onNodeWithText("По категориям").performScrollTo().assertIsDisplayed()
        rule.onNodeWithContentDescription("Настройки").performClick()
        rule.onNodeWithText("Защита и копии").performScrollTo().assertIsDisplayed()
        rule.onNodeWithContentDescription("Операции").performClick()
        rule.onNodeWithText("Поиск и фильтры").performScrollTo().assertIsDisplayed()
        saveImage("journal-navigation", editor = false)
    }

    @Test
    fun tagArchiveAssignmentAndSavedFilterUseExplicitSelections() {
        val tag = TagEvent(1, "trip", "Поездка", false, 1)
        val filter = FilterEvent(1, "trip-filter", "Покупки поездки", "", "expense", null, "main", tag.id, false, 2)
        val organized = snapshot.copy(organization = OrganizationState(
            listOf(tag), listOf(TagAssignment(1, purchase.id, tag.id, true, 3)), listOf(filter),
        ))
        var renamed: String? = null
        var archived = false
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                OrganizationEditor(organized, commands(tagAction = { name, expected, archive, _ ->
                    assertEquals(tag, expected); renamed = name; archived = archive
                }), {})
            } }
        }
        rule.onNode(hasText("Поездка") and hasClickAction()).performScrollTo().performClick()
        rule.onNodeWithText("Сохранить изменения").assertIsDisplayed()
        rule.onNodeWithContentDescription("В архиве").assertExists()
        rule.onNode(hasText("Название тега") and hasSetTextAction()).performScrollTo().performTextReplacement("Отпуск")
        rule.onNode(hasText("Название тега") and hasSetTextAction()).performImeAction()
        rule.waitForIdle()
        rule.onNodeWithContentDescription("В архиве").performScrollTo().assertIsDisplayed().performClick().assertIsOn()
        saveImage("tag-editor")
        rule.onNodeWithText("Сохранить изменения").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals("Отпуск", renamed); assertTrue(archived) }

        var assigned: Set<String>? = null
        rule.activity.runOnUiThread {
            rule.activity.setContent { CrownTheme {
                OperationTagsEditor(purchase, snapshot.copy(organization = OrganizationState(listOf(tag))),
                    commands(assignAction = { id, expected, selected, _ ->
                        assertEquals(purchase.id, id); assertTrue(expected.isEmpty()); assigned = selected
                    }), {})
            } }
        }
        rule.onNodeWithContentDescription("Тег: Поездка").performClick()
        saveImage("operation-tags")
        rule.onNodeWithText("Сохранить теги").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(setOf(tag.id), assigned) }

        var savedQuery: JournalQuery? = null
        rule.activity.runOnUiThread {
            rule.activity.setContent { CrownTheme {
                FinanceScreen(DemoState.Ready(organized), {}, "Учебная", {}, commands(
                    filterAction = { name, query, expected, archive, _ ->
                        assertEquals("Повторный поиск", name); assertNull(expected); assertFalse(archive); savedQuery = query
                    },
                ))
            } }
        }
        rule.onNodeWithText("Поиск и фильтры").performScrollTo().performClick()
        rule.onNodeWithText("Выбрать: сохранённый фильтр").performScrollTo().performClick()
        rule.onNodeWithText("Покупки поездки").performClick()
        rule.onNodeWithText("Найдено операций: 1 из 1").performScrollTo().assertIsDisplayed()
        rule.onNode(hasText("Сохранить фильтр") and hasClickAction()).performScrollTo().performClick()
        rule.onNode(hasText("Название фильтра") and hasSetTextAction()).performTextReplacement("Повторный поиск")
        saveImage("saved-filter")
        rule.onAllNodes(hasText("Сохранить фильтр") and hasClickAction()).onLast().performClick()
        rule.runOnIdle { assertEquals(filter.query(), savedQuery) }
    }

    @Test
    fun ruleEditorRejectsPriorityAndSavesChosenCategory() {
        var saved: CategoryRule? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                CategoryRulesEditor(snapshot, commands(ruleAction = { value, previous, _ ->
                    assertNull(previous); saved = value
                }), {})
            } }
        }
        rule.onNode(hasText("Название правила") and hasSetTextAction()).performTextReplacement("Магазины")
        rule.onNode(hasText("Описание содержит") and hasSetTextAction()).performScrollTo().performTextReplacement("магазин")
        rule.onNode(hasText("Описание содержит") and hasSetTextAction()).performImeAction()
        rule.waitForIdle()
        rule.onNodeWithText("Выбрать: категория правила").performScrollTo().performClick()
        rule.onNodeWithText("Продукты").performClick()
        rule.onNode(hasText("Приоритет · 0–999") and hasSetTextAction()).performScrollTo().performTextReplacement("1000")
        rule.onNodeWithText("Создать правило").assertIsDisplayed().performClick()
        rule.onNodeWithText("Приоритет — целое число от 0 до 999").assertIsDisplayed()
        assertNull(saved)
        rule.onNode(hasText("Приоритет · 0–999") and hasSetTextAction()).performTextReplacement("20")
        saveImage("rule-editor")
        rule.onNodeWithText("Создать правило").performClick()
        rule.runOnIdle {
            assertEquals(food.id, saved?.categoryId)
            assertEquals(20, saved?.priority)
        }
    }

    @Test
    fun categorySuggestionNeedsApplicationAndManualChoiceWins() {
        val shop = CategoryRule("shop", "Магазины", OperationKind.Expense, "магазин", food.id)
        var recorded: Operation? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                OperationEditor(snapshot.balances, snapshot.categories, snapshot.month, null, commands { recorded = it }, {}, rules = listOf(shop))
            } }
        }
        rule.onNode(hasText("Сумма, ₽") and hasSetTextAction()).performScrollTo().performTextReplacement("10.00")
        rule.onNode(hasText("Описание") and hasSetTextAction()).performScrollTo().performTextReplacement("Учебный магазин")
        rule.onNode(hasText("Описание") and hasSetTextAction()).performImeAction()
        rule.waitForIdle()
        rule.onNodeWithText("Применить категорию").performScrollTo().performClick()
        rule.onNodeWithText("Категория: Продукты").performScrollTo().performClick()
        rule.onNodeWithText("Дом").performClick()
        rule.onNodeWithText("Применить категорию").assertDoesNotExist()
        saveImage("rule-manual-choice")
        rule.onNodeWithText("Сохранить операцию").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(home.id, recorded?.categoryId) }
    }

    @Test
    fun statementRuleConflictRequiresReviewBeforeWriting() {
        val shop = CategoryRule("shop", "Магазины", OperationKind.Expense, "магазин", food.id)
        val conflicting = shop.copy(id = "other-rule", name = "Другое правило", categoryId = home.id)
        val snapshotWithRules = snapshot.copy(ruleEvents = listOf(
            shop.event(1).copy(sequence = 1), conflicting.event(2).copy(sequence = 2),
        ))
        val document = parseStatement("date;amount;description;id\n2026-11-20;-10.00;Учебный магазин;rule-demo".toByteArray())
        var written: List<ImportChoice>? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                StatementReview(document, snapshotWithRules, commands(importAction = { _, values -> written = values }), {})
            } }
        }
        rule.onNodeWithText("Предложить категории").performScrollTo().performClick()
        rule.onNodeWithContentDescription("Подтвердить строки выписки").performClick()
        rule.onNodeWithText("Добавить операции (1)").assertIsNotEnabled()
        rule.onNodeWithTag("statement-rows").performScrollToNode(hasText("Настроить строку 1"))
        rule.onNodeWithText("Настроить строку 1").performScrollTo().performClick()
        rule.onNodeWithText("Выбрать: категория").performScrollTo().performClick()
        rule.onNodeWithText("Продукты").performClick()
        rule.onNodeWithText("Сохранить операцию").performClick()
        rule.onNodeWithText("Категория к записи: Продукты").performScrollTo().assertIsDisplayed()
        saveImage("rule-statement", editor = false)
        rule.onNodeWithContentDescription("Подтвердить строки выписки").performClick()
        rule.onNodeWithText("Добавить операции (1)").assertIsEnabled().performClick()
        rule.runOnIdle { assertEquals(food.id, written?.single()?.operation?.categoryId) }
    }

    @Test
    fun bonusEntryValidatesAmountAndKeepsCategoryAndOperationLink() {
        var recorded: BonusEntryEvent? = null
        val program = BonusProgramEvent(1, "store", "Учебный магазин", false, 1)
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme { BonusEntryEditor(snapshot, program, null, commands(bonusAction = { value, _, _ -> recorded = value }), {}) } }
        }
        rule.onNode(hasText("Количество бонусов") and hasSetTextAction()).performTextReplacement("-1")
        rule.onNode(hasText("Количество бонусов") and hasSetTextAction()).performImeAction()
        rule.onNodeWithText("Сохранить бонусы").assertIsDisplayed().performClick()
        rule.onNodeWithText("Введите ненулевое число, не более двух знаков после запятой").assertIsDisplayed()
        assertNull(recorded)
        rule.onNode(hasText("Количество бонусов") and hasSetTextAction()).performScrollTo().performTextReplacement("1,25")
        rule.onNode(hasText("Количество бонусов") and hasSetTextAction()).performImeAction()
        rule.onNodeWithText("Выбрать: категория расхода").performScrollTo().performClick()
        rule.onNodeWithText("Продукты").performClick()
        rule.onNodeWithText("Выбрать: связанная операция").performScrollTo().performClick()
        rule.onNodeWithText("15.11.2026 · Учебная покупка").performClick()
        saveImage("bonus-entry")
        rule.onNodeWithText("Сохранить бонусы").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertEquals(125L, recorded?.units)
            assertEquals(food.id, recorded?.categoryId)
            assertEquals(purchase.id, recorded?.operationId)
            assertEquals("2026-11-01", recorded?.date)
        }
    }

    @Test
    fun bonusPageKeepsProgramBalancesSeparateAndEditsInitiallySelectedProgram() {
        val programs = listOf(BonusProgramEvent(1, "a", "Магазин", false, 1), BonusProgramEvent(2, "b", "Другой магазин", false, 2))
        val entries = listOf(
            BonusEntryEvent(1, "one", "a", date.toString(), BonusKind.Accrual.name, 125, food.id, null, "", false, 1),
            BonusEntryEvent(2, "two", "b", date.toString(), BonusKind.Accrual.name, 999, null, null, "", false, 2),
        )
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme { FinanceScreen(DemoState.Ready(snapshot.copy(bonuses = BonusState(programs, entries))), {}, "Синтетика", {}, commands()) } }
        }
        rule.onNodeWithContentDescription("Бонусы").performClick()
        rule.onNodeWithText("Программа: Другой магазин").assertIsDisplayed().performClick()
        rule.onNodeWithText("Магазин", useUnmergedTree = true).performClick()
        rule.onNodeWithTag("bonus-list").performScrollToNode(hasText("1,25 баллов"))
        rule.onNodeWithText("1,25 баллов").assertIsDisplayed()
        rule.onNodeWithTag("bonus-list").performScrollToNode(hasText("Продукты: 1,25"))
        rule.onNodeWithText("Продукты: 1,25").assertIsDisplayed()
        saveImage("bonus-page", editor = false)
        rule.onNodeWithTag("bonus-list").performScrollToNode(hasText("Настроить программу"))
        rule.onNodeWithText("Настроить программу").performClick()
        rule.onNodeWithText("Программа бонусов").assertIsDisplayed()
        rule.onNode(hasText("Название программы") and hasSetTextAction()).assertTextContains("Магазин")
    }

    @Test
    fun passwordFormChecksConfirmationAndClearsSubmittedFields() {
        var called = false
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme { PasswordForm(commands(passwordAction = { current, next, _ ->
                assertEquals("old-only", current.concatToString())
                assertEquals("new-only", next.concatToString())
                current.fill(' '); next.fill(' '); called = true
            }), {}) } }
        }
        rule.onNode(hasText("Пароль или CA1") and hasSetTextAction()).performTextReplacement("old-only")
        rule.onNode(hasText("Новый пароль") and hasSetTextAction()).performTextReplacement("new-only")
        rule.onNode(hasText("Повторите пароль") and hasSetTextAction()).performTextReplacement("different")
        rule.onNodeWithText("Изменить пароль").performClick()
        rule.onNodeWithText("Новые парольные фразы различаются").assertIsDisplayed()
        assertFalse(called)
        rule.onNode(hasText("Повторите пароль") and hasSetTextAction()).performTextReplacement("new-only")
        saveImage("password-change")
        rule.onNodeWithText("Изменить пароль").assertIsDisplayed().performClick()
        rule.runOnIdle { assertTrue(called) }
        rule.onNode(hasText("Новый пароль") and hasSetTextAction()).assert(SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.EditableText, androidx.compose.ui.text.AnnotatedString("")))
    }

    @Test
    fun depositEditorValidatesRateAndSavesExplicitPolicies() {
        var recorded: DepositScenario? = null
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme {
                DepositEditor(null, null, "", commands(depositAction = { _, title, value, previous, archived, _ ->
                    assertEquals("Учебный вклад", title); assertNull(previous); assertFalse(archived); recorded = value
                }), {}, {})
            } }
        }
        rule.onNode(hasText("Название сценария") and hasSetTextAction()).performTextReplacement("Учебный вклад")
        rule.onNode(hasText("Сумма вклада, ₽") and hasSetTextAction()).performScrollTo().performTextReplacement("10000,00")
        rule.onNode(hasText("Открытие") and hasSetTextAction()).performScrollTo().performTextReplacement("01012026")
        rule.onNode(hasText("Окончание") and hasSetTextAction()).performScrollTo().performTextReplacement("01012027")
        rule.onNode(hasText("Начальная ставка, %") and hasSetTextAction()).performScrollTo().performTextReplacement("1001")
        rule.onNode(hasText("Начальная ставка, %") and hasSetTextAction()).performImeAction()
        rule.onNodeWithText("Сохранить сценарий").assertIsDisplayed().performClick()
        rule.onNodeWithText("Проверьте ставку 1: от 0 до 1000%, до 8 дробных знаков").assertIsDisplayed()
        assertNull(recorded)
        rule.onNode(hasText("Начальная ставка, %") and hasSetTextAction()).performScrollTo().performTextReplacement("12")
        rule.onNode(hasText("Начальная ставка, %") and hasSetTextAction()).performImeAction()
        rule.onNodeWithText("Правила расчёта").performScrollTo().performClick()
        rule.onNodeWithText("Дни в году: 365 или 366 по календарю").performScrollTo().performClick()
        rule.onNodeWithText("Всегда 365 дней").performClick()
        saveImage("deposit-editor")
        rule.onNodeWithText("Сохранить сценарий").assertIsDisplayed().performClick()
        rule.runOnIdle {
            assertNotNull(recorded)
            assertEquals(DepositDayCount.Fixed365, recorded?.dayCount)
            assertEquals(LocalDate.of(2026, 1, 1), recorded?.opened)
            assertEquals(120000.toBigInteger(), forecastDeposit(requireNotNull(recorded)).interest)
        }
    }

    @Test
    fun depositHistoryRecalculatesChosenVersionWithoutChangingLedger() {
        val opened = LocalDate.of(2026, 1, 1)
        val scenario = DepositScenario(opened, opened.plusYears(1), 1000000, listOf(DepositRate("r", opened, java.math.BigDecimal("10"))), emptyList(), listOf(opened.plusYears(1)))
        val first = DepositEvent(1, "d", "Учебный вклад", encodeDeposit(scenario), false, 1)
        val second = first.copy(sequence = 2, payload = encodeDeposit(scenario.copy(rates = listOf(DepositRate("r", opened, java.math.BigDecimal("12"))))), recordedAt = 2)
        rule.activity.runOnUiThread {
            rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
            rule.activity.setContent { CrownTheme { DepositsPage(snapshot.copy(depositEvents = listOf(first, second)), commands(), {}) } }
        }
        rule.onNodeWithText("Учебный вклад").performClick()
        rule.waitUntil(10000) { rule.onAllNodesWithText(money(120000)).fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithTag("deposits-list").performScrollToNode(hasText("История условий"))
        rule.onNodeWithText("История условий").performClick()
        rule.onNodeWithTag("deposits-list").performScrollToNode(hasText("Версия 1 · Учебный вклад"))
        rule.onNodeWithText("Версия 1 · Учебный вклад").performClick()
        rule.waitUntil(10000) { rule.onAllNodesWithText(money(100000)).fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithTag("deposits-list").performScrollToNode(hasText("Прогноз процентов"))
        saveImage("deposit-history", editor = false)
        rule.onNodeWithTag("deposits-list").performScrollToNode(hasText("Просмотр прежних условий · версия 1"))
        rule.onNodeWithText("Просмотр прежних условий · версия 1").assertIsDisplayed()
        rule.onNodeWithText("Изменить условия").assertDoesNotExist()
    }

    private fun saveImage(name: String, editor: Boolean = true) {
        rule.waitForIdle()
        val directory = File(rule.activity.noBackupFilesDir, "synthetic-ui-proof").apply { mkdirs() }
        val file = File(directory, "$name.png")
        val node = if (editor) rule.onNodeWithTag("operation-editor") else rule.onRoot()
        val bitmap = if (Build.VERSION.SDK_INT < 28) {
            requireNotNull(InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot())
        } else {
            node.captureToImage().asAndroidBitmap()
        }
        file.outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
    }
}
