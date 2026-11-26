package ru.crown.finance.ledger

import java.io.File

internal class BookLocations(private val root: File) {
    fun path(number: Int): File {
        require(number > 0)
        return File(root, if (number == 1) "mobile-book-v1" else "mobile-book-v2-$number")
    }

    fun numbers(): List<Int> = root.list().orEmpty().mapNotNull { name ->
        if (name == "mobile-book-v1") {
            1
        } else {
            name.removePrefix("mobile-book-v2-").toIntOrNull()
                ?.takeIf { it > 1 && path(it).name == name }
        }
    }.sorted()

    fun nextNumber(): Int = Math.addExact(numbers().lastOrNull() ?: 0, 1)
}
