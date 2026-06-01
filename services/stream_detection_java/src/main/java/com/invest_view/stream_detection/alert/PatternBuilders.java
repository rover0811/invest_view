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
                        "short_period", "5",
                        "long_period", "20"),
                CROSS_STRATEGY);
    }

    public static StockPattern buildRsi(
            String symbol,
            String market,
            PatternType patternType,
            long windowStartMs,
            long windowEndMs,
            int closePrice,
            double rsi) {
        return buildPattern(
                symbol,
                market,
                patternType,
                windowStartMs,
                windowEndMs,
                Map.of(
                        "close_price", String.valueOf(closePrice),
                        "rsi", round6(rsi),
                        "period", "14"),
                RSI_STRATEGY);
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
                        "ema12", round6(ema12),
                        "ema26", round6(ema26)),
                MACD_STRATEGY);
    }

    public static String round6(double value) {
        return BigDecimal.valueOf(value).setScale(6, RoundingMode.HALF_EVEN).toPlainString();
    }
}
