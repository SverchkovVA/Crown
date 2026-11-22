package ru.crown.finance.ledger

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class KeyEnvelopeTest {
    @Test fun replacementChecksExpectedEnvelopeAndLeavesNoStagingFiles() {
        val directory = Files.createTempDirectory("crown-key-test").toFile()
        try {
            val file = java.io.File(directory, "envelope")
            val first = byteArrayOf(1, 2, 3)
            val second = byteArrayOf(4, 5, 6)
            file.writeBytes(first)
            assertThrows(IllegalStateException::class.java) { replaceKeyEnvelope(file, second, second) }
            assertArrayEquals(first, file.readBytes())
            assertThrows(IllegalArgumentException::class.java) { replaceKeyEnvelope(file, first, byteArrayOf()) }
            assertArrayEquals(first, file.readBytes())
            replaceKeyEnvelope(file, first, second)
            assertArrayEquals(second, file.readBytes())
            assertEquals(listOf("envelope"), directory.listFiles()!!.map { it.name })
        } finally { directory.deleteRecursively() }
    }
}
