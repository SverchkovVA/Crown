package ru.crown.finance.ocrprobe;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.File;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class LifecycleOcrTest {
    @Test public void dimensionsRejectOverflowAndOversizedImagesBeforeNativeWork() {
        BoundedOcrJob.validateDimensions(10000, 800);
        for (int[] size : new int[][]{{0,1},{1,0},{-1,1},{10001,1},{10000,801},{Integer.MAX_VALUE,Integer.MAX_VALUE}}) {
            assertThrows(IllegalArgumentException.class, () -> BoundedOcrJob.validateDimensions(size[0], size[1]));
        }
        BoundedOcrJob job = new BoundedOcrJob(new File("/missing-models"), null);
        assertThrows(IllegalArgumentException.class, () -> job.recognize(null));
    }

    @Test public void resultIsImmutableAndJobCannotRunTwice() throws Exception {
        Bitmap image = smallCard();
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            BoundedOcrJob job = new BoundedOcrJob(OfflineOcrTest.prepareData(), null);
            List<BoundedOcrJob.Word> result = worker.submit(() -> job.recognize(image)).get(30, TimeUnit.SECONDS);
            assertTrue(join(result).replace('−','-').replace('–','-').contains("-12,34"));
            assertThrows(UnsupportedOperationException.class, () -> result.clear());
            ExecutionException failed = assertThrows(ExecutionException.class, () -> worker.submit(() -> job.recognize(image)).get(10, TimeUnit.SECONDS));
            assertTrue(failed.getCause() instanceof IllegalStateException);
            for (BoundedOcrJob.Word word : result) {
                assertTrue(word.left >= 0 && word.top >= 0 && word.right <= image.getWidth() && word.bottom <= image.getHeight());
            }
            job.cancel();
            assertTrue(job.isCancelled());
            assertFalse(image.isRecycled());
        } finally { worker.shutdownNow(); assertTrue(worker.awaitTermination(30, TimeUnit.SECONDS)); image.recycle(); }
    }

    @Test public void mainThreadAndCancellationBeforeStartNeverEnterNativeOcr() throws Exception {
        File root = OfflineOcrTest.prepareData();
        Bitmap image = smallCard();
        try {
            AtomicReference<Throwable> error = new AtomicReference<>();
            InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> {
                try { new BoundedOcrJob(root, null).recognize(image); } catch (Throwable failure) { error.set(failure); }
            });
            assertTrue(error.get() instanceof IllegalStateException);
            BoundedOcrJob cancelled = new BoundedOcrJob(root, null);
            cancelled.cancel();
            assertThrows(CancellationException.class, () -> cancelled.recognize(image));
            assertFalse(image.isRecycled());
        } finally { image.recycle(); }
    }

    @Test(timeout = 60000) public void mainThreadCancelDuringProgressDiscardsResultAndNextJobWorks() throws Exception {
        File root = OfflineOcrTest.prepareData();
        Bitmap image = Bitmap.createBitmap(1400, 2800, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image); canvas.drawColor(Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG); paint.setColor(Color.BLACK); paint.setTextSize(34);
        for (int row = 0; row < 65; row++) canvas.drawText("Учебная строка " + row + " -123,45 RUB", 40, 50 + row * 40, paint);
        CountDownLatch recognizing = new CountDownLatch(1), cancelDelivered = new CountDownLatch(1);
        ExecutorService worker = Executors.newSingleThreadExecutor();
        Bitmap next = null;
        try {
            BoundedOcrJob job = new BoundedOcrJob(root, percent -> {
                if (percent > 0 && percent < 100) {
                    recognizing.countDown();
                    try { if (!cancelDelivered.await(10, TimeUnit.SECONDS)) throw new IllegalStateException("Отмена не доставлена"); }
                    catch (InterruptedException stopped) { Thread.currentThread().interrupt(); }
                }
            });
            Future<List<BoundedOcrJob.Word>> task = worker.submit(() -> job.recognize(image));
            assertTrue("Нет прогресса OCR", recognizing.await(30, TimeUnit.SECONDS));
            InstrumentationRegistry.getInstrumentation().runOnMainSync(job::cancel);
            cancelDelivered.countDown();
            ExecutionException stopped = assertThrows(ExecutionException.class, () -> task.get(20, TimeUnit.SECONDS));
            assertTrue(stopped.getCause() instanceof CancellationException);
            assertTrue(job.isCancelled());
            assertFalse(image.isRecycled());
            next = smallCard();
            final Bitmap fresh = next;
            List<BoundedOcrJob.Word> result = worker.submit(() -> new BoundedOcrJob(root, null).recognize(fresh)).get(20, TimeUnit.SECONDS);
            assertTrue(join(result).replace('−','-').replace('–','-').contains("-12,34"));
        } finally {
            cancelDelivered.countDown();
            worker.shutdownNow(); assertTrue(worker.awaitTermination(30, TimeUnit.SECONDS));
            image.recycle(); if (next != null) next.recycle();
        }
    }

    @Test public void failedProgressConsumerStopsCleanlyWithoutReturningPartialWords() throws Exception {
        File root = OfflineOcrTest.prepareData();
        Bitmap image = smallCard();
        ExecutorService worker = Executors.newSingleThreadExecutor();
        try {
            BoundedOcrJob job = new BoundedOcrJob(root, percent -> { throw new IllegalStateException("Синтетический отказ потребителя"); });
            ExecutionException stopped = assertThrows(ExecutionException.class, () -> worker.submit(() -> job.recognize(image)).get(30, TimeUnit.SECONDS));
            assertTrue(stopped.getCause() instanceof IllegalStateException);
            assertTrue(job.isCancelled());
            assertFalse(image.isRecycled());
        } finally { worker.shutdownNow(); assertTrue(worker.awaitTermination(30, TimeUnit.SECONDS)); image.recycle(); }
    }
    private Bitmap smallCard() {
        Bitmap image = Bitmap.createBitmap(720, 300, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image); canvas.drawColor(Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG); paint.setColor(Color.BLACK); paint.setTextSize(48);
        canvas.drawText("Учебный магазин", 40, 90, paint);
        canvas.drawText("-12,34 RUB", 40, 200, paint);
        return image;
    }
    private String join(List<BoundedOcrJob.Word> words) {
        StringBuilder text = new StringBuilder();
        for (BoundedOcrJob.Word word : words) text.append(word.text);
        return text.toString();
    }
}
