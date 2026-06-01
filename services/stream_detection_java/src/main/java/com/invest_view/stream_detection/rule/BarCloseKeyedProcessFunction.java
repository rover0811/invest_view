package com.invest_view.stream_detection.rule;

import com.invest_view.events.StockPattern;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;

public abstract class BarCloseKeyedProcessFunction
        extends KeyedProcessFunction<String, StockTick, StockPattern> {

    private static final Logger LOG = LoggerFactory.getLogger(BarCloseKeyedProcessFunction.class);

    private static final long serialVersionUID = 1L;

    public static final long FIVE_MINUTES_MS = 5 * 60 * 1000L;
    public static final long DEFAULT_LATE_TOLERANCE_MS = 10_000L;
    public static final ZoneId KST = ZoneId.of("Asia/Seoul");

    public static final String CURRENT_BUCKET_ID_STATE = "bar_current_bucket_id";
    public static final String CURRENT_BUCKET_LAST_PRICE_STATE = "bar_current_bucket_last_price";
    public static final String CURRENT_BUCKET_MAX_TRADE_TS_STATE = "bar_current_bucket_max_trade_ts";
    public static final String CURRENT_BUCKET_MARKET_STATE = "bar_current_bucket_market";
    public static final String CURRENT_BUCKET_TIMER_STATE = "bar_current_bucket_timer";

    private static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.BASIC_ISO_DATE;
    private static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ofPattern("HHmmss");

    private final long lateToleranceMs;

    private transient ValueState<Long> currentBucketId;
    private transient ValueState<Integer> currentBucketLastPrice;
    private transient ValueState<Long> currentBucketMaxTradeTs;
    private transient ValueState<String> currentBucketMarket;
    private transient ValueState<Long> currentBucketTimer;
    private transient String closingMarket;
    private transient long closingBucketEndMs;

    protected BarCloseKeyedProcessFunction() {
        this(DEFAULT_LATE_TOLERANCE_MS);
    }

    protected BarCloseKeyedProcessFunction(long lateToleranceMs) {
        this.lateToleranceMs = lateToleranceMs;
    }

    @Override
    public void open(Configuration parameters) throws Exception {
        this.currentBucketId = getRuntimeContext().getState(
                new ValueStateDescriptor<>(CURRENT_BUCKET_ID_STATE, Long.class));
        this.currentBucketLastPrice = getRuntimeContext().getState(
                new ValueStateDescriptor<>(CURRENT_BUCKET_LAST_PRICE_STATE, Integer.class));
        this.currentBucketMaxTradeTs = getRuntimeContext().getState(
                new ValueStateDescriptor<>(CURRENT_BUCKET_MAX_TRADE_TS_STATE, Long.class));
        this.currentBucketMarket = getRuntimeContext().getState(
                new ValueStateDescriptor<>(CURRENT_BUCKET_MARKET_STATE, String.class));
        this.currentBucketTimer = getRuntimeContext().getState(
                new ValueStateDescriptor<>(CURRENT_BUCKET_TIMER_STATE, Long.class));
    }

    @Override
    public void processElement(StockTick tick, Context ctx, Collector<StockPattern> out) throws Exception {
        long tradeTimestampMs = tradeTimestampMillis(tick.getBusinessDate(), tick.getTradeTime());
        long bucketStartMs = bucketStartMillis(tick.getBusinessDate(), tick.getTradeTime());
        long bucketEndMs = bucketEndMillis(bucketStartMs);
        String symbol = tick.getSymbol();

        if (isPastAllowedLateness(bucketEndMs, ctx.timerService().currentWatermark())) {
            LOG.warn("Dropping late tick for symbol={} bucketStart={} tradeTime={} watermark={}",
                    symbol, bucketStartMs, tick.getTradeTime(), ctx.timerService().currentWatermark());
            return;
        }

        Long activeBucketStart = currentBucketId.value();
        if (activeBucketStart == null) {
            startBucket(bucketStartMs, tick, tradeTimestampMs, ctx);
            return;
        }

        if (bucketStartMs == activeBucketStart) {
            updateActiveBucket(tick, tradeTimestampMs);
            return;
        }

        if (bucketStartMs > activeBucketStart) {
            Long timer = currentBucketTimer.value();
            if (timer != null) {
                ctx.timerService().deleteEventTimeTimer(timer);
            }
            finalizeActiveBucket(symbol, ctx, out);
            startBucket(bucketStartMs, tick, tradeTimestampMs, ctx);
            return;
        }

        LOG.warn("Dropping out-of-order tick for already closed bucket symbol={} tickBucketStart={} activeBucketStart={} tradeTime={}",
                symbol, bucketStartMs, activeBucketStart, tick.getTradeTime());
    }

    @Override
    public void onTimer(long timestamp, OnTimerContext ctx, Collector<StockPattern> out) throws Exception {
        Long timer = currentBucketTimer.value();
        if (timer != null && timer == timestamp) {
            finalizeActiveBucket(ctx.getCurrentKey(), ctx, out);
            clearActiveBucket();
        }
    }

    protected abstract void onBarClose(
            String symbol,
            int closePrice,
            long bucketStartMs,
            Context ctx,
            Collector<StockPattern> out) throws Exception;

    protected String closingMarket() {
        return closingMarket;
    }

    protected long closingBucketEndMs() {
        return closingBucketEndMs;
    }

    protected long lateToleranceMs() {
        return lateToleranceMs;
    }

    public static long tradeTimestampMillis(String businessDate, String tradeTime) {
        String normalizedTime = leftPadTime(tradeTime);
        LocalDate date = LocalDate.parse(businessDate, DATE_FORMATTER);
        LocalTime time = LocalTime.parse(normalizedTime, TIME_FORMATTER);
        return ZonedDateTime.of(date, time, KST).toInstant().toEpochMilli();
    }

    public static long bucketStartMillis(String businessDate, String tradeTime) {
        String normalizedTime = leftPadTime(tradeTime);
        LocalDate date = LocalDate.parse(businessDate, DATE_FORMATTER);
        LocalTime time = LocalTime.parse(normalizedTime, TIME_FORMATTER);
        int flooredMinute = (time.getMinute() / 5) * 5;
        LocalTime flooredTime = LocalTime.of(time.getHour(), flooredMinute);
        return ZonedDateTime.of(date, flooredTime, KST).toInstant().toEpochMilli();
    }

    public static long bucketEndMillis(long bucketStartMs) {
        return bucketStartMs + FIVE_MINUTES_MS;
    }

    private static String leftPadTime(String tradeTime) {
        if (tradeTime == null) {
            throw new IllegalArgumentException("tradeTime must not be null");
        }
        if (tradeTime.length() >= 6) {
            return tradeTime;
        }
        return "0".repeat(6 - tradeTime.length()) + tradeTime;
    }

    private boolean isPastAllowedLateness(long bucketEndMs, long watermark) {
        return watermark != Long.MIN_VALUE && watermark >= bucketEndMs + lateToleranceMs;
    }

    private void startBucket(long bucketStartMs, StockTick tick, long tradeTimestampMs, Context ctx) throws Exception {
        long timerMs = bucketEndMillis(bucketStartMs) + lateToleranceMs;
        currentBucketId.update(bucketStartMs);
        currentBucketLastPrice.update(tick.getPrice());
        currentBucketMaxTradeTs.update(tradeTimestampMs);
        currentBucketMarket.update(tick.getMarket());
        currentBucketTimer.update(timerMs);
        ctx.timerService().registerEventTimeTimer(timerMs);
    }

    private void updateActiveBucket(StockTick tick, long tradeTimestampMs) throws Exception {
        Long maxTradeTs = currentBucketMaxTradeTs.value();
        if (maxTradeTs == null || tradeTimestampMs >= maxTradeTs) {
            currentBucketLastPrice.update(tick.getPrice());
            currentBucketMaxTradeTs.update(tradeTimestampMs);
            currentBucketMarket.update(tick.getMarket());
        }
    }

    private void finalizeActiveBucket(String symbol, Context ctx, Collector<StockPattern> out) throws Exception {
        Long bucketStartMs = currentBucketId.value();
        Integer closePrice = currentBucketLastPrice.value();
        if (bucketStartMs == null || closePrice == null) {
            return;
        }

        closingMarket = currentBucketMarket.value();
        closingBucketEndMs = bucketEndMillis(bucketStartMs);
        try {
            onBarClose(symbol, closePrice, bucketStartMs, ctx, out);
        } finally {
            closingMarket = null;
            closingBucketEndMs = 0L;
        }
    }

    private void clearActiveBucket() throws Exception {
        currentBucketId.clear();
        currentBucketLastPrice.clear();
        currentBucketMaxTradeTs.clear();
        currentBucketMarket.clear();
        currentBucketTimer.clear();
    }
}
