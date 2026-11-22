package ru.crown.finance.ocrprobe;

import android.content.Intent;
import android.graphics.*;
import androidx.lifecycle.Lifecycle;
import androidx.test.core.app.ActivityScenario;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.concurrent.TimeUnit;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ActivityLifecycleOcrTest {
    @Test(timeout = 60000) public void stoppingActivityCancelsNativeWorkWithoutUiDelivery() throws Exception {
        Bitmap image = page(true);
        OcrLifecycleHostActivity.Harness fixture = new OcrLifecycleHostActivity.Harness(OfflineOcrTest.prepareData(), image, true);
        OcrLifecycleHostActivity.fixture = fixture;
        ActivityScenario<OcrLifecycleHostActivity> scenario = null;
        try {
            scenario = ActivityScenario.launch(new Intent(ApplicationProvider.getApplicationContext(), OcrLifecycleHostActivity.class));
            assertTrue("Распознавание не началось", fixture.progress.await(30, TimeUnit.SECONDS));
            scenario.moveToState(Lifecycle.State.CREATED);
            assertTrue("Рабочая задача не завершилась", fixture.finished.await(20, TimeUnit.SECONDS));
            assertEquals("отменён", fixture.outcome.get());
            assertEquals(0, fixture.delivered.get());
            scenario.moveToState(Lifecycle.State.RESUMED);
            assertEquals(0, fixture.delivered.get());
        } finally {
            fixture.releaseProgress.countDown(); fixture.releaseDelivery.countDown();
            if (scenario != null) scenario.close();
            assertTrue(fixture.finished.await(30, TimeUnit.SECONDS));
            OcrLifecycleHostActivity.fixture = null; image.recycle();
        }
    }
    @Test(timeout = 60000) public void destroyedActivityDiscardsAlreadyRecognizedLateResult() throws Exception {
        Bitmap image = page(false);
        OcrLifecycleHostActivity.Harness fixture = new OcrLifecycleHostActivity.Harness(OfflineOcrTest.prepareData(), image, false);
        OcrLifecycleHostActivity.fixture = fixture;
        ActivityScenario<OcrLifecycleHostActivity> scenario = null;
        try {
            scenario = ActivityScenario.launch(new Intent(ApplicationProvider.getApplicationContext(), OcrLifecycleHostActivity.class));
            assertTrue("Результат не подготовлен", fixture.resultReady.await(30, TimeUnit.SECONDS));
            scenario.moveToState(Lifecycle.State.DESTROYED);
            assertTrue(fixture.finished.await(20, TimeUnit.SECONDS));
            assertTrue(fixture.deliveryHandled.await(10, TimeUnit.SECONDS));
            assertEquals(0, fixture.delivered.get());
            assertEquals("отброшен", fixture.outcome.get());
        } finally {
            fixture.releaseProgress.countDown(); fixture.releaseDelivery.countDown();
            if (scenario != null) scenario.close();
            assertTrue(fixture.finished.await(30, TimeUnit.SECONDS));
            OcrLifecycleHostActivity.fixture = null; image.recycle();
        }
    }
    private Bitmap page(boolean large) {
        Bitmap image = Bitmap.createBitmap(large ? 1400 : 720, large ? 2800 : 300, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image); canvas.drawColor(Color.WHITE);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG); paint.setColor(Color.BLACK); paint.setTextSize(large ? 34 : 48);
        if (large) for (int row = 0; row < 65; row++) canvas.drawText("Учебная строка " + row + " -123,45 RUB", 40, 50 + row * 40, paint);
        else { canvas.drawText("Учебный магазин", 40, 90, paint); canvas.drawText("-12,34 RUB", 40, 200, paint); }
        return image;
    }
}
