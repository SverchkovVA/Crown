package ru.crown.finance.ocrprobe;

import android.graphics.*;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.core.app.ApplicationProvider;
import android.content.Context;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.io.File;
import java.time.LocalDate;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class OperationCandidatesTest {
    private OperationCandidates.Token word(String value,int x,int y,int width,int height) {
        return new OperationCandidates.Token(value,x,y,x+width,y+height);
    }
    private OperationCandidates.Result parse(List<OperationCandidates.Token> words,OperationCandidates.Mode mode) {
        return OperationCandidates.analyze(words,1000,1700,2026,"RUB",mode);
    }
    @Test public void clockAndBonusAreNotMoneyAndFallbacksAreMarked() {
        List<OperationCandidates.Token> words=Arrays.asList(
            word("05:10",30,20,70,24),word("24",30,100,30,26),word("октября",70,100,130,26),
            word("Покупка",30,210,150,30),word("-20,50",500,210,150,30),
            word("+1",30,310,40,26),word("бонус",90,310,100,26));
        OperationCandidates.Result result=OperationCandidates.analyze(words,1000,1700,2026,"USD",OperationCandidates.Mode.HISTORY);
        assertEquals(1,result.proposals.size());assertEquals(1,result.nonCashRows);
        OperationCandidates.Proposal row=result.proposals.get(0);
        assertEquals(Long.valueOf(-2050),row.signedMinor());assertEquals("USD",row.currency);
        assertTrue(row.defaultCurrency);assertTrue(row.defaultYear);assertEquals(LocalDate.of(2026,10,24),row.date);
        assertNull(row.time);assertTrue(row.hasAllFields());
        assertTrue(parse(Collections.singletonList(word("05.10",30,20,90,24)),OperationCandidates.Mode.HISTORY).proposals.isEmpty());
        assertTrue(parse(Collections.singletonList(word("05,10",30,20,90,24)),OperationCandidates.Mode.CARD).proposals.isEmpty());
    }
    @Test public void comparableCardAmountsAndCroppedAmountRequireReview() {
        OperationCandidates.Result competing=parse(Arrays.asList(
            word("-100,00 RUB",200,300,300,40),word("-50,00 RUB",200,500,300,40)),OperationCandidates.Mode.CARD);
        assertEquals(1,competing.proposals.size());assertTrue(competing.proposals.get(0).ambiguous);
        assertNull(competing.proposals.get(0).minor);assertFalse(competing.proposals.get(0).hasAllFields());
        OperationCandidates.Proposal cropped=parse(Collections.singletonList(word("-10,00 RUB",0,300,250,40)),OperationCandidates.Mode.CARD).proposals.get(0);
        assertTrue(cropped.cropped);assertFalse(cropped.hasAllFields());
    }
    @Test public void disconnectedDecimalFragmentsNeverBecomeConfidentMoney() {
        OperationCandidates.Result result=parse(Arrays.asList(
            word("573",40,300,100,40),word("19",600,300,70,40),word("RUB",680,300,90,40)),OperationCandidates.Mode.CARD);
        assertEquals(1,result.proposals.size());assertNull(result.proposals.get(0).minor);
        assertTrue(result.proposals.get(0).ambiguous);assertFalse(result.proposals.get(0).hasAllFields());
    }
    @Test public void invalidDateClearsInheritanceAndClockIsNotOperationTime() {
        List<OperationCandidates.Token> words=Arrays.asList(
            word("05:10",30,20,70,24),word("24 октября",30,100,220,26),
            word("Покупка",30,210,150,30),word("-10,00 RUB",500,210,260,30),
            word("31 ноября",30,310,220,26),word("Покупка",30,410,150,30),word("-20,00 RUB",500,410,260,30));
        OperationCandidates.Result result=parse(words,OperationCandidates.Mode.HISTORY);
        assertEquals(2,result.proposals.size());assertEquals(LocalDate.of(2026,10,24),result.proposals.get(0).date);
        assertNull(result.proposals.get(1).date);assertFalse(result.proposals.get(1).hasAllFields());
        OperationCandidates.Result relative=parse(Arrays.asList(word("24 октября",30,100,220,26),word("-10 RUB",400,200,250,30),
            word("Вчера",30,300,150,26),word("-20 RUB",400,400,250,30)),OperationCandidates.Mode.HISTORY);
        assertNull(relative.proposals.get(1).date);
        for(String malformed:new String[]{"27.10.20260","27 октября 20260","27 октября 202","270.10.2026","27.100.2026","270 октября 2026"}) {
            OperationCandidates.Result wrongYear=parse(Arrays.asList(word("24 октября",30,100,220,26),word("-10 RUB",400,200,250,30),
                word(malformed,30,300,300,26),word("-20 RUB",400,400,250,30)),OperationCandidates.Mode.HISTORY);
            assertEquals(2,wrongYear.proposals.size());assertNull(wrongYear.proposals.get(1).date);
            assertFalse(wrongYear.proposals.get(1).hasAllFields());
        }
        OperationCandidates.Proposal card=parse(Arrays.asList(word("05:10",30,20,70,24),
            word("27.10.2026 в 19:17",30,100,350,26),word("-10,00 RUB",300,300,280,50)),OperationCandidates.Mode.CARD).proposals.get(0);
        assertEquals("19:17",card.time);assertEquals(LocalDate.of(2026,10,27),card.date);assertFalse(card.defaultYear);
    }
    @Test public void moneyRemainsExactAndOversizedInputIsRejected() {
        OperationCandidates.Proposal maximum=parse(Collections.singletonList(
            word("+92233720368547758,07 RUB",30,300,700,40)),OperationCandidates.Mode.CARD).proposals.get(0);
        assertEquals(Long.valueOf(Long.MAX_VALUE),maximum.minor);
        OperationCandidates.Proposal overflow=parse(Collections.singletonList(
            word("+92233720368547758,08 RUB",30,300,700,40)),OperationCandidates.Mode.CARD).proposals.get(0);
        assertNull(overflow.minor);assertFalse(overflow.hasAllFields());
        OperationCandidates.Token valid=word("-1 RUB",30,300,150,40);
        assertThrows(IllegalArgumentException.class,()->OperationCandidates.analyze(Collections.nCopies(10001,valid),1000,1700,2026,"RUB",OperationCandidates.Mode.HISTORY));
        assertThrows(IllegalArgumentException.class,()->OperationCandidates.analyze(Collections.singletonList(valid),10000,801,2026,"RUB",OperationCandidates.Mode.HISTORY));
    }
    @Test public void findsMainAmountRatherThanSmallerBalanceOnLightCard() throws Exception { card(false); }
    @Test public void findsMainAmountRatherThanSmallerBalanceOnDarkCard() throws Exception { card(true); }
    private void card(boolean dark) throws Exception {
        Bitmap image=Bitmap.createBitmap(900,1000,Bitmap.Config.ARGB_8888);
        Canvas canvas=new Canvas(image);canvas.drawColor(dark?Color.rgb(25,25,25):Color.WHITE);
        Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);paint.setColor(dark?Color.WHITE:Color.BLACK);
        paint.setTypeface(Typeface.create("sans-serif",Typeface.NORMAL));paint.setTextSize(24);canvas.drawText("05:10",40,45,paint);
        paint.setTextSize(40);canvas.drawText("Детали операции",50,125,paint);
        paint.setTextSize(32);canvas.drawText("27 октября 2026 в 19:17",50,200,paint);
        paint.setTextSize(36);canvas.drawText("Учебный магазин",50,290,paint);
        paint.setTextSize(64);canvas.drawText("-573,19 RUB",260,440,paint);
        paint.setTextSize(30);canvas.drawText("Остаток на счете",50,650,paint);canvas.drawText("1 037,17 RUB",50,715,paint);
        try {
            OperationCandidates.Result result=recognize(image,OperationCandidates.Mode.CARD);
            assertEquals(1,result.proposals.size());OperationCandidates.Proposal row=result.proposals.get(0);
            assertEquals(Long.valueOf(57319),row.minor);
            assertTrue(row.sign==-1||row.sign==0);
            assertEquals(LocalDate.of(2026,10,27),row.date);assertEquals("19:17",row.time);
            if(row.sign==0)assertFalse(row.hasAllFields());
            writeMeasurement(dark?"dark-card":"light-card",result);
        } finally { image.recycle(); }
    }
    @Test public void findsSixHistoryRowsWithoutPredefinedAmountCoordinates() throws Exception {
        Bitmap image=Bitmap.createBitmap(1000,1700,Bitmap.Config.ARGB_8888);
        Canvas canvas=new Canvas(image);canvas.drawColor(Color.WHITE);
        Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);paint.setColor(Color.BLACK);paint.setTypeface(Typeface.create("sans-serif",Typeface.NORMAL));
        paint.setTextSize(24);canvas.drawText("05:10",40,45,paint);
        paint.setTextSize(40);canvas.drawText("История операций",40,120,paint);canvas.drawText("24 октября",40,200,paint);
        String[] amounts={"+101,25","-20,50","-3,75","+42,00","-7,10","-9,90"};
        long[] expected={10125,-2050,-375,4200,-710,-990};
        for(int i=0;i<amounts.length;i++){
            int baseline=340+i*205;
            paint.setTextSize(32);canvas.drawText("Учебная покупка",40,baseline,paint);
            paint.setTextSize(40);canvas.drawText(amounts[i]+" RUB",570,baseline,paint);
        }
        paint.setTextSize(28);canvas.drawText("+1 бонус",40,1530,paint);
        try {
            OperationCandidates.Result result=recognize(image,OperationCandidates.Mode.HISTORY);
            assertEquals(6,result.proposals.size());assertEquals(1,result.nonCashRows);
            for(int i=0;i<expected.length;i++){
                OperationCandidates.Proposal row=result.proposals.get(i);
                assertEquals(Long.valueOf(Math.abs(expected[i])),row.minor);
                assertEquals(LocalDate.of(2026,10,24),row.date);assertTrue(row.defaultYear);
                if(row.sign!=0)assertEquals(Long.valueOf(expected[i]),row.signedMinor());else assertFalse(row.hasAllFields());
            }
            writeMeasurement("six-row-history",result);
        } finally { image.recycle(); }
    }
    private OperationCandidates.Result recognize(Bitmap image,OperationCandidates.Mode mode) throws Exception {
        ExecutorService worker=Executors.newSingleThreadExecutor();
        try {
            BoundedOcrJob job=new BoundedOcrJob(OfflineOcrTest.prepareData(),null);
            List<BoundedOcrJob.Word> words=worker.submit(()->job.recognize(image)).get(45,TimeUnit.SECONDS);
            return OperationCandidates.analyze(OperationCandidates.fromWords(words),image.getWidth(),image.getHeight(),2026,"RUB",mode);
        } finally {worker.shutdownNow();assertTrue(worker.awaitTermination(30,TimeUnit.SECONDS));}
    }
    private void writeMeasurement(String name,OperationCandidates.Result result) throws Exception {
        int unknown=0,complete=0,cropped=0;
        for(OperationCandidates.Proposal row:result.proposals){if(row.sign==0)unknown++;if(row.hasAllFields())complete++;if(row.cropped)cropped++;}
        String report="{\"rows\":"+result.proposals.size()+",\"unknown_directions\":"+unknown+",\"complete_fields\":"+complete+
            ",\"cropped\":"+cropped+",\"non_cash_rows\":"+result.nonCashRows+",\"synthetic_only\":true,\"writes_financial_book\":false}";
        Context context=ApplicationProvider.getApplicationContext();
        File root=new File(context.getNoBackupFilesDir(),"synthetic-candidate-proof");assertTrue(root.mkdirs()||root.isDirectory());
        try(java.io.FileOutputStream out=new java.io.FileOutputStream(new File(root,name+".json"))){out.write(report.getBytes(java.nio.charset.StandardCharsets.UTF_8));}
    }
}
