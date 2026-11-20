package ru.crown.finance.ui

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

    private fun commands(accept: (Operation) -> Unit = {}) = BookCommands(
        busy = false, error = null, clearError = {}, changeMonth = {}, addAccount = { _, _, _, _ -> },
        addOperation = { value, _ -> accept(value) }, reviseOperation = { _, _, _ -> },
        backup = {}, export = { _, _ -> }, addCategory = { _, _, _ -> }, inspectBackup = {},
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
        rule.onNodeWithText("Учебная покупка").performClick()
        rule.onNodeWithText("Оформить возврат").assertIsDisplayed().performClick()
        rule.onNodeWithText("Сохранить возврат").assertIsDisplayed()
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("7.00")
        rule.onNodeWithText("Сохранить возврат").performClick()
        rule.onNodeWithText("Сумма возврата превышает доступный остаток").assertIsDisplayed()
        assertNull(recorded)
        rule.onNode(hasText("Возврат части 1, ₽") and hasSetTextAction()).performTextReplacement("1.50")
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
        saveImage("split-form")
        rule.onNodeWithText("Сохранить операцию").assertIsDisplayed().performClick()
        rule.runOnIdle { assertEquals(listOf(Allocation(food.id, 600), Allocation(home.id, 400)), recorded?.parts) }
    }

    private fun saveImage(name: String) {
        rule.waitForIdle()
        val directory = File(rule.activity.noBackupFilesDir, "synthetic-ui-proof").apply { mkdirs() }
        val file = File(directory, "$name.png")
        val bitmap = rule.onNodeWithTag("operation-editor").captureToImage().asAndroidBitmap()
        file.outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
    }
}
