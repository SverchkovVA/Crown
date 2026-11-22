package ru.crown.finance.ocrprobe;

import android.app.Activity;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.TextView;
import java.io.File;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Закрытый стенд Activity; не является экраном финансового приложения. */
public final class OcrLifecycleHostActivity extends Activity {
    public static volatile Harness fixture;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private BoundedOcrJob job;
    private Harness harness;
    private boolean active;

    public static final class Harness {
        public final File models;
        public final Bitmap image;
        public final boolean gateProgress;
        public final CountDownLatch progress = new CountDownLatch(1);
        public final CountDownLatch releaseProgress = new CountDownLatch(1);
        public final CountDownLatch resultReady = new CountDownLatch(1);
        public final CountDownLatch releaseDelivery = new CountDownLatch(1);
        public final CountDownLatch finished = new CountDownLatch(1);
        public final CountDownLatch deliveryHandled = new CountDownLatch(1);
        public final AtomicInteger delivered = new AtomicInteger();
        public final AtomicReference<String> outcome = new AtomicReference<>("ожидание");
        public Harness(File models, Bitmap image, boolean gateProgress) {
            this.models = models; this.image = image; this.gateProgress = gateProgress;
        }
        private void await(CountDownLatch latch) {
            try { if (!latch.await(15, TimeUnit.SECONDS)) throw new IllegalStateException("Тестовый барьер не снят"); }
            catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
        }
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        TextView label = new TextView(this); label.setText("Проверка жизненного цикла OCR"); setContentView(label);
        harness = fixture;
        if (harness == null) { finish(); return; }
        job = new BoundedOcrJob(harness.models, percent -> {
            if (harness.gateProgress && percent > 0 && percent < 100) {
                harness.progress.countDown();
                harness.await(harness.releaseProgress);
            }
        });
        worker.execute(() -> {
            harness.outcome.set("распознавание");
            try {
                job.recognize(harness.image);
                harness.resultReady.countDown();
                harness.await(harness.releaseDelivery);
                ui.post(() -> {
                    if (active && !isDestroyed() && !job.isCancelled()) {
                        harness.delivered.incrementAndGet(); harness.outcome.set("принят");
                    } else harness.outcome.set("отброшен");
                    harness.deliveryHandled.countDown();
                });
            } catch (CancellationException cancelled) {
                harness.outcome.set("отменён"); harness.deliveryHandled.countDown();
            } catch (RuntimeException failure) {
                harness.outcome.set("ошибка"); harness.deliveryHandled.countDown();
            } finally { harness.finished.countDown(); }
        });
    }
    @Override public void onStart() { super.onStart(); active = true; }
    @Override public void onStop() {
        active = false;
        if (job != null) job.cancel();
        if (harness != null) { harness.releaseProgress.countDown(); harness.releaseDelivery.countDown(); }
        super.onStop();
    }
    @Override public void onDestroy() {
        if (job != null) job.cancel();
        worker.shutdown();
        super.onDestroy();
    }
}
