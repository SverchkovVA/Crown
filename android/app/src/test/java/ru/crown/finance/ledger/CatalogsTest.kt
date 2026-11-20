package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test

class CatalogsTest {
    private val bank = Bank("bank", "Учебный банк")
    private val account = Account("main", "Счёт", bank.id, AccountKind.Saving)
    private val parent = Category("parent", "Продукты", OperationKind.Expense)
    private val child = Category("child", "Кафе", OperationKind.Expense, parent.id)

    @Test
    fun validatesAccountAndCategoryLinks() {
        validateCatalogs(listOf(account), listOf(parent, child), listOf(bank))
        assertThrows(IllegalArgumentException::class.java) {
            validateCatalogs(listOf(account), listOf(parent, child), emptyList())
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateCatalogs(listOf(account), listOf(parent, child.copy(kind = OperationKind.Income)), listOf(bank))
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateCatalogs(listOf(account), listOf(child), listOf(bank))
        }
    }

    @Test
    fun rejectsCategoryCyclesAndDuplicateBanks() {
        assertThrows(IllegalArgumentException::class.java) {
            validateCatalogs(emptyList(), listOf(parent.copy(parentId = child.id), child), emptyList())
        }
        assertThrows(IllegalArgumentException::class.java) {
            validateCatalogs(emptyList(), emptyList(), listOf(bank, bank.copy(id = "other")))
        }
    }
}
