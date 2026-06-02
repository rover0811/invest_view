package com.invest_view.stream_detection.watermark;

import com.investview.ticks.StockTick;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.junit.jupiter.api.Test;

import java.time.format.DateTimeParseException;

import static org.junit.jupiter.api.Assertions.*;

class TickWatermarkStrategyTest {

    @Test
    void testParseIso8601_validZSuffix() {
        long ms = TickWatermarkStrategy.parseIso8601ToMs("2026-05-24T09:00:00.000Z");
        // 2026-05-24T09:00:00Z in epoch ms (verified via Python)
        assertEquals(1779613200000L, ms);
    }

    @Test
    void testParseIso8601_invalid_throwsException() {
        assertThrows(DateTimeParseException.class,
                () -> TickWatermarkStrategy.parseIso8601ToMs("not-a-date"));
    }

    @Test
    void testForTicks_returnsNonNull() {
        WatermarkStrategy<StockTick> strategy = TickWatermarkStrategy.forTicks();
        assertNotNull(strategy);
    }
}
