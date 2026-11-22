package ru.crown.finance.ocrprobe;

import android.graphics.Bitmap;
import android.os.Looper;
import com.googlecode.tesseract.android.ResultIterator;
import com.googlecode.tesseract.android.TessBaseAPI;
import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.IntConsumer;

/** Изолированная проба жизненного цикла. В рабочее Crown пока не подключена. */
public final class BoundedOcrJob {
    public static final int MAX_SIDE = 10000;
    public static final long MAX_PIXELS = 8_000_000L;
    public static final int MAX_WORDS = 10000;
    public static final int MAX_CHARACTERS = 1_000_000;
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private final AtomicBoolean started = new AtomicBoolean();
    private final AtomicReference<RuntimeException> progressFailure = new AtomicReference<>();
    private final File models;
    private final IntConsumer progress;

    public BoundedOcrJob(File models, IntConsumer progress) {
        if (models == null) throw new IllegalArgumentException("Не заданы локальные модели OCR");
        this.models = models;
        this.progress = progress;
    }
    /** Вызывается из любого потока; JNI и освобождение памяти остаются в рабочем потоке. */
    public void cancel() { cancelled.set(true); }
    public boolean isCancelled() { return cancelled.get(); }
    private void checkCancelled() {
        if (progressFailure.get() != null) throw new IllegalStateException("Не удалось передать прогресс OCR", progressFailure.get());
        if (cancelled.get() || Thread.currentThread().isInterrupted()) {
            cancelled.set(true);
            throw new CancellationException("Распознавание отменено");
        }
    }
    public static void validateDimensions(int width, int height) {
        if (width < 1 || height < 1 || width > MAX_SIDE || height > MAX_SIDE || (long) width * height > MAX_PIXELS)
            throw new IllegalArgumentException("Изображение OCR превышает допустимые размеры");
    }
    public List<Word> recognize(Bitmap image) {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IllegalStateException("OCR выполняется вне потока интерфейса");
        if (!started.compareAndSet(false, true)) throw new IllegalStateException("Задача OCR уже запускалась");
        checkCancelled();
        if (image == null || image.isRecycled()) throw new IllegalArgumentException("Изображение недоступно");
        validateDimensions(image.getWidth(), image.getHeight());
        AtomicReference<TessBaseAPI> reference = new AtomicReference<>();
        TessBaseAPI engine = new TessBaseAPI(value -> {
            if (progress != null) try { progress.accept(value.getPercent()); }
            catch (RuntimeException failure) { progressFailure.compareAndSet(null, failure); cancelled.set(true); }
            if (cancelled.get() || Thread.currentThread().isInterrupted()) {
                cancelled.set(true);
                reference.get().stop();
            }
        });
        reference.set(engine);
        try {
            if (!engine.init(models.getAbsolutePath(), "rus+eng")) throw new IllegalStateException("Локальные модели OCR не загрузились");
            checkCancelled();
            engine.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
            engine.setImage(image);
            checkCancelled();
            // В отличие от getUTF8Text этот запуск допускает stop из прогресса.
            engine.getHOCRText(0);
            checkCancelled();
            ResultIterator iterator = engine.getResultIterator();
            if (iterator == null) return Collections.emptyList();
            List<Word> words = new ArrayList<>();
            int characters = 0;
            try {
                iterator.begin();
                do {
                    checkCancelled();
                    String text = iterator.getUTF8Text(TessBaseAPI.PageIteratorLevel.RIL_WORD);
                    if (text == null || text.trim().isEmpty()) continue;
                    text = text.trim();
                    int[] box = iterator.getBoundingBox(TessBaseAPI.PageIteratorLevel.RIL_WORD);
                    if (box == null || box.length != 4 || box[0] < 0 || box[1] < 0 || box[2] <= box[0] || box[3] <= box[1] || box[2] > image.getWidth() || box[3] > image.getHeight())
                        throw new IllegalStateException("Неверная геометрия результата OCR");
                    characters = Math.addExact(characters, text.length());
                    if (words.size() >= MAX_WORDS || characters > MAX_CHARACTERS) throw new IllegalArgumentException("Результат OCR превышает допустимый объём");
                    words.add(new Word(text, box, iterator.confidence(TessBaseAPI.PageIteratorLevel.RIL_WORD)));
                } while (iterator.next(TessBaseAPI.PageIteratorLevel.RIL_WORD));
            } finally { iterator.delete(); }
            checkCancelled();
            return Collections.unmodifiableList(words);
        } finally { engine.recycle(); }
    }
    public static final class Word {
        public final String text;
        public final int left, top, right, bottom;
        /** Оценка OCR не является вероятностью и не подтверждает финансовые поля. */
        public final float confidence;
        private Word(String text, int[] box, float confidence) {
            if (!Float.isFinite(confidence) || confidence < 0 || confidence > 100) throw new IllegalStateException("Неверная оценка OCR");
            this.text = text; left = box[0]; top = box[1]; right = box[2]; bottom = box[3]; this.confidence = confidence;
        }
    }
}
