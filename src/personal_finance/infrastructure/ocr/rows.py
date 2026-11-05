"""Строки движка с неизменяемыми индексами исходных слов."""

from personal_finance.domain.ocr import OcrDocument


def document_rows(document: OcrDocument) -> list[tuple[str, tuple[int, ...]]]:
    indices = {(w.line_key, w.word_number): i for i, w in enumerate(document.words)}
    return [
        (
            " ".join(w.text for w in line).strip(),
            tuple(indices[(w.line_key, w.word_number)] for w in line),
        )
        for line in document.lines()
    ]
