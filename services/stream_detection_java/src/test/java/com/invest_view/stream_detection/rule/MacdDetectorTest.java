package com.invest_view.stream_detection.rule;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
import com.invest_view.stream_detection.indicator.Indicators;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.java.functions.KeySelector;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class MacdDetectorTest {

    @Test
    public void testTwoCloseBullishCrossDoesNotEmitBeforeWarmup() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, List.of(10000, 15000));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
        }
    }

    @Test
    public void testTwoCloseBearishCrossDoesNotEmitBeforeWarmup() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, List.of(10000, 5000));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
        }
    }

    @Test
    public void testBullishCrossEmitsAfterWarmup() throws Exception {
        List<Integer> closes = warmupClosesWithFinalClose(15000);

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.MACD_BULLISH, output.get(0).getValue().getPatternType());
            assertEquals("macd_12_26_9", output.get(0).getValue().getStrategyName());
        }
    }

    @Test
    public void testBearishCrossEmitsAfterWarmup() throws Exception {
        List<Integer> closes = warmupClosesWithFinalClose(5000);

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.MACD_BEARISH, output.get(0).getValue().getPatternType());
            assertEquals("macd_12_26_9", output.get(0).getValue().getStrategyName());
        }
    }

    @Test
    public void testNextStatePreservesEmaFormulaAndCountsClosedBars() {
        MacdDetector.MacdState first = MacdDetector.nextState(null, 10000);
        assertEquals(10000.0, first.fastEma, 0.000001);
        assertEquals(10000.0, first.slowEma, 0.000001);
        assertEquals(0.0, first.macd, 0.000001);
        assertEquals(0.0, first.signal, 0.000001);
        assertEquals(1, first.closedBarCount);

        MacdDetector.MacdState second = MacdDetector.nextState(first, 15000);
        double expectedFastEma = Indicators.ema(10000.0, 15000.0, MacdDetector.FAST_PERIOD);
        double expectedSlowEma = Indicators.ema(10000.0, 15000.0, MacdDetector.SLOW_PERIOD);
        double expectedMacd = expectedFastEma - expectedSlowEma;
        double expectedSignal = Indicators.ema(0.0, expectedMacd, MacdDetector.SIGNAL_PERIOD);
        assertEquals(expectedFastEma, second.fastEma, 0.000001);
        assertEquals(expectedSlowEma, second.slowEma, 0.000001);
        assertEquals(expectedMacd, second.macd, 0.000001);
        assertEquals(expectedSignal, second.signal, 0.000001);
        assertEquals(2, second.closedBarCount);
    }

    private static List<Integer> warmupClosesWithFinalClose(int finalClose) {
        List<Integer> closes = new ArrayList<>(Collections.nCopies(34, 10000));
        closes.add(finalClose);
        return closes;
    }

    private static void feedCloses(
            KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h,
            List<Integer> closes) throws Exception {
        for (int i = 0; i < closes.size(); i++) {
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAtBucket(i, closes.get(i))));
        }
        h.processElement(new StreamRecord<>(RuleTestTicks.tickAtBucket(closes.size(), closes.get(closes.size() - 1))));
    }

    private static KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> harness() throws Exception {
        KeyedProcessOperator<String, StockTick, StockPattern> op = new KeyedProcessOperator<>(new MacdDetector());
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }
}
