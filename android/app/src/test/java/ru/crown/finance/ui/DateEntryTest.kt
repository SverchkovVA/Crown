package ru.crown.finance.ui

import androidx.compose.ui.text.AnnotatedString
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class DateEntryTest {
    @Test
    fun acceptsDigitsAndDottedPasteWithoutTruncatingExtraInput() {
        assertEquals("29022024", dateDigits(" 29.02.2024 "))
        assertEquals("29022024", dateDigits("29022024"))
        assertNull(dateDigits("290220240"))
        assertNull(dateDigits("29x02x2024"))
        assertEquals("29.02.2024", dateInputText("29022024"))
        assertEquals("29.0", dateInputText("290"))
        assertEquals("", dateInputText(""))
    }

    @Test
    fun cursorMappingStaysInBoundsAndRoundTripsAtEveryLength() {
        for (length in 0..8) {
            val raw = "29022024".take(length)
            val transformed = DateVisualTransformation.filter(AnnotatedString(raw))
            val mapping = transformed.offsetMapping
            var previous = -1
            for (offset in 0..length) {
                val visual = mapping.originalToTransformed(offset)
                assertTrue(visual in 0..transformed.text.length)
                assertTrue(visual >= previous)
                assertEquals(offset, mapping.transformedToOriginal(visual))
                previous = visual
            }
            for (offset in 0..transformed.text.length) {
                assertTrue(mapping.transformedToOriginal(offset) in 0..length)
            }
        }
    }

    @Test
    fun maskedInputStillUsesStrictCalendarValidation() {
        assertEquals(LocalDate.of(2024, 2, 29), dateValue(dateInputText("29022024")))
        assertNull(dateValue(dateInputText("29022026")))
        assertNull(dateValue(dateInputText("31022026")))
        assertNull(dateValue(dateInputText("2902")))
    }
}
