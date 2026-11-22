package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.time.LocalDate

class OrganizationTest {
    private val tag = TagEvent(1, "tag", "Поездка", false, 1)
    private val filter = FilterEvent(1, "filter", "Покупки поездки", "магазин", "expense", null, "main", tag.id, false, 2)
    private val state = OrganizationState(listOf(tag), listOf(TagAssignment(1, "op", tag.id, true, 2)), listOf(filter))
    private fun validate(value: OrganizationState) = value.validate(setOf("main"), emptySet(), setOf("op"))

    @Test
    fun tagHistoryAndAssignmentsKeepArchivedMembership() {
        val archived = tag.copy(sequence = 2, name = "Отпуск", archived = true, recordedAt = 3)
        val actual = state.copy(tagEvents = listOf(tag, archived))
        validate(actual)
        assertEquals(listOf(archived), actual.tags)
        assertEquals(setOf(tag.id), actual.tagsByOperation()["op"])
        val removed = actual.copy(assignments = actual.assignments + TagAssignment(2, "op", tag.id, false, 4))
        assertTrue(removed.tagsByOperation().isEmpty())
    }

    @Test
    fun rejectsForeignReferencesDuplicateRevisionsAndDuplicateActiveNames() {
        assertThrows(IllegalArgumentException::class.java) { validate(state.copy(tagEvents = emptyList())) }
        assertThrows(IllegalArgumentException::class.java) { state.validate(emptySet(), emptySet(), setOf("op")) }
        assertThrows(IllegalArgumentException::class.java) { state.validate(setOf("main"), emptySet(), emptySet()) }
        assertThrows(IllegalArgumentException::class.java) { validate(state.copy(tagEvents = listOf(tag, tag))) }
        assertThrows(IllegalArgumentException::class.java) {
            validate(state.copy(tagEvents = listOf(tag, tag.copy(sequence = 2, id = "other", name = "ПОЕЗДКА"))))
        }
    }

    @Test
    fun binaryCodecRetainsBothHistoryAndStrictFilterFields() {
        val output = ByteArrayOutputStream()
        DataOutputStream(output).use { it.organization(state) }
        assertEquals(state, DataInputStream(ByteArrayInputStream(output.toByteArray())).organization())
        assertThrows(IllegalArgumentException::class.java) { validate(state.copy(filterEvents = listOf(filter.copy(kind = "unknown")))) }
        assertThrows(IllegalArgumentException::class.java) { validate(state.copy(filterEvents = listOf(filter.copy(textQuery = "x".repeat(101))))) }
        assertThrows(IllegalArgumentException::class.java) {
            DataInputStream(ByteArrayInputStream(byteArrayOf(-1, -1, -1, -1))).organization()
        }
    }

    @Test
    fun tagAndAccountFiltersIncludeBothSidesOfTransfer() {
        val transfer = Operation("op", OperationKind.Transfer, LocalDate.of(2026, 11, 22), "main", 100, "Перевод", "second")
        val accounts = listOf(Account("main", "Карта"), Account("second", "Накопительный"))
        val query = JournalQuery(accountId = "second", tagId = tag.id)
        assertEquals(listOf(transfer), query.apply(listOf(transfer), accounts, emptyList(), state.tagsByOperation()))
        assertTrue(query.apply(listOf(transfer), accounts, emptyList()).isEmpty())
        assertEquals(2, query.count)
    }
}
