package com.invest_view.stream_detection.alert;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.NameBasedGenerator;
import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public final class PatternBuilders {

    private static final UUID NAMESPACE_DNS = UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");
    private static final NameBasedGenerator UUID5_GEN = Generators.nameBasedGenerator(NAMESPACE_DNS);

    public static final String CROSS_STRATEGY = "ma5_ma20_cross";
    public static final String RSI_STRATEGY = "rsi_14";
    public static final String MACD_STRATEGY = "macd_12_26_9";

    private PatternBuilders() {
    }

    public static String makePatternEventId(String symbol, String patternType, String windowKey) {
        String name = symbol + "|" + patternType + "|" + windowKey;
        return UUID5_GEN.generate(name).toString();
    }

    public static StockPattern buildPattern(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            Map<String, String> triggerValues,
            String strategyName) {
        String windowKey = windowStartMs + "|" + windowEndMs;
        return StockPattern.newBuilder()
                .setPatternEventId(makePatternEventId(symbol, patternType.name(), windowKey))
                .setSymbol(symbol)
                .setMarket(market)
                .setPatternType(patternType)
                .setWindowStart(Instant.ofEpochMilli(windowStartMs))
                .setWindowEnd(Instant.ofEpochMilli(windowEndMs))
                .setTriggeredAt(Instant.ofEpochMilli(windowEndMs))
                .setTriggerValues(triggerValues)
                .setStrategyName(strategyName)
                .setSourceTickEventId(null)
                .build();
    }

    public static StockPattern buildMovingAverageCross(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double shortMa,
            double longMa) {
        return buildMovingAverageCross(
                symbol, market, patternType, windowStartMs, windowEndMs,
                closePrice, shortMa, longMa, 5, 20);
    }

    public static StockPattern buildMovingAverageCross(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double shortMa,
            double longMa,
            int shortPeriod,
            int longPeriod) {
        return buildPattern(
                symbol,
                market,
                patternType,
                windowStartMs,
                windowEndMs,
                Map.of(
                        "close_price", String.valueOf(closePrice),
                        "ma_short", round6(shortMa),
                        "ma_long", round6(longMa),
                        "short_period", String.valueOf(shortPeriod),
                        "long_period", String.valueOf(longPeriod)),
                "ma" + shortPeriod + "_ma" + longPeriod + "_cross");
    }

    public static StockPattern buildRsi(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double rsi) {
        return buildRsi(symbol, market, patternType, windowStartMs, windowEndMs, closePrice, rsi, 14);
    }

    public static StockPattern buildRsi(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double rsi,
            int period) {
        return buildPattern(
                symbol,
                market,
                patternType,
                windowStartMs,
                windowEndMs,
                Map.of(
                        "close_price", String.valueOf(closePrice),
                        "rsi", round6(rsi),
                        "period", String.valueOf(period)),
                "rsi_" + period);
    }

    public static StockPattern buildMacd(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double macd,
            double signal,
            double ema12,
            double ema26) {
        return buildMacd(
                symbol, market, patternType, windowStartMs, windowEndMs,
                closePrice, macd, signal, ema12, ema26, 12, 26, 9);
    }

    public static StockPattern buildMacd(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double macd,
            double signal,
            double fastEma,
            double slowEma,
            int fastPeriod,
            int slowPeriod,
            int signalPeriod) {
        return buildPattern(
                symbol,
                market,
                patternType,
                windowStartMs,
                windowEndMs,
                Map.of(
                        "close_price", String.valueOf(closePrice),
                        "macd", round6(macd),
                        "signal", round6(signal),
                        "ema12", round6(fastEma),
                        "ema26", round6(slowEma),
                        "ema_fast", round6(fastEma),
                        "ema_slow", round6(slowEma),
                        "fast_period", String.valueOf(fastPeriod),
                        "slow_period", String.valueOf(slowPeriod),
                        "signal_period", String.valueOf(signalPeriod)),
                "macd_" + fastPeriod + "_" + slowPeriod + "_" + signalPeriod);
    }

    public static String round6(double value) {
        return BigDecimal.valueOf(value).setScale(6, RoundingMode.HALF_EVEN).toPlainString();
    }
}
