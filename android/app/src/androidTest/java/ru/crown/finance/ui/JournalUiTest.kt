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
        closeAction: (YearMonth, Boolean, String) -> Unit = { _, _, _ -> },
        accept: (Operation) -> Unit = {},
    ) = BookCommands(
        busy = false, error = null, clearError = {}, changeMonth = {}, addAccount = accountAction,
        addOperation = { value, _ -> accept(value) }, reviseOperation = { _, _, _ -> },
        backup = {}, export = { _, _ -> }, addCategory = categoryAction, inspectBackup = {},
        setPeriodClosed = closeAction, reconcile = reconcileAction,
        chooseStatement = {}, importStatement = importAction, addBank = bankAction,
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
