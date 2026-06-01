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

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

public class MacdDetectorTest {

    @Test
    public void testBullishCross() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, List.of(10000, 15000));

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.MACD_BULLISH, output.get(0).getValue().getPatternType());
            assertEquals("macd_12_26_9", output.get(0).getValue().getStrategyName());
        }
    }

    @Test
    public void testBearishCross() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness()) {
            feedCloses(h, List.of(10000, 5000));

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals(PatternType.MACD_BEARISH, output.get(0).getValue().getPatternType());
            assertEquals("macd_12_26_9", output.get(0).getValue().getStrategyName());
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
        KeyedProcessOperator<String, StockTick, StockPattern> op = new KeyedProcessOperator<>(new MacdDetector());
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }
}
