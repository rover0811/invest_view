package com.invest_view.stream_detection.indicator;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

public class IndicatorsTest {

    @Test
    public void testSma_usesLastPeriodCloses() {
        assertEquals(4.0, Indicators.sma(List.of(1, 2, 3, 4, 5), 3), 0.000001);
    }

    @Test
    public void testRsi_knownTextbookFourteenPeriodExample() {
        List<Integer> closesInCents = List.of(
                4434, 4409, 4415, 4361, 4433,
                4483, 4510, 4542, 4584, 4608,
                4589, 4603, 4561, 4628, 4628);

        assertEquals(70.46, Indicators.rsi(closesInCents, 14), 0.5);
    }

    @Test
    public void testRsi_boundaries() {
        assertEquals(100.0, Indicators.rsi(List.of(1, 2, 3, 4, 5, 6), 5), 0.000001);
        assertEquals(0.0, Indicators.rsi(List.of(6, 5, 4, 3, 2, 1), 5), 0.000001);
        assertEquals(50.0, Indicators.rsi(List.of(5, 5, 5, 5, 5, 5), 5), 0.000001);
    }

    @Test
    public void testRsi_requiresPeriodPlusOneCloses() {
        assertThrows(IllegalArgumentException.class, () -> Indicators.rsi(List.of(1, 2, 3), 3));
    }

    @Test
    public void testEma_usesStandardMultiplier() {
        assertEquals(11.0, Indicators.ema(10.0, 13.0, 5), 0.000001);
    }
}
