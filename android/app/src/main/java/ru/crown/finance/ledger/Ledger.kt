package ru.crown.finance.ledger

import java.math.BigInteger
import java.time.LocalDate
import java.time.YearMonth

internal enum class OperationKind(val wireName: String) {
    Opening("opening"),
    Income("income"),
    Expense("expense"),
    Transfer("transfer");

    companion object {
        fun fromWireName(name: String): OperationKind = entries.single { it.wireName == name }
    }
}

internal data class Account(val id: String, val name: String)

internal data class Operation(
    val id: String,
    val kind: OperationKind,
    val date: LocalDate,
    val accountId: String,
    val amountMinor: Long,
    val description: String,
    val destinationId: String? = null,
    val feeMinor: Long = 0,
    val categoryId: String? = null,
) {
    init {
        if (kind == OperationKind.Opening) {
            require(amountMinor != Long.MIN_VALUE)
        } else {
            require(amountMinor > 0)
        }
        require(feeMinor >= 0)
        require(categoryId == null || (categoryId.length in 1..64 && kind in categoryKinds))
        if (kind == OperationKind.Transfer) {
            require(destinationId != null && destinationId != accountId)
            Math.addExact(amountMinor, feeMinor)
        } else {
            require(destinationId == null && feeMinor == 0L)
        }
    }

    fun movementFor(accountId: String): Long = when (accountId) {
        this.accountId -> when (kind) {
            OperationKind.Opening, OperationKind.Income -> amountMinor
            OperationKind.Expense -> -amountMinor
            OperationKind.Transfer -> -Math.addExact(amountMinor, feeMinor)
        }
        destinationId -> amountMinor
        else -> error("Операция не затрагивает счёт")
    }
}

internal data class AccountBalance(val account: Account, val minor: BigInteger)

internal data class LedgerSnapshot(
    val month: YearMonth,
    val balances: List<AccountBalance>,
    val operations: List<Operation>,
    val incomeMinor: BigInteger,
    val expenseMinor: BigInteger,
    val history: List<OperationHistory> = emptyList(),
    val categories: List<Category> = emptyList(),
) {
    val totalMinor: BigInteger = balances.fold(BigInteger.ZERO) { sum, balance -> sum + balance.minor }

    companion object {
        fun from(month: YearMonth, accounts: List<Account>, operations: List<Operation>): LedgerSnapshot {
            val balances = accounts.associate { it.id to BigInteger.ZERO }.toMutableMap()
            require(balances.size == accounts.size)
            require(operations.map { it.id }.toSet().size == operations.size)
            var income = BigInteger.ZERO
            var expense = BigInteger.ZERO
            val monthRows = mutableListOf<Operation>()
            for (operation in operations.sortedWith(compareBy(Operation::date, Operation::id))) {
                require(operation.accountId in balances)
                val destination = operation.destinationId
                require(destination == null || destination in balances)
                if (operation.date > month.atEndOfMonth()) continue
                val origin = operation.accountId
                balances[origin] = balances.getValue(origin) + operation.movementFor(origin).toBigInteger()
                if (destination != null) {
                    balances[destination] = balances.getValue(destination) + operation.movementFor(destination).toBigInteger()
                }
                if (YearMonth.from(operation.date) != month) continue
                monthRows.add(operation)
                when (operation.kind) {
                    OperationKind.Opening -> Unit
                    OperationKind.Income -> income += operation.amountMinor.toBigInteger()
                    OperationKind.Expense -> expense += operation.amountMinor.toBigInteger()
                    OperationKind.Transfer -> expense += operation.feeMinor.toBigInteger()
                }
            }
            return LedgerSnapshot(
                month, accounts.map { AccountBalance(it, balances.getValue(it.id)) },
                monthRows.asReversed(), income, expense,
            )
        }
    }
}
