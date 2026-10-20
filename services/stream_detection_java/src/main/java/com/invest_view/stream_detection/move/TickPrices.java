package com.invest_view.stream_detection.move;

import com.investview.ticks.StockTick;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Optional;

/**
 * Derives the previous KRX close, the daily return and the trade event time
 * from a {@link StockTick}.
 *
 * <p>KIS {@code change} is the difference against the previous KRX close. Its
 * sign is taken from {@code change_sign} (1, 2 up; 3 flat; 4, 5 down) and the
 * magnitude from {@code |change|}, so both signed and unsigned producers work.
 */
public final class TickPrices {

    public static final ZoneId KST = ZoneId.of("Asia/Seoul");

    private static final DateTimeFormatter BUSINESS_DATE = DateTimeFormatter.BASIC_ISO_DATE;
    private static final DateTimeFormatter TRADE_TIME = DateTimeFormatter.ofPattern("HHmmss");

    private TickPrices() {
    }

    /** Signed change against the previous KRX close, or empty for an unknown sign code. */
    public static Optional<Integer> signedChange(StockTick tick) {
        int magnitude = Math.abs(tick.getChange());
        switch (tick.getChangeSign()) {
            case "1":
            case "2":
                return Optional.of(magnitude);
            case "3":
                return Optional.of(0);
            case "4":
            case "5":
                return Optional.of(-magnitude);
            default:
                return Optional.empty();
        }
    }

    /** Previous KRX close ({@code price - signedChange}); empty when not derivable or not positive. */
    public static Optional<Integer> prevClose(StockTick tick) {
        if (tick.getPrice() <= 0) {
            return Optional.empty();
        }
        return signedChange(tick)
                .map(change -> tick.getPrice() - change)
                .filter(prev -> prev > 0);
    }

    /** Return against the previous close in percent units (1.0 means +1%). */
    public static double returnPct(int price, int prevClose) {
        return ((double) price / (double) prevClose - 1.0) * 100.0;
    }

    public static LocalDate businessDate(StockTick tick) {
        return LocalDate.parse(tick.getBusinessDate(), BUSINESS_DATE);
    }

    /** Trade event time: {@code business_date + trade_time} interpreted in KST. */
    public static long tradeEventTimeMs(StockTick tick) {
        LocalTime time = LocalTime.parse(tick.getTradeTime(), TRADE_TIME);
        return businessDate(tick).atTime(time).atZone(KST).toInstant().toEpochMilli();
    }
}
