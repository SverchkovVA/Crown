package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.math.BigDecimal
import java.time.LocalDate

class DepositsTest {
    private val start = LocalDate.of(2023, 12, 30)
    private fun sample() = DepositScenario(
        start, LocalDate.of(2024, 1, 4), 100001,
        listOf(DepositRate("rate0", start, BigDecimal("7.12345678")), DepositRate("rate1", LocalDate.of(2024, 1, 2), BigDecimal("12.5"))),
        listOf(DepositMovement("a", LocalDate.of(2023, 12, 31), 50003), DepositMovement("b", LocalDate.of(2024, 1, 2), -25007), DepositMovement("c", LocalDate.of(2024, 1, 2), 100)),
        listOf(LocalDate.of(2024, 1, 1), LocalDate.of(2024, 1, 4)), capitalize = true, allowTopups = true, allowWithdrawals = true,
    )

    @Test fun everyPolicyMatchesIndependentRationalOracleAcrossLeapYear() {
        val path = listOf(File("../../shared/android-fixtures/deposits-v1.tsv"), File("shared/android-fixtures/deposits-v1.tsv")).first { it.isFile }
        val groups = path.readLines().map { it.split('\t') }.groupBy { it.take(6) }
        assertEquals(96, groups.size)
        for ((options, rows) in groups) {
            val value = sample().copy(
                boundary = AccrualBoundary.entries.single { it.wire == options[0] }, moment = BalanceMoment.entries.single { it.wire == options[1] },
                dayCount = DepositDayCount.entries.single { it.wire == options[2] }, rounding = DepositRounding.entries.single { it.wire == options[3] },
                roundingStage = RoundingStage.entries.single { it.wire == options[4] }, capitalize = options[5] == "1",
            )
            val forecast = forecastDeposit(value)
            assertEquals(6, forecast.days.size)
            rows.forEach { row ->
                val day = forecast.days[row[6].toInt()]
                assertEquals(row[7].toLong(), day.base); assertEquals(row[8].toLong(), day.movement)
                assertEquals(row[9].toBigInteger(), day.interestNumerator); assertEquals(row[10].toLong(), day.payout)
                assertEquals(row[11].toLong(), day.capitalized); assertEquals(row[12].toLong(), day.closing)
            }
        }
    }

    @Test fun halfKopeckAndDailyRoundingAreExact() {
        val opened = LocalDate.of(2026, 1, 1)
        val scenario = DepositScenario(opened, opened.plusYears(1), 1, listOf(DepositRate("r", opened, BigDecimal("50"))), emptyList(), listOf(opened.plusYears(1)),
            boundary = AccrualBoundary.OpeningIncluded, dayCount = DepositDayCount.Fixed365)
        for ((mode, expected) in listOf(DepositRounding.HalfUp to 1L, DepositRounding.HalfEven to 0L, DepositRounding.Down to 0L)) {
            assertEquals(expected.toBigInteger(), forecastDeposit(scenario.copy(rounding = mode)).interest)
            assertEquals(0.toBigInteger(), forecastDeposit(scenario.copy(rounding = mode, roundingStage = RoundingStage.Daily)).interest)
        }
    }

    @Test fun canonicalCodecPreservesRulesMovementsAndForecast() {
        val value = sample()
        val encoded = encodeDeposit(value)
        val restored = decodeDeposit(encoded)
        assertEquals(encoded, encodeDeposit(restored))
        assertEquals(encoded, encodeDeposit(value.copy(rates = value.rates.reversed(), movements = value.movements.reversed(), payouts = value.payouts.reversed())))
        assertEquals(forecastDeposit(value).days, forecastDeposit(restored).days)
        assertThrows(Exception::class.java) { decodeDeposit(encoded.dropLast(5)) }
        val bytes = java.util.Base64.getDecoder().decode(encoded)
        bytes[3] = 2
        assertThrows(IllegalArgumentException::class.java) { decodeDeposit(java.util.Base64.getEncoder().encodeToString(bytes)) }
    }

    @Test fun invalidPermissionsDatesRatesAndBalancesAreRejected() {
        val value = sample()
        for (invalid in listOf(
            value.copy(allowTopups = false), value.copy(allowWithdrawals = false),
            value.copy(initial = 0), value.copy(minimum = 100002),
            value.copy(matures = start), value.copy(payouts = listOf(start)),
            value.copy(rates = value.rates + value.rates.first()),
            value.copy(movements = value.movements + value.movements.first()),
            value.copy(movements = listOf(DepositMovement("bad", start, -100002))),
            value.copy(rates = listOf(DepositRate("bad", start, BigDecimal("0.000000001")))),
            value.copy(initial = Long.MAX_VALUE),
        )) assertThrows(IllegalArgumentException::class.java) { forecastDeposit(invalid) }
        assertNull(parseDepositRate("NaN")); assertNull(parseDepositRate("1e2")); assertNull(parseDepositRate("1000.1"))
        assertEquals(BigDecimal("7.12345678"), parseDepositRate("7,12345678"))
    }

    @Test fun monthlyDatesKeepOriginalDayAndCalculationCanBeCancelled() {
        val opened = LocalDate.of(2024, 1, 31)
        assertEquals(listOf(LocalDate.of(2024, 2, 29), LocalDate.of(2024, 3, 31), LocalDate.of(2024, 4, 30)), monthlyDepositPayouts(opened, LocalDate.of(2024, 4, 30)))
        var checked = 0
        assertThrows(java.util.concurrent.CancellationException::class.java) {
            forecastDeposit(sample()) { if (++checked == 2) throw java.util.concurrent.CancellationException() }
        }
        assertEquals(2, checked)
    }

    @Test fun backupRetainsScenarioVersionsWithoutInventingMoney() {
        val payload = encodeDeposit(sample())
        val events = listOf(DepositEvent(1, "d", "Учебный вклад", payload, false, 1), DepositEvent(2, "d", "Учебный вклад", payload, true, 2))
        val ledger = BackupLedger("Синтетика", emptyList(), emptyList(), emptyList(), depositEvents = events)
        assertEquals(ledger, PortableBackup.open(PortableBackup.seal(ledger, "copy-only".toCharArray()).inputStream(), "copy-only".toCharArray()))
        assertThrows(IllegalArgumentException::class.java) { ledger.copy(depositEvents = events.reversed()).validate() }
    }
}
