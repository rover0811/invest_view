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

public class CrossDetectorTest {

    @Test
    public void testGoldenCross() throws Exception {
        List<Integer> closes = new ArrayList<>();
        closes.addAll(Collections.nCopies(15, 100));
        closes.addAll(Collections.nCopies(5, 90));
        closes.add(150);

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            StockPattern pattern = output.get(0).getValue();
            assertEquals(PatternType.GOLDEN_CROSS, pattern.getPatternType());
            assertEquals("102.000000", pattern.getTriggerValues().get("ma_short"));
            assertEquals("100.000000", pattern.getTriggerValues().get("ma_long"));
        }
    }

    @Test
    public void testDeadCross() throws Exception {
        List<Integer> closes = new ArrayList<>();
        closes.addAll(Collections.nCopies(15, 100));
        closes.addAll(Collections.nCopies(5, 110));
        closes.add(50);

        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, closes);

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            StockPattern pattern = output.get(0).getValue();
            assertEquals(PatternType.DEAD_CROSS, pattern.getPatternType());
            assertEquals("98.000000", pattern.getTriggerValues().get("ma_short"));
            assertEquals("100.000000", pattern.getTriggerValues().get("ma_long"));
        }
    }

    @Test
    public void testNoCross() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, Collections.nCopies(21, 100));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
        }
    }

    @Test
    public void testInsufficientBars() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, Collections.nCopies(20, 100));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
        }
    }

    @Test
    public void testMultipleTicksInSameBucketProduceOneClosedBar() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAt(0, 0, 100, "KRX")));
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAt(0, 60, 500, "KRX")));
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAtBucket(1, 100)));

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
        KeyedProcessOperator<String, StockTick, StockPattern> op = new KeyedProcessOperator<>(new CrossDetector());
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }
}
