package ru.crown.finance.ledger

internal data class JournalQuery(
    val text: String = "",
    val kind: OperationKind? = null,
    val categoryId: String? = null,
    val accountId: String? = null,
    val tagId: String? = null,
) {
    init {
        require(text.length <= 100)
        require(listOfNotNull(categoryId, accountId, tagId).all { it.length in 1..64 })
    }
    val count: Int get() = listOf(text.isNotBlank(), kind != null, categoryId != null, accountId != null, tagId != null).count { it }

    fun apply(
        rows: List<Operation>, accounts: List<Account>, categories: List<Category>,
        tagsByOperation: Map<String, Set<String>> = emptyMap(),
    ): List<Operation> {
        val words = text.trim().split(Regex("\\s+")).filter(String::isNotBlank)
        val accountNames = accounts.associate { it.id to it.name }
        val categoryMap = categories.associateBy(Category::id)
        fun includesCategory(id: String?): Boolean {
            var current = id
            val visited = mutableSetOf<String>()
            while (current != null && visited.add(current)) {
                if (current == categoryId) return true
                current = categoryMap[current]?.parentId
            }
            return false
        }
        return rows.filter { operation ->
            val ids = listOfNotNull(operation.categoryId) + operation.parts.mapNotNull(Allocation::categoryId)
            val searchable = listOfNotNull(
                operation.description, accountNames[operation.accountId],
                accountNames[operation.destinationId],
            ) + ids.mapNotNull { categoryMap[it]?.name }
            (accountId == null || operation.accountId == accountId || operation.destinationId == accountId) &&
                (tagId == null || tagId in tagsByOperation[operation.id].orEmpty()) &&
                (kind == null || operation.kind == kind) &&
                (categoryId == null || ids.any(::includesCategory)) &&
                words.all { word -> searchable.any { it.contains(word, ignoreCase = true) } }
        }
    }
}
