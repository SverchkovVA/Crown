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
            rule.onNodeWithContentDescription("Настройки").performClick()
            rule.onNodeWithText("Сменить парольную фразу").performScrollTo().performClick()
            field("Текущий пароль или CA1", old)
            field("Новая парольная фраза", next)
            field("Повторите новую фразу", next)
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
