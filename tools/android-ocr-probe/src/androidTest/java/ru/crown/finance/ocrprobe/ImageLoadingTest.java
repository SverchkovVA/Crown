package ru.crown.finance.ocrprobe;

import android.graphics.*;
import androidx.exifinterface.media.ExifInterface;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.*;
import java.security.MessageDigest;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.*;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class ImageLoadingTest {
    @Test public void pngIsNormalizedOnWhiteAndOriginalHashIsPreserved() throws Exception {
        Bitmap original = Bitmap.createBitmap(3, 2, Bitmap.Config.ARGB_8888);
        original.eraseColor(Color.TRANSPARENT); original.setPixel(1, 0, Color.argb(128,255,0,0));
        byte[] bytes = encode(original, Bitmap.CompressFormat.PNG);
        AtomicBoolean closed = new AtomicBoolean();
        InputStream source = new ByteArrayInputStream(bytes) { @Override public void close() { closed.set(true); } };
        try (BoundedImageLoader.Image image = BoundedImageLoader.load(source, () -> false)) {
            assertEquals(3, image.bitmap.getWidth()); assertEquals(2, image.bitmap.getHeight());
            assertEquals(Color.WHITE, image.bitmap.getPixel(0,0));
            int color = image.bitmap.getPixel(1,0); assertEquals(255, Color.alpha(color)); assertEquals(255, Color.red(color));
            assertTrue(Color.green(color) >= 127 && Color.green(color) <= 128);
            StringBuilder expected = new StringBuilder();
            for (byte value : MessageDigest.getInstance("SHA-256").digest(bytes)) expected.append(String.format(java.util.Locale.ROOT,"%02x",value & 255));
            assertEquals(expected.toString(), image.sourceSha256);
            assertTrue(closed.get()); assertFalse(original.isRecycled());
        } finally { original.recycle(); }
    }
    @Test public void everyExifOrientationMapsPixelsWithoutCropping() {
        Bitmap input = Bitmap.createBitmap(3,2,Bitmap.Config.ARGB_8888);
        int[] colors = {Color.RED,Color.GREEN,Color.BLUE,Color.CYAN,Color.MAGENTA,Color.YELLOW};
        input.setPixels(colors,0,3,0,0,3,2);
        int[][] expected = {{0,1,2,3,4,5},{2,1,0,5,4,3},{5,4,3,2,1,0},{3,4,5,0,1,2},{0,3,1,4,2,5},{3,0,4,1,5,2},{5,2,4,1,3,0},{2,5,1,4,0,3}};
        try {
            for(int orientation=1;orientation<=8;orientation++) {
                Bitmap output=BoundedImageLoader.orient(input,orientation);
                try {
                    assertEquals(orientation >= 5 ? 2 : 3,output.getWidth());
                    assertEquals(orientation >= 5 ? 3 : 2,output.getHeight());
                    int[] pixels=new int[6];output.getPixels(pixels,0,output.getWidth(),0,0,output.getWidth(),output.getHeight());
                    for(int index=0;index<6;index++) assertEquals(colors[expected[orientation-1][index]],pixels[index]);
                } finally { if(output != input)output.recycle(); }
            }
        } finally {input.recycle();}
    }
    @Test public void jpegExifRotationAndUnsupportedFormatsAreHandled() throws Exception {
        android.content.Context context=ApplicationProvider.getApplicationContext();
        File file=File.createTempFile("synthetic-exif-",".jpg",context.getNoBackupFilesDir());
        Bitmap bitmap=Bitmap.createBitmap(40,20,Bitmap.Config.ARGB_8888);bitmap.eraseColor(Color.WHITE);
        try {
            try(FileOutputStream out=new FileOutputStream(file)){out.write(encode(bitmap,Bitmap.CompressFormat.JPEG));}
            ExifInterface exif=new ExifInterface(file.getAbsolutePath());exif.setAttribute(ExifInterface.TAG_ORIENTATION,"6");exif.saveAttributes();
            try(BoundedImageLoader.Image image=BoundedImageLoader.load(new FileInputStream(file),()->false)){
                assertEquals(20,image.bitmap.getWidth());assertEquals(40,image.bitmap.getHeight());
            }
            assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(new ByteArrayInputStream("GIF89a".getBytes(java.nio.charset.StandardCharsets.US_ASCII)),()->false));
        } finally {bitmap.recycle();assertTrue(file.delete());}
    }
    @Test public void truncatedAnimatedAndOversizedSourcesFailClosed() throws Exception {
        Bitmap bitmap=Bitmap.createBitmap(4,4,Bitmap.Config.ARGB_8888);
        byte[] png=encode(bitmap,Bitmap.CompressFormat.PNG);bitmap.recycle();
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(new ByteArrayInputStream(java.util.Arrays.copyOf(png,png.length-12)),()->false));
        ByteArrayOutputStream animated=new ByteArrayOutputStream();animated.write(png,0,33);
        DataOutputStream data=new DataOutputStream(animated);data.writeInt(8);byte[] chunk={'a','c','T','L',0,0,0,1,0,0,0,0};data.write(chunk);
        java.util.zip.CRC32 crc=new java.util.zip.CRC32();crc.update(chunk);data.writeInt((int)crc.getValue());animated.write(png,33,png.length-33);
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(new ByteArrayInputStream(animated.toByteArray()),()->false));
        AtomicBoolean closed=new AtomicBoolean();
        InputStream huge=new InputStream(){int count;@Override public int read(){return 0;}@Override public int read(byte[] block){count+=block.length;return count <= BoundedImageLoader.MAX_BYTES + block.length ? block.length : -1;}@Override public void close(){closed.set(true);}};
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(huge,()->false));assertTrue(closed.get());
    }
    @Test public void cancellationAndNonProgressingStreamsAlwaysCloseInput() throws Exception {
        AtomicBoolean closed=new AtomicBoolean();
        InputStream zero=new InputStream(){@Override public int read(){return 0;}@Override public int read(byte[] b){return 0;}@Override public void close(){closed.set(true);}};
        assertThrows(IOException.class,()->BoundedImageLoader.load(zero,()->false));assertTrue(closed.get());
        closed.set(false);
        InputStream cancelled=new ByteArrayInputStream(new byte[100]){@Override public void close(){closed.set(true);}};
        assertThrows(CancellationException.class,()->BoundedImageLoader.load(cancelled,()->true));assertTrue(closed.get());
    }
    @Test public void multiImageJpegIsRejectedAndLegalMarkerPaddingIsAccepted() throws Exception {
        Bitmap image=Bitmap.createBitmap(40,20,Bitmap.Config.ARGB_8888);image.eraseColor(Color.WHITE);
        byte[] jpeg=encode(image,Bitmap.CompressFormat.JPEG);image.recycle();
        ByteArrayOutputStream joined=new ByteArrayOutputStream();joined.write(jpeg);joined.write(jpeg);
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(new ByteArrayInputStream(joined.toByteArray()),()->false));
        ByteArrayOutputStream mpo=new ByteArrayOutputStream();mpo.write(jpeg,0,2);mpo.write(new byte[]{(byte)255,(byte)226,0,6,'M','P','F',0});mpo.write(jpeg,2,jpeg.length-2);
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(new ByteArrayInputStream(mpo.toByteArray()),()->false));
        ByteArrayOutputStream padded=new ByteArrayOutputStream();padded.write(jpeg,0,2);padded.write(255);padded.write(jpeg,2,jpeg.length-2);
        try(BoundedImageLoader.Image decoded=BoundedImageLoader.load(new ByteArrayInputStream(padded.toByteArray()),()->false)){
            assertEquals(40,decoded.bitmap.getWidth());assertEquals(20,decoded.bitmap.getHeight());
        }
    }
    @Test public void invalidCancellationAndMainThreadCloseStreamBeforeRejecting() throws Exception {
        AtomicBoolean closed=new AtomicBoolean();
        InputStream invalid=new ByteArrayInputStream(new byte[1]){@Override public void close(){closed.set(true);}};
        assertThrows(IllegalArgumentException.class,()->BoundedImageLoader.load(invalid,null));assertTrue(closed.get());
        closed.set(false);
        AtomicReference<Throwable> failure=new AtomicReference<>();
        InputStream main=new ByteArrayInputStream(new byte[1]){@Override public void close(){closed.set(true);}};
        androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().runOnMainSync(()->{
            try{BoundedImageLoader.load(main,()->false);}catch(Throwable error){failure.set(error);}
        });
        assertTrue(failure.get() instanceof IllegalStateException);assertTrue(closed.get());
    }
    private byte[] encode(Bitmap bitmap,Bitmap.CompressFormat format) {
        ByteArrayOutputStream out=new ByteArrayOutputStream();assertTrue(bitmap.compress(format,100,out));return out.toByteArray();
    }
}
