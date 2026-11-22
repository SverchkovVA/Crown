package ru.crown.finance.ui

import android.content.Context
import android.os.Build
import androidx.compose.ui.test.*
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
            rule.onNodeWithText("Закрыть").performClick()
            waitText("Открыть книгу")
            runBlocking {
                BookRepository(context).open(next.toCharArray(), CredentialKind.Password).use { book ->
                    assertEquals(title, book.identity.name)
                    val snapshot = book.snapshot(YearMonth.now())
                    assertEquals(12000.toBigInteger(), snapshot.totalMinor)
                    assertEquals(345.toBigInteger(), snapshot.expenseMinor)
                    assertEquals(120000.toBigInteger(), forecastDeposit(currentDeposits(snapshot.depositEvents).single().scenario()).interest)
                    val plans = currentPlans(snapshot.planningEvents)
                    assertEquals(2, plans.size)
                    val budget = plans.single { it.kind == PlanningKind.Budget.wire }.conditions() as BudgetPlan
                    assertEquals(345.toBigInteger(), executeBudget(budget, snapshot.currentOperations()).spent)
                    val goal = plans.single { it.kind == PlanningKind.Goal.wire }.conditions() as GoalPlan
                    assertEquals(0L, goal.current)
                    assertEquals(2.toBigInteger(), projectGoal(goal).contributions)
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
