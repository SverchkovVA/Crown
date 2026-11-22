package ru.crown.finance.ocrprobe;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.googlecode.tesseract.android.TessBaseAPI;
import com.googlecode.tesseract.android.ResultIterator;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.File;
import java.io.InputStream;
import java.io.FileOutputStream;
import static org.junit.Assert.*;

/** Читает только собственный вымышленный рисунок, доступа к книге Crown не имеет. */
@RunWith(AndroidJUnit4.class)
public class OfflineOcrTest {
    @Test public void readsLightCardWithCyrillicAndGeometry() throws Exception { verifyCard(false); }
    @Test public void readsDarkCardWithCyrillicAndGeometry() throws Exception { verifyCard(true); }

    @Test public void readsSixSignedRowsFromSyntheticHistoryWithoutJoiningDifferentRows() throws Exception {
        File root = prepareData();
        Bitmap bitmap = Bitmap.createBitmap(800, 1500, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        canvas.drawColor(Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.BLACK);
        paint.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        paint.setTextSize(26);
        canvas.drawText("05:10", 50, 45, paint);
        paint.setTextSize(40);
        canvas.drawText("История операций", 50, 130, paint);
        canvas.drawText("24 октября 2026", 50, 220, paint);
        String[] amounts = {"+101,25", "-20,50", "-3,75", "+42,00", "-7,10", "-9,90"};
        for (int row = 0; row < amounts.length; row++) {
            int baseline = 340 + row * 160;
            canvas.drawText("Учебная строка " + (row + 1), 50, baseline, paint);
            canvas.drawText(amounts[row] + " RUB", 480, baseline, paint);
        }
        TessBaseAPI engine = new TessBaseAPI();
        try {
            assertTrue(engine.init(root.getAbsolutePath(), "rus+eng"));
            engine.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
            engine.setImage(bitmap);
            engine.getHOCRText(0);
            engine.getUTF8Text();
            ResultIterator words = engine.getResultIterator();
            assertNotNull(words);
            StringBuilder[] rows = new StringBuilder[6];
            for (int row = 0; row < rows.length; row++) rows[row] = new StringBuilder();
            try {
                words.begin();
                do {
                    int[] box = words.getBoundingBox(TessBaseAPI.PageIteratorLevel.RIL_WORD);
                    String text = words.getUTF8Text(TessBaseAPI.PageIteratorLevel.RIL_WORD);
                    if (box != null && box[0] >= 440 && text != null) {
                        int center = (box[1] + box[3]) / 2;
                        for (int row = 0; row < rows.length; row++) {
                            if (Math.abs(center - (320 + row * 160)) <= 35) rows[row].append(text.trim());
                        }
                    }
                } while (words.next(TessBaseAPI.PageIteratorLevel.RIL_WORD));
            } finally { words.delete(); }
            for (int row = 0; row < rows.length; row++) {
                String text = rows[row].toString().replace('−', '-').replace('–', '-').replace('.', ',');
                assertTrue("Не подтверждена сумма со знаком в синтетической строке " + (row + 1), text.startsWith(amounts[row]));
            }
        } finally { engine.recycle(); bitmap.recycle(); }
    }

    @Test(timeout = 30000) public void cancelsRecognizingAtProgressAndAcceptsNextImage() throws Exception {
        File root = prepareData();
        Bitmap page = Bitmap.createBitmap(1024, 2400, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(page);
        canvas.drawColor(Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.BLACK);
        paint.setTextSize(32);
        for (int row = 0; row < 55; row++) canvas.drawText("Учебная строка " + row + " 123,45 RUB", 40, 60 + row * 40, paint);
        java.util.concurrent.atomic.AtomicBoolean stopped = new java.util.concurrent.atomic.AtomicBoolean(false);
        java.util.concurrent.atomic.AtomicReference<TessBaseAPI> reference = new java.util.concurrent.atomic.AtomicReference<>();
        TessBaseAPI engine = new TessBaseAPI(progress -> {
            if (progress.getPercent() > 0 && progress.getPercent() < 100 && stopped.compareAndSet(false, true)) {
                reference.get().stop();
            }
        });
        reference.set(engine);
        Bitmap next = null;
        try {
            assertTrue(engine.init(root.getAbsolutePath(), "rus+eng"));
            engine.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
            engine.setImage(page);
            engine.getHOCRText(0);
            assertTrue("Остановка не запрошена во время распознавания", stopped.get());
            // Частичный результат отменённой задачи не читается и никуда не записывается.
            engine.clear();
            next = Bitmap.createBitmap(720, 220, Bitmap.Config.ARGB_8888);
            Canvas fresh = new Canvas(next);
            fresh.drawColor(Color.WHITE);
            paint.setTextSize(52);
            fresh.drawText("-12,34 RUB", 50, 140, paint);
            engine.setPageSegMode(TessBaseAPI.PageSegMode.PSM_SINGLE_LINE);
            engine.setImage(next);
            engine.getHOCRText(0);
            assertTrue("Следующее изображение не распознано после отмены",
                engine.getUTF8Text().replace('−', '-').replace('–', '-').contains("-12,34"));
        } finally { engine.recycle(); page.recycle(); if (next != null) next.recycle(); }
    }

    static File prepareData() throws Exception {
        Context context = ApplicationProvider.getApplicationContext();
        File root = new File(context.getNoBackupFilesDir(), "synthetic-ocr");
        File data = new File(root, "tessdata");
        assertTrue(data.mkdirs() || data.isDirectory());
        for (String language : new String[]{"rus", "eng"}) {
            try (InputStream input = context.getAssets().open("tessdata/" + language + ".traineddata");
                 FileOutputStream output = new FileOutputStream(new File(data, language + ".traineddata"))) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                output.getFD().sync();
            }
        }
        return root;
    }

    private void verifyCard(boolean dark) throws Exception {
        File root = prepareData();
        Bitmap bitmap = Bitmap.createBitmap(720, 900, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        canvas.drawColor(dark ? Color.BLACK : Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(dark ? Color.WHITE : Color.BLACK);
        paint.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        paint.setTextSize(42);
        canvas.drawText("Детали операции", 50, 100, paint);
        canvas.drawText("27.10.2026", 50, 220, paint);
        canvas.drawText("Учебный магазин", 50, 380, paint);
        paint.setTextSize(54);
        canvas.drawText("-573,19 RUB", 50, 560, paint);
        TessBaseAPI engine = new TessBaseAPI();
        try {
            assertTrue("Модели не загрузились", engine.init(root.getAbsolutePath(), "rus+eng"));
            engine.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
            engine.setImage(bitmap);
            engine.getHOCRText(0);
            String recognized = engine.getUTF8Text();
            assertTrue("Не распознана синтетическая дата", recognized.contains("27.10.2026"));
            assertTrue("Не распознана синтетическая сумма со знаком",
                recognized.replace('−', '-').replace('–', '-').contains("-573,19"));
            assertTrue("Не распознана кириллица", recognized.toLowerCase(java.util.Locale.ROOT).contains("магазин"));
            ResultIterator words = engine.getResultIterator();
            assertNotNull(words);
            try {
                words.begin();
                int count = 0;
                do {
                    int[] box = words.getBoundingBox(TessBaseAPI.PageIteratorLevel.RIL_WORD);
                    assertNotNull(box);
                    assertEquals(4, box.length);
                    assertTrue("Неверная геометрия слова",
                        box[0] >= 0 && box[1] >= 0 && box[2] > box[0] && box[3] > box[1] &&
                        box[2] <= bitmap.getWidth() && box[3] <= bitmap.getHeight());
                    count++;
                } while (words.next(TessBaseAPI.PageIteratorLevel.RIL_WORD));
                assertTrue("Недостаточно распознанных слов", count >= 6);
            } finally { words.delete(); }
        } finally { engine.recycle(); bitmap.recycle(); }
    }
}
