package ru.crown.finance.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Build
import android.view.WindowManager
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import ru.crown.finance.MainActivity
import ru.crown.finance.ledger.BookLocations
import ru.crown.finance.ledger.BookRepository
import ru.crown.finance.ledger.CredentialKind
import java.io.ByteArrayInputStream
import java.io.File
import java.time.LocalDate
import java.time.YearMonth
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class MultipleBooksTest {
    @get:Rule val rule = createEmptyComposeRule()

    @Test fun legacyNewAndRestoredBooksKeepSeparateAccessAndBalances() {
        assumeTrue(Build.HARDWARE in listOf("ranchu", "goldfish"))
        val context = ApplicationProvider.getApplicationContext<Context>()
        val locations = BookLocations(context.noBackupFilesDir)
        assumeTrue(locations.numbers().isEmpty())
        val suffix = UUID.randomUUID().toString().take(8)
        val title = "Учебная $suffix"
        val secondTitle = "Вторая $suffix"
        val original = "source-$suffix"
        val second = "other-$suffix"
        val restored = "restored-$suffix"
        val copyPassword = "copy-$suffix"
        val legacy = BookRepository(context)
        val today = LocalDate.now()

        try {
            runBlocking {
                legacy.create(title, original.toCharArray()).first.use {
                    it.addAccount("Исходный счёт", 12345, today)
                }
            }
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                rule.onNodeWithText("Книга 1").assertIsDisplayed()
                click("Все книги")
                click("Новая книга")
                field("Название книги", secondTitle)
                field("Парольная фраза", second)
                field("Повторите парольную фразу", second)
                click("Создать книгу")
                waitText("Записал код")
                click("Записал код")
                waitText("Добавить счёт")
                rule.onNodeWithText("Добавить счёт").performClick()
                field("Название счёта", "Второй счёт")
                field("Начальный остаток, ₽", "46,00")
                rule.onNode(hasText("Начальный остаток, ₽") and hasSetTextAction()).performImeAction()
                rule.onNodeWithText("Сохранить счёт").performClick()
                waitText("Добавить операцию")
                closeBook()
                scenario.onActivity { it.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
                saveScreen(context, "books-list")
                click("Книга 1")
                field("Парольная фраза", second)
                click("Открыть книгу")
                waitText("Не удалось открыть книгу.", substring = true)
                field("Парольная фраза", original)
                click("Открыть книгу")
                waitText("Добавить операцию")
                closeBook()
            }

            runBlocking {
                val restoredRepository = BookRepository(context, locations.path(locations.nextNumber()))
                val (sourceId, copy) = legacy.open(original.toCharArray(), CredentialKind.Password).use { book ->
                    assertEquals(12345.toBigInteger(), book.snapshot(YearMonth.from(today)).totalMinor)
                    book.identity.id to book.backup(copyPassword.toCharArray())
                }
                try {
                    restoredRepository.restore(restored.toCharArray(), copyPassword.toCharArray()) {
                        ByteArrayInputStream(copy)
                    }.first.use {
                        assertEquals(title, it.identity.name)
                        assertNotEquals(sourceId, it.identity.id)
                        assertEquals(12345.toBigInteger(), it.snapshot(YearMonth.from(today)).totalMinor)
                    }
                } finally {
                    copy.fill(0)
                }
                BookRepository(context, locations.path(2)).open(second.toCharArray(), CredentialKind.Password).use {
                    assertEquals(secondTitle, it.identity.name)
                    assertEquals(4600.toBigInteger(), it.snapshot(YearMonth.from(today)).totalMinor)
                }
                assertEquals(listOf(1, 2, 3), locations.numbers())
            }

            ActivityScenario.launch(MainActivity::class.java).use {
                waitText("Книга 3")
                click("Книга 3")
                field("Парольная фраза", restored)
                click("Открыть книгу")
                waitText("Добавить операцию")
                closeBook()
                rule.onNodeWithText("Книга 1").assertIsDisplayed()
                rule.onNodeWithText("Книга 2").assertIsDisplayed()
            }
        } finally {
            runBlocking {
                for ((index, credentials) in listOf(original to title, second to secondTitle, restored to title).withIndex()) {
                    val folder = locations.path(index + 1)
                    if (!folder.exists()) continue
                    val (password, expectedName) = credentials
                    BookRepository(context, folder).open(password.toCharArray(), CredentialKind.Password).use {
                        check(it.identity.name == expectedName)
                    }
                    check(folder.canonicalFile.parentFile == context.noBackupFilesDir.canonicalFile)
                    check(folder.deleteRecursively())
                }
            }
        }
    }

    private fun closeBook() {
        rule.onNodeWithText("Закрыть").performClick()
        waitText("Ваши книги")
    }

    private fun field(label: String, value: String) {
        rule.onNode(hasText(label) and hasSetTextAction()).performScrollTo().performTextReplacement(value)
    }

    private fun click(text: String) = rule.onNodeWithText(text).performScrollTo().performClick()

    private fun waitText(text: String, substring: Boolean = false) {
        rule.waitUntil(20_000) {
            rule.onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun saveScreen(context: Context, name: String) {
        rule.waitForIdle()
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        rule.waitUntil(8_000) {
            val frame = automation.takeScreenshot() ?: return@waitUntil false
            try { frame.getPixel(frame.width / 2, frame.height / 2) != Color.BLACK }
            finally { frame.recycle() }
        }
        val bitmap = requireNotNull(automation.takeScreenshot())
        val directory = File(context.noBackupFilesDir, "synthetic-ui-proof").apply { mkdirs() }
        try {
            File(directory, "$name.png").outputStream().use {
                assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it))
            }
        } finally {
            bitmap.recycle()
        }
    }
}
