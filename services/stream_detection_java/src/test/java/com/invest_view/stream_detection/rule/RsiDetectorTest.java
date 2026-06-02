package com.invest_view.stream_detection.rule;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
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

public class RsiDetectorTest {

    @Test
    public void testOverboughtEmitsAboveSeventy() throws Exception {
        List<Integer> closes = new ArrayList<>();
        for (int i = 0; i < 15; i++) {
            closes.add(100 + i);
        }

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.RSI_OVERBOUGHT, output.get(0).getValue().getPatternType());
            assertEquals("100.000000", output.get(0).getValue().getTriggerValues().get("rsi"));
        }
    }

    @Test
    public void testOversoldEmitsBelowThirty() throws Exception {
        List<Integer> closes = new ArrayList<>();
        for (int i = 0; i < 15; i++) {
            closes.add(100 - i);
        }

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.RSI_OVERSOLD, output.get(0).getValue().getPatternType());
            assertEquals("0.000000", output.get(0).getValue().getTriggerValues().get("rsi"));
        }
    }

    @Test
    public void testBoundaryNeutralDoesNotEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, Collections.nCopies(15, 100));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
        }
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
        KeyedProcessOperator<String, StockTick, StockPattern> op = new KeyedProcessOperator<>(new RsiDetector());
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }
}
