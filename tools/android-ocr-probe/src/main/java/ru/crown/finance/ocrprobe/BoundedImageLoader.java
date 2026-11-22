package ru.crown.finance.ocrprobe;

import android.graphics.*;
import androidx.exifinterface.media.ExifInterface;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;

/** Ограниченный загрузчик пробы: не открывает пути, URI или сеть самостоятельно. */
public final class BoundedImageLoader {
    public static final int MAX_BYTES = 12 * 1024 * 1024;
    private BoundedImageLoader() {}
    public static final class Image implements AutoCloseable {
        public final Bitmap bitmap;
        public final String sourceSha256;
        private Image(Bitmap bitmap, String hash) { this.bitmap = bitmap; sourceSha256 = hash; }
        @Override public void close() { bitmap.recycle(); }
    }
    private static void check(BooleanSupplier cancelled) {
        if (cancelled.getAsBoolean() || Thread.currentThread().isInterrupted()) throw new CancellationException("Загрузка изображения отменена");
    }
    /** Принимает владение потоком и закрывает его при любом исходе. */
    public static Image load(InputStream source, BooleanSupplier cancelled) throws IOException {
        byte[] raw = read(source, cancelled);
        Bitmap decoded = null, oriented = null, normalized = null;
        boolean returned = false;
        try {
            check(cancelled);
            BitmapFactory.Options options = new BitmapFactory.Options(); options.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(raw, 0, raw.length, options);
            boolean png = "image/png".equals(options.outMimeType);
            boolean jpeg = "image/jpeg".equals(options.outMimeType);
            if (!png && !jpeg) throw new IllegalArgumentException("Для OCR нужен PNG или JPEG");
            BoundedOcrJob.validateDimensions(options.outWidth, options.outHeight);
            int orientation = ExifInterface.ORIENTATION_NORMAL;
            if (png) validatePng(raw, cancelled);
            else {
                validateJpeg(raw, cancelled);
            }
            orientation = new ExifInterface(new ByteArrayInputStream(raw)).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL);
            if (orientation < 0 || orientation > 8) throw new IllegalArgumentException("Неизвестный поворот изображения");
            check(cancelled);
            options.inJustDecodeBounds = false; options.inPreferredConfig = Bitmap.Config.ARGB_8888; options.inScaled = false;
            decoded = BitmapFactory.decodeByteArray(raw, 0, raw.length, options);
            if (decoded == null) throw new IllegalArgumentException("Не удалось декодировать изображение");
            BoundedOcrJob.validateDimensions(decoded.getWidth(), decoded.getHeight());
            oriented = orient(decoded, orientation);
            normalized = Bitmap.createBitmap(oriented.getWidth(), oriented.getHeight(), Bitmap.Config.ARGB_8888);
            normalized.setDensity(Bitmap.DENSITY_NONE);
            Canvas canvas = new Canvas(normalized); canvas.drawColor(Color.WHITE); canvas.drawBitmap(oriented, 0, 0, null);
            check(cancelled);
            byte[] digest;
            try { digest = MessageDigest.getInstance("SHA-256").digest(raw); }
            catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
            StringBuilder hash = new StringBuilder(64);
            for (byte value : digest) hash.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            Image result = new Image(normalized, hash.toString()); returned = true; return result;
        } finally {
            Arrays.fill(raw, (byte) 0);
            if (oriented != null && oriented != decoded) oriented.recycle();
            if (decoded != null) decoded.recycle();
            if (!returned && normalized != null) normalized.recycle();
        }
    }
    private static byte[] read(InputStream source, BooleanSupplier cancelled) throws IOException {
        if (source == null) throw new IllegalArgumentException("Не задан источник изображения");
        byte[] block = new byte[65536];
        WipingBuffer buffer = new WipingBuffer();
        try (InputStream input = source) {
            if (cancelled == null) throw new IllegalArgumentException("Не задан признак отмены");
            if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) throw new IllegalStateException("Загрузка OCR выполняется вне потока интерфейса");
            while (true) {
                check(cancelled);
                int count = input.read(block);
                if (count < 0) break;
                if (count == 0) throw new IOException("Поток изображения не возвращает данные");
                if ((long) buffer.size() + count > MAX_BYTES) throw new IllegalArgumentException("Изображение больше 12 МиБ");
                buffer.write(block, 0, count);
            }
            if (buffer.size() == 0) throw new IllegalArgumentException("Изображение пустое");
            return buffer.toByteArray();
        } finally { Arrays.fill(block, (byte) 0); buffer.wipe(); }
    }
    private static final class WipingBuffer extends ByteArrayOutputStream {
        void wipe() { Arrays.fill(buf, (byte) 0); reset(); }
    }
    private static void validatePng(byte[] bytes, BooleanSupplier cancelled) {
        byte[] signature = {(byte)137,80,78,71,13,10,26,10};
        if (bytes.length < 33 || !Arrays.equals(signature, Arrays.copyOf(bytes, 8))) throw new IllegalArgumentException("Неверный PNG");
        int position = 8, index = 0;
        while (position <= bytes.length - 12) {
            check(cancelled);
            long length = 0;
            for (int offset = 0; offset < 4; offset++) length = length * 256 + (bytes[position + offset] & 255);
            long next = position + length + 12;
            if (next > bytes.length) throw new IllegalArgumentException("PNG обрезан");
            String type = new String(bytes, position + 4, 4, StandardCharsets.US_ASCII);
            if ((index == 0 && (!"IHDR".equals(type) || length != 13)) || (index > 0 && "IHDR".equals(type)))
                throw new IllegalArgumentException("Неверный заголовок PNG");
            if ("acTL".equals(type)) throw new IllegalArgumentException("Для OCR нужен один неподвижный снимок");
            if ("IEND".equals(type)) {
                if (length != 0 || next != bytes.length) throw new IllegalArgumentException("Лишние данные после PNG");
                return;
            }
            position = (int) next; index++;
        }
        throw new IllegalArgumentException("PNG не завершён");
    }
    private static void validateJpeg(byte[] raw, BooleanSupplier cancelled) {
        if (raw.length < 4 || (raw[0] & 255) != 255 || (raw[1] & 255) != 216)
            throw new IllegalArgumentException("Неверный JPEG");
        int position = 2; boolean scan = false, sawScan = false;
        while (position < raw.length) {
            check(cancelled);
            if (scan) while (position < raw.length && (raw[position] & 255) != 255) position++;
            if (position >= raw.length || (raw[position] & 255) != 255) break;
            while (position < raw.length && (raw[position] & 255) == 255) position++;
            if (position >= raw.length) break;
            int marker = raw[position++] & 255;
            if (marker == 0 && scan) continue;
            if (marker >= 208 && marker <= 215 && scan) continue;
            if (marker == 1) continue;
            boolean wasScan = scan; scan = false;
            if (marker == 217) {
                if (!sawScan || position != raw.length) throw new IllegalArgumentException("JPEG содержит несколько снимков или лишние данные");
                return;
            }
            if (marker == 0 || marker == 216 || (marker >= 208 && marker <= 215) || position > raw.length - 2) break;
            int length = (raw[position] & 255) * 256 + (raw[position + 1] & 255);
            if (length < 2 || (long) position + length > raw.length) break;
            if (marker == 226 && length >= 6 && raw[position+2] == 'M' && raw[position+3] == 'P' && raw[position+4] == 'F' && raw[position+5] == 0)
                throw new IllegalArgumentException("Для OCR нужен один JPEG, а не MPO");
            position += length;
            if (marker == 218) { scan = true; sawScan = true; }
            else if (marker == 220 && wasScan) scan = true;
        }
        throw new IllegalArgumentException("JPEG обрезан или имеет неверную структуру");
    }
    static Bitmap orient(Bitmap source, int orientation) {
        Matrix matrix = new Matrix();
        switch (orientation) {
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL: matrix.setScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_180: matrix.setRotate(180); break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL: matrix.setScale(1, -1); break;
            case ExifInterface.ORIENTATION_TRANSPOSE: matrix.setValues(new float[]{0,1,0,1,0,0,0,0,1}); break;
            case ExifInterface.ORIENTATION_ROTATE_90: matrix.setRotate(90); break;
            case ExifInterface.ORIENTATION_TRANSVERSE: matrix.setValues(new float[]{0,-1,0,-1,0,0,0,0,1}); break;
            case ExifInterface.ORIENTATION_ROTATE_270: matrix.setRotate(270); break;
            default: return source;
        }
        return Bitmap.createBitmap(source, 0, 0, source.getWidth(), source.getHeight(), matrix, false);
    }
}
