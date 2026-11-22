package ru.crown.finance.ui

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation

internal fun dateDigits(text: String): String? {
    if (text.any { it !in '0'..'9' && it != '.' && !it.isWhitespace() }) return null
    return text.filter { it in '0'..'9' }.takeIf { it.length <= 8 }
}

internal fun dateInputText(digits: String): String = buildString {
    digits.forEachIndexed { index, digit ->
        if (index == 2 || index == 4) append('.')
        append(digit)
    }
}

internal object DateVisualTransformation : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val formatted = dateInputText(text.text)
        val mapping = object : OffsetMapping {
            override fun originalToTransformed(offset: Int): Int =
                (offset + if (offset > 2) 1 else 0) + if (offset > 4) 1 else 0

            override fun transformedToOriginal(offset: Int): Int =
                formatted.take(offset).count { it != '.' }
        }
        return TransformedText(AnnotatedString(formatted), mapping)
    }
}

@Composable
internal fun DateEntry(value: String, change: (String) -> Unit, label: String, busy: Boolean) {
    val focus = LocalFocusManager.current
    OutlinedTextField(
        value = dateDigits(value).orEmpty(),
        onValueChange = { text -> dateDigits(text)?.let { change(dateInputText(it)) } },
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        placeholder = { Text("ДД.ММ.ГГГГ") },
        enabled = !busy,
        singleLine = true,
        visualTransformation = DateVisualTransformation,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
    )
}
