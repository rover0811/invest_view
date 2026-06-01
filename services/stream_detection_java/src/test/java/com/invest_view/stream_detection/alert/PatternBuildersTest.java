package com.invest_view.stream_detection.alert;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

public class PatternBuildersTest {

    @Test
    public void testUuid5_deterministicAndInputSensitive() {
        String first = PatternBuilders.makePatternEventId("005930", "GOLDEN_CROSS", "1|2");
        String second = PatternBuilders.makePatternEventId("005930", "GOLDEN_CROSS", "1|2");
        String otherType = PatternBuilders.makePatternEventId("005930", "DEAD_CROSS", "1|2");
        String otherWindow = PatternBuilders.makePatternEventId("005930", "GOLDEN_CROSS", "2|3");

        assertEquals(first, second);
        assertNotEquals(first, otherType);
        assertNotEquals(first, otherWindow);
    }

    @Test
    public void testBuildPattern_setsAllFields() {
        StockPattern pattern = PatternBuilders.buildPattern(
                "005930",
                "KRX",
                PatternType.RSI_OVERBOUGHT,
                1716530400000L,
                1716530700000L,
                Map.of("rsi", "72.000000"),
                PatternBuilders.RSI_STRATEGY);

        assertEquals("005930", pattern.getSymbol());
        assertEquals("KRX", pattern.getMarket());
        assertEquals(PatternType.RSI_OVERBOUGHT, pattern.getPatternType());
        assertEquals(Instant.ofEpochMilli(1716530400000L), pattern.getWindowStart());
        assertEquals(Instant.ofEpochMilli(1716530700000L), pattern.getWindowEnd());
        assertEquals(Instant.ofEpochMilli(1716530700000L), pattern.getTriggeredAt());
        assertEquals("72.000000", pattern.getTriggerValues().get("rsi"));
        assertEquals(PatternBuilders.RSI_STRATEGY, pattern.getStrategyName());
        assertNull(pattern.getSourceTickEventId());
    }

    @Test
    public void testBuildCrossTriggerValues() {
        StockPattern pattern = PatternBuilders.buildMovingAverageCross(
                "005930", "KRX", PatternType.GOLDEN_CROSS,
                1000L, 2000L, 70500, 101.2345678, 99.0);

        assertEquals(Set.of("close_price", "ma_short", "ma_long", "short_period", "long_period"),
                pattern.getTriggerValues().keySet());
        assertEquals("101.234568", pattern.getTriggerValues().get("ma_short"));
        assertEquals("99.000000", pattern.getTriggerValues().get("ma_long"));
        assertEquals(PatternBuilders.CROSS_STRATEGY, pattern.getStrategyName());
    }
}
