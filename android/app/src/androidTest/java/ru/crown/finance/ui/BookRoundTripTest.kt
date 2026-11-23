package ru.crown.finance.ui

import android.content.Context
import android.os.Build
import android.graphics.Bitmap
import android.view.WindowManager
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.test.*
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Assume.assumeFalse
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import ru.crown.finance.MainActivity
import ru.crown.finance.ledger.*
import java.io.File
import java.time.YearMonth
import java.util.UUID

/** Полный путь через MainActivity и настоящую модель. Запускается только на пустом эмуляторе. */
@RunWith(AndroidJUnit4::class)
class BookRoundTripTest {
    @get:Rule val rule = createAndroidComposeRule<MainActivity>()

    private fun saveScreen(name: String) {
        rule.waitForIdle()
        val bitmap = requireNotNull(InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot())
        val directory = File(rule.activity.noBackupFilesDir, "synthetic-ui-proof").apply { mkdirs() }
        try { File(directory, "$name.png").outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) } }
        finally { bitmap.recycle() }
    }

    @Test fun realBookRetainsOperationAfterPasswordChangeAndImmediateReopen() {
        assumeTrue(Build.HARDWARE in listOf("ranchu", "goldfish"))
        val context = ApplicationProvider.getApplicationContext<Context>()
        val directory = File(context.noBackupFilesDir, "mobile-book-v1")
        assumeFalse(directory.exists())
        val title = "Синтетика E2E " + UUID.randomUUID().toString()
        val old = "e2e-old-only"
        val next = "e2e-new-only"
        var created = false
        fun waitText(text: String) = rule.waitUntil(15_000) {
            rule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
        }
        fun field(label: String, value: String) {
            rule.onNode(hasText(label) and hasSetTextAction()).performScrollTo().performTextReplacement(value)
        }
        try {
            field("Название книги", title)
            field("Парольная фраза", old)
            field("Повторите парольную фразу", old)
            rule.onNodeWithText("Создать книгу").performScrollTo().performClick()
            waitText("Записал код")
            created = true
            rule.onNodeWithText("Записал код").performScrollTo().performClick()
            rule.activity.runOnUiThread { rule.activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
            waitText("Добавить счёт")
            rule.onNodeWithText("Добавить счёт").performClick()
            field("Название счёта", "Учебный счёт")
            field("Начальный остаток, ₽", "123,45")
            rule.onNode(hasText("Начальный остаток, ₽") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить счёт").performClick()
            waitText("Добавить операцию")
            rule.onNodeWithText("Добавить операцию").performClick()
            field("Сумма, ₽", "3,45")
            field("Описание", "Учебная E2E покупка")
            rule.onNode(hasText("Описание") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить операцию").performClick()
            waitText("Добавить операцию")
            rule.onNodeWithContentDescription("Счета").performClick()
            rule.onNodeWithText("Управление счетами").performScrollTo().performClick()
            rule.onNodeWithContentDescription("Изменить: Учебный счёт").performClick()
            field("Название", "Счёт после изменения")
            rule.onNode(hasText("Название") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить изменения").assertIsDisplayed().performClick()
            waitText("Новый счёт")
            rule.onNodeWithContentDescription("Назад").performClick()
            rule.onNodeWithContentDescription("Отчёты").performClick()
            rule.onNodeWithText("Вклады · прогноз процентов").performScrollTo().performClick()
            rule.onNodeWithText("Новый сценарий вклада", substring = false).performClick()
            field("Название сценария", "E2E вклад")
            field("Сумма вклада, ₽", "10000,00")
            field("Открытие", "01012026")
            field("Окончание", "01012027")
            field("Начальная ставка, %", "12")
            rule.onNode(hasText("Начальная ставка, %") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить сценарий").performClick()
            waitText("Прогноз процентов")
            rule.onNodeWithContentDescription("Отчёты").performClick()
            rule.onNodeWithText("Бюджеты и цели").performScrollTo().performClick()
            rule.onNodeWithText("Новый бюджет").performClick()
            field("Название бюджета", "E2E бюджет")
            field("Лимит расходов, ₽", "10,00")
            rule.onNode(hasText("Лимит расходов, ₽") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить бюджет").performClick()
            waitText("Расходы за период")
            rule.onNodeWithTag("planning-list").performScrollToNode(hasText("Бюджеты"))
            rule.onNodeWithText("Бюджеты").performClick()
            rule.onNodeWithText("Цели").performClick()
            rule.onNodeWithText("Новая цель").performClick()
            field("Название цели", "E2E цель")
            field("Сумма цели, ₽", "200,00")
            field("Ежемесячно, ₽", "100,00")
            rule.onNode(hasText("Ежемесячно, ₽") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить цель").performClick()
            waitText("Накоплено")
            rule.onNodeWithContentDescription("Отчёты").performClick()
            rule.onNodeWithText("Регулярные платежи").performScrollTo().performClick()
            rule.onNodeWithText("Новый календарь").performClick()
            field("Название календаря", "E2E календарь")
            field("Название платежа", "E2E ожидаемая покупка")
            field("Сумма платежа, ₽", "3,45")
            rule.onNode(hasText("Сумма платежа, ₽") and hasSetTextAction()).performImeAction()
            rule.onNodeWithText("Сохранить календарь").performClick()
            rule.waitUntil(15_000) {
                rule.onNodeWithTag("calendars-list").fetchSemanticsNode().config.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.StateDescription) == "Расчёт готов"
            }
            rule.onNodeWithTag("calendars-list").performScrollToNode(hasText("Прогноз остатка"))
            rule.onNodeWithText("Прогноз остатка").assertIsDisplayed()
            rule.waitUntil(10000) { rule.onNodeWithTag("calendars-list").fetchSemanticsNode().config.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.StateDescription) == "Расчёт готов" }
            rule.onNodeWithTag("calendars-list").performScrollToNode(hasText("Сопоставить платёж 1"))
            rule.onNodeWithText("Сопоставить платёж 1").performClick()
            rule.onNodeWithTag("calendars-list").performScrollToNode(hasText("Связать: Учебная E2E покупка"))
            rule.onNodeWithText("Связать: Учебная E2E покупка").performClick()
            waitText("Сопоставление сохранено")
            rule.onNodeWithTag("calendars-list").performScrollToNode(hasText("Связь сохранена в книге"))
            saveScreen("main-calendar-linked")
            rule.onNodeWithContentDescription("Настройки").performClick()
            rule.onNodeWithText("Сменить парольную фразу").performScrollTo().performClick()
            field("Пароль или CA1", old)
            field("Новый пароль", next)
            field("Повторите пароль", next)
            rule.onNodeWithText("Изменить пароль").assertIsDisplayed().performClick()
            waitText("Парольная фраза изменена")
            rule.onNodeWithText("Закрыть").performClick()
            waitText("Открыть книгу")
            field("Парольная фраза", next)
            rule.onNodeWithText("Открыть книгу").performScrollTo().performClick()
            waitText("Добавить операцию")
            rule.onNodeWithTag("operations-list").performScrollToNode(hasText("Учебная E2E покупка"))
            rule.onNodeWithText("Учебная E2E покупка").assertIsDisplayed()
            rule.onNodeWithContentDescription("Отчёты").performClick()
            rule.onNodeWithText("Регулярные платежи").performScrollTo().performClick()
            rule.onNodeWithText("E2E календарь").performClick()
            rule.waitUntil(10000) { rule.onNodeWithTag("calendars-list").fetchSemanticsNode().config.getOrNull(androidx.compose.ui.semantics.SemanticsProperties.StateDescription) == "Расчёт готов" }
            rule.onNodeWithTag("calendars-list").performScrollToNode(hasText("Связь сохранена в книге"))
            rule.onNodeWithText("Связь сохранена в книге").assertIsDisplayed()
            saveScreen("main-calendar-reopened")
            rule.onNodeWithText("Закрыть").performClick()
            waitText("Открыть книгу")
            runBlocking {
                BookRepository(context).open(next.toCharArray(), CredentialKind.Password).use { book ->
                    assertEquals(title, book.identity.name)
                    val snapshot = book.snapshot(YearMonth.now())
                    assertEquals("Счёт после изменения", snapshot.balances.single().account.name)
                    assertEquals(listOf("Учебный счёт", "Счёт после изменения"), snapshot.catalogHistory.filter { it.value.type == CatalogType.Account }.map { it.value.name })
                    assertEquals(12000.toBigInteger(), snapshot.totalMinor)
                    assertEquals(345.toBigInteger(), snapshot.expenseMinor)
                    assertEquals(120000.toBigInteger(), forecastDeposit(currentDeposits(snapshot.depositEvents).single().scenario()).interest)
                    val plans = currentPlans(snapshot.planningEvents)
                    assertEquals(3, plans.size)
                    val budget = plans.single { it.kind == PlanningKind.Budget.wire }.conditions() as BudgetPlan
                    assertEquals(345.toBigInteger(), executeBudget(budget, snapshot.currentOperations()).spent)
                    val goal = plans.single { it.kind == PlanningKind.Goal.wire }.conditions() as GoalPlan
                    assertEquals(0L, goal.current)
                    assertEquals(2.toBigInteger(), projectGoal(goal).contributions)
                    val calendarEvent = plans.single { it.kind == PlanningKind.Calendar.wire }
                    val calendar = calendarEvent.conditions() as CalendarPlan
                    val day = java.time.LocalDate.now()
                    val report = forecastAccount(calendar, setOf(snapshot.balances.single().account.id), snapshot.currentOperations(), day, day)
                    assertEquals(11655.toBigInteger(), report.closing)
                    val savedMatches = resolveCalendarMatches(calendarEvent, snapshot.balances.single().account.id, forecastPayments(calendar, day, day), snapshot.currentOperations(), snapshot.calendarMatchEvents)
                    assertEquals(1, snapshot.calendarMatchEvents.size)
                    assertEquals(1, savedMatches.matches.size)
                    assertEquals(12000.toBigInteger(), forecastAccount(calendar, setOf(snapshot.balances.single().account.id), snapshot.currentOperations(), day, day, savedMatches.matches).closing)
                    val paid = snapshot.currentOperations().single { it.kind == OperationKind.Expense }
                    assertEquals(12000.toBigInteger(), forecastAccount(calendar, setOf(paid.accountId), snapshot.currentOperations(), day, day, listOf(PaymentMatch(0, paid.id, paid.fingerprint()))).closing)
                }
            }
        } finally {
            rule.activityRule.scenario.close()
            if (created && directory.exists()) runBlocking {
                // Перед удалением доказываем принадлежность именно этому синтетическому сценарию.
                val repository = BookRepository(context)
                val book = listOf(next, old).firstNotNullOfOrNull { password ->
                    runCatching { repository.open(password.toCharArray(), CredentialKind.Password) }.getOrNull()
                }
                checkNotNull(book) { "Не удалось подтвердить принадлежность тестовой книги" }
                book.use { check(it.identity.name == title) }
                check(directory.canonicalFile.parentFile == context.noBackupFilesDir.canonicalFile)
                check(directory.deleteRecursively())
            }
        }
    }
}
