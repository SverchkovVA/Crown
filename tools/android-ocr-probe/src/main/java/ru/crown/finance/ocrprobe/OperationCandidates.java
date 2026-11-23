package ru.crown.finance.ocrprobe;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.DateTimeException;
import java.util.*;
import java.util.regex.*;

/** Изолированная проба. Предложения не являются подтверждением и не записывают деньги. */
final class OperationCandidates {
    enum Mode { CARD, HISTORY }
    static final class Token {
        final String text; final int left, top, right, bottom;
        Token(String text, int left, int top, int right, int bottom) {
            this.text=text; this.left=left; this.top=top; this.right=right; this.bottom=bottom;
        }
        int height() { return bottom-top; }
    }
    static final class Proposal {
        final Long minor; final int sign; final String currency; final LocalDate date;
        final String time; final boolean defaultCurrency, defaultYear, ambiguous, cropped;
        final int left, top, right, bottom, textHeight;
        Proposal(Long minor, int sign, String currency, DateInfo date, String time,
                 boolean defaultCurrency, boolean ambiguous, boolean cropped, List<Token> words) {
            this.minor=minor; this.sign=sign; this.currency=currency; this.date=date==null?null:date.date;
            this.defaultYear=date!=null&&date.defaultYear; this.time=time; this.defaultCurrency=defaultCurrency;
            this.ambiguous=ambiguous; this.cropped=cropped;
            left=words.stream().mapToInt(w->w.left).min().orElse(0);
            top=words.stream().mapToInt(w->w.top).min().orElse(0);
            right=words.stream().mapToInt(w->w.right).max().orElse(0);
            bottom=words.stream().mapToInt(w->w.bottom).max().orElse(0);
            textHeight=words.stream().mapToInt(Token::height).max().orElse(0);
        }
        Long signedMinor() { return minor==null||sign==0?null:sign>0?minor:-minor; }
        boolean hasAllFields() { return minor!=null&&sign!=0&&date!=null&&!ambiguous&&!cropped; }
    }
    static final class Result {
        final List<Proposal> proposals; final int nonCashRows;
        Result(List<Proposal> proposals, int nonCashRows) {
            this.proposals=Collections.unmodifiableList(proposals); this.nonCashRows=nonCashRows;
        }
    }
    private static final Pattern CURRENCY=Pattern.compile("(?iu)(?<!\\p{L})(RUB|руб(?:лей|ля|ль)?\\.?|₽|USD|\\$|EUR|€)(?!\\p{L})");
    private static final Pattern BONUS=Pattern.compile("(?iu)\\b(?:бонус\\p{L}*|балл\\p{L}*|points?)\\b");
    private static final Pattern AMOUNT=Pattern.compile("^([+-])?((?:\\d{1,3}(?: \\d{3})+|\\d+)(?:[.,]\\d{1,2})?)$");
    private static final Pattern DATE=Pattern.compile("(?<!\\d)(\\d+)[./](\\d+)[./](\\d+)(?!\\d)");
    private static final Pattern MONTH_DATE=Pattern.compile("(?iu)(?<!\\d)(\\d+)\\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:[,\\s]+(\\d{4}))?(?!\\p{L})");
    private static final Pattern TIME=Pattern.compile("(?<!\\d)([01]?\\d|2[0-3]):([0-5]\\d)(?!\\d)");
    private static final String[] MONTHS={"января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"};
    private static String clean(String text) {
        return text.replace('\u00a0',' ').replace('\u202f',' ').replace('\u2212','-')
            .replace('\u2013','-').replace('\u2014','-').trim();
    }
    private static final class DateInfo {
        final LocalDate date; final boolean defaultYear;
        DateInfo(LocalDate date, boolean defaultYear) { this.date=date; this.defaultYear=defaultYear; }
    }
    private static DateInfo date(String text, int year) {
        Matcher full=DATE.matcher(text);
        int month, day; boolean inferred;
        if(full.find()) {
            if(full.group(1).length()>2||full.group(2).length()>2||full.group(3).length()!=4) return new DateInfo(null,false);
            day=Integer.parseInt(full.group(1));month=Integer.parseInt(full.group(2));year=Integer.parseInt(full.group(3));inferred=false;
        }
        else {
            Matcher named=MONTH_DATE.matcher(text);
            if(!named.find()) return text.trim().matches("(?iu)^(?:сегодня|вчера)\\b.*")?new DateInfo(null,true):null;
            if(named.group(1).length()>2) return new DateInfo(null,false);
            day=Integer.parseInt(named.group(1)); month=Arrays.asList(MONTHS).indexOf(named.group(2).toLowerCase(Locale.ROOT))+1;
            String tail=text.substring(named.end(2)).trim();
            Matcher suppliedYear=Pattern.compile("^[,\\s]*(\\d+)").matcher(tail);
            inferred=!suppliedYear.find()||TIME.matcher(tail).lookingAt();
            if(!inferred) {
                if(suppliedYear.group(1).length()!=4) return new DateInfo(null,false);
                year=Integer.parseInt(suppliedYear.group(1));
            }
        }
        try { return new DateInfo(year>=1900&&year<=9999?LocalDate.of(year,month,day):null,inferred); }
        catch(DateTimeException error) { return new DateInfo(null,inferred); }
    }
    private static String time(String text) {
        Matcher match=TIME.matcher(text);
        return match.find()?String.format(Locale.ROOT,"%02d:%s",Integer.parseInt(match.group(1)),match.group(2)):null;
    }
    private static final class Line {
        final List<Token> words=new ArrayList<>(); int top, bottom;
        Line(Token word) { top=word.top;bottom=word.bottom;words.add(word); }
        boolean accepts(Token word) {
            int overlap=Math.min(bottom,word.bottom)-Math.max(top,word.top);
            return overlap>0&&overlap*2>=Math.min(bottom-top,word.height());
        }
        void add(Token word) { words.add(word);top=Math.min(top,word.top);bottom=Math.max(bottom,word.bottom); }
        String text() { words.sort(Comparator.comparingInt(w->w.left));StringJoiner text=new StringJoiner(" ");for(Token w:words) text.add(w.text);return text.toString(); }
    }
    static List<Token> fromWords(List<BoundedOcrJob.Word> words) {
        List<Token> result=new ArrayList<>();
        for(BoundedOcrJob.Word w:words) result.add(new Token(w.text,w.left,w.top,w.right,w.bottom));
        return result;
    }
    static Result analyze(List<Token> input, int width, int height, int year, String fallbackCurrency, Mode mode) {
        if(width<=0||height<=0||width>10000||height>10000||(long)width*height>8000000||year<1900||year>9999||
            input.size()>10000||!Arrays.asList("RUB","USD","EUR").contains(fallbackCurrency)||mode==null)
            throw new IllegalArgumentException("Неверные границы OCR-пробы");
        long characters=0;
        List<Token> ordered=new ArrayList<>(input);
        for(Token word:ordered) {
            if(word.text==null||word.left<0||word.top<0||word.right<=word.left||word.bottom<=word.top||
                word.right>width||word.bottom>height||(characters+=word.text.length())>1000000)
                throw new IllegalArgumentException("Неверная геометрия или объём слов OCR");
        }
        ordered.sort(Comparator.comparingInt((Token w)->w.top).thenComparingInt(w->w.left));
        List<Line> lines=new ArrayList<>();
        for(Token word:ordered) {
            Line chosen=null;
            for(int i=lines.size()-1;i>=Math.max(0,lines.size()-5);i--) if(lines.get(i).accepts(word)) {chosen=lines.get(i);break;}
            if(chosen==null) lines.add(new Line(word)); else chosen.add(word);
        }
        lines.sort(Comparator.comparingInt(line->line.top));
        DateInfo cardDate=null; String cardTime=null; boolean conflictingDates=false;
        for(Line line:lines) {
            String text=line.text(); DateInfo value=date(text,year);
            if(value!=null&&value.date!=null) {
                if(cardDate!=null&&!cardDate.date.equals(value.date)) conflictingDates=true;
                cardDate=value; String found=time(text);if(found!=null) cardTime=found;
            }
        }
        if(conflictingDates) cardDate=null;
        List<Proposal> candidates=new ArrayList<>(); DateInfo inherited=null; int nonCash=0;
        for(Line line:lines) {
            String text=line.text(); DateInfo inline=date(text,year);
            if(inline!=null) inherited=inline;
            if(BONUS.matcher(text).find()&&!CURRENCY.matcher(text).find()) {nonCash++;continue;}
            String residual=TIME.matcher(DATE.matcher(text).replaceAll("")).replaceAll("");
            residual=MONTH_DATE.matcher(residual).replaceAll("");
            residual=CURRENCY.matcher(residual).replaceAll("");
            residual=residual.replaceAll("(?iu)сегодня|вчера|\\bв\\b","");
            boolean dateHeader=inline!=null&&!Pattern.compile("\\p{L}").matcher(residual).find();
            if(mode==Mode.HISTORY&&dateHeader) continue;
            if(mode==Mode.CARD&&text.toLowerCase(Locale.ROOT).matches(".*(?:остаток|баланс|доступно|лимит).*")) continue;
            List<List<Token>> clusters=new ArrayList<>(); List<Token> cluster=new ArrayList<>();
            for(Token word:line.words) {
                String numeric=clean(CURRENCY.matcher(clean(word.text)).replaceAll(""));
                boolean moneyPart=!TIME.matcher(word.text).find()&&(numeric.isEmpty()||numeric.matches("[\\d\\s.,+\\-]+"));
                int gap=cluster.isEmpty()?0:word.left-cluster.get(cluster.size()-1).right;
                int span=cluster.isEmpty()?word.height():Math.max(word.height(),cluster.stream().mapToInt(Token::height).max().orElse(1));
                if(!moneyPart||(!cluster.isEmpty()&&gap>span*3/2)) { if(!cluster.isEmpty()) {clusters.add(cluster);cluster=new ArrayList<>();} }
                if(moneyPart) cluster.add(word);
            }
            if(!cluster.isEmpty()) clusters.add(cluster);
            boolean unlabeledMultiple=clusters.size()>1&&!Pattern.compile("\\p{L}").matcher(CURRENCY.matcher(text).replaceAll("")).find();
            for(List<Token> part:clusters) {
                StringJoiner join=new StringJoiner(" ");for(Token word:part) join.add(clean(word.text));
                String raw=join.toString(); Matcher currency=CURRENCY.matcher(raw);
                String code=fallbackCurrency; boolean defaultCode=true;
                if(currency.find()) {
                    String marker=currency.group().toUpperCase(Locale.ROOT);
                    code=marker.equals("USD")||marker.equals("$")?"USD":marker.equals("EUR")||marker.equals("€")?"EUR":"RUB";
                    defaultCode=false;
                }
                String amount=clean(CURRENCY.matcher(raw).replaceAll("")).replaceAll("\\s+"," ");
                amount=amount.replaceFirst("^([+-])\\s+","$1").replaceAll("\\s+([.,])\\s*","$1");
                if(amount.isEmpty()||(!amount.matches("^[+-].*")&&defaultCode&&!amount.matches(".*[.,]\\d{1,2}$"))) continue;
                boolean bareRow=!Pattern.compile("\\p{L}").matcher(CURRENCY.matcher(text).replaceAll("")).find();
                if(defaultCode&&amount.matches("(?:[01]?\\d|2[0-3])[.,][0-5]\\d")&&
                    ((mode==Mode.HISTORY&&bareRow)||(mode==Mode.CARD&&part.get(0).top<height/10))) continue;
                Matcher parsed=AMOUNT.matcher(amount); Long minor=null;int sign=0;
                if(amount.length()<=40&&parsed.matches()) {
                    sign="-".equals(parsed.group(1))?-1:"+".equals(parsed.group(1))?1:0;
                    try {long value=new BigDecimal(parsed.group(2).replace(" ","").replace(',','.')).movePointRight(2).longValueExact();if(value>0)minor=value;}
                    catch(ArithmeticException ignored) { /* Слишком большую сумму нужно проверить вручную. */ }
                }
                int left=part.stream().mapToInt(w->w.left).min().orElse(0);
                int right=part.stream().mapToInt(w->w.right).max().orElse(0);
                int top=part.stream().mapToInt(w->w.top).min().orElse(0);
                int bottom=part.stream().mapToInt(w->w.bottom).max().orElse(0);
                int margin=Math.max(2,part.stream().mapToInt(Token::height).max().orElse(1)/5);
                boolean cropped=left<=margin||right>=width-margin||top==0||bottom==height;
                DateInfo useDate=mode==Mode.CARD?cardDate:inline!=null?inline:inherited;
                String useTime=mode==Mode.CARD?cardTime:time(text);
                if(unlabeledMultiple) { minor=null;sign=0; }
                candidates.add(new Proposal(minor,sign,code,useDate,useTime,defaultCode,minor==null||unlabeledMultiple,cropped,part));
            }
        }
        if(mode==Mode.CARD&&candidates.size()>1) {
            candidates.sort(Comparator.comparingInt((Proposal p)->p.textHeight).reversed());
            Proposal first=candidates.get(0),second=candidates.get(1);
            if(first.textHeight*10<second.textHeight*13) {
                List<Token> box=Collections.singletonList(new Token("",first.left,first.top,first.right,first.bottom));
                first=new Proposal(null,0,first.currency,cardDate,cardTime,first.defaultCurrency,true,first.cropped,box);
            }
            candidates=new ArrayList<>(Collections.singletonList(first));
        }
        return new Result(candidates,nonCash);
    }
}
