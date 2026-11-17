package ru.crown.finance.ledger

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
) {
    init {
        if (kind == OperationKind.Opening) {
            require(amountMinor != Long.MIN_VALUE)
        } else {
            require(amountMinor > 0)
        }
        require(feeMinor >= 0)
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

internal data class AccountBalance(val account: Account, val minor: Long)

internal data class LedgerSnapshot(
    val month: YearMonth,
    val balances: List<AccountBalance>,
    val operations: List<Operation>,
    val incomeMinor: Long,
    val expenseMinor: Long,
) {
    val totalMinor: Long = balances.fold(0L) { sum, balance -> Math.addExact(sum, balance.minor) }

    companion object {
        fun from(month: YearMonth, accounts: List<Account>, operations: List<Operation>): LedgerSnapshot {
            val balances = accounts.associate { it.id to 0L }.toMutableMap()
            require(balances.size == accounts.size)
            require(operations.map { it.id }.toSet().size == operations.size)
            var income = 0L
            var expense = 0L
            val monthRows = mutableListOf<Operation>()
            for (operation in operations.sortedWith(compareBy(Operation::date, Operation::id))) {
                require(operation.accountId in balances)
                val destination = operation.destinationId
                require(destination == null || destination in balances)
                if (operation.date > month.atEndOfMonth()) continue
                val origin = operation.accountId
                balances[origin] = Math.addExact(balances.getValue(origin), operation.movementFor(origin))
                if (destination != null) {
                    balances[destination] = Math.addExact(
                        balances.getValue(destination), operation.movementFor(destination),
                    )
                }
                if (YearMonth.from(operation.date) != month) continue
                monthRows.add(operation)
                when (operation.kind) {
                    OperationKind.Opening -> Unit
                    OperationKind.Income -> income = Math.addExact(income, operation.amountMinor)
                    OperationKind.Expense -> expense = Math.addExact(expense, operation.amountMinor)
                    OperationKind.Transfer -> expense = Math.addExact(expense, operation.feeMinor)
                }
            }
            return LedgerSnapshot(
                month, accounts.map { AccountBalance(it, balances.getValue(it.id)) },
                monthRows.asReversed(), income, expense,
            )
        }
    }
}
