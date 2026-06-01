package com.invest_view.stream_detection.rule;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
import com.invest_view.stream_detection.alert.PatternBuilders;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.java.functions.KeySelector;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.api.watermark.Watermark;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.apache.flink.util.Collector;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

public class BarCloseKeyedProcessFunctionTest {

    @Test
    public void testBucketFlooringMatchesKstFiveMinuteRule() {
        long nine00 = BarCloseKeyedProcessFunction.bucketStartMillis("20260601", "090321");
        long nine05 = BarCloseKeyedProcessFunction.bucketStartMillis("20260601", "090700");

        assertEquals(BarCloseKeyedProcessFunction.tradeTimestampMillis("20260601", "090000"), nine00);
        assertEquals(BarCloseKeyedProcessFunction.tradeTimestampMillis("20260601", "090500"), nine05);
    }

    @Test
    public void testTimerClosesBucketWithoutNextTick() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness(new CloseEchoFunction())) {
            StockTick tick = RuleTestTicks.tickAt(0, 30, 100, "KRX");
            h.processElement(new StreamRecord<>(tick));

            long timerMs = BarCloseKeyedProcessFunction.bucketEndMillis(
                    BarCloseKeyedProcessFunction.bucketStartMillis(tick.getBusinessDate(), tick.getTradeTime()))
                    + BarCloseKeyedProcessFunction.DEFAULT_LATE_TOLERANCE_MS;
            h.processWatermark(new Watermark(timerMs));

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals("100", output.get(0).getValue().getTriggerValues().get("close_price"));
        }
    }

    @Test
    public void testOutOfOrderTickKeepsCloseAtMaxTradeTimestamp() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness(new CloseEchoFunction())) {
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAt(0, 270, 100, "KRX")));
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAt(0, 180, 200, "KRX")));
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAtBucket(1, 300)));

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals("100", output.get(0).getValue().getTriggerValues().get("close_price"));
        }
    }

    @Test
    public void testLateTickAfterTimerIsDropped() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h = harness(new CloseEchoFunction())) {
            StockTick tick = RuleTestTicks.tickAt(0, 30, 100, "KRX");
            h.processElement(new StreamRecord<>(tick));
            long timerMs = BarCloseKeyedProcessFunction.bucketEndMillis(
                    BarCloseKeyedProcessFunction.bucketStartMillis(tick.getBusinessDate(), tick.getTradeTime()))
                    + BarCloseKeyedProcessFunction.DEFAULT_LATE_TOLERANCE_MS;
            h.processWatermark(new Watermark(timerMs));
            h.processElement(new StreamRecord<>(RuleTestTicks.tickAt(0, 240, 999, "KRX")));

            List<StreamRecord<? extends StockPattern>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals("100", output.get(0).getValue().getTriggerValues().get("close_price"));
        }
    }

    private static KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> harness(
            BarCloseKeyedProcessFunction function) throws Exception {
        KeyedProcessOperator<String, StockTick, StockPattern> op = new KeyedProcessOperator<>(function);
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockPattern> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }

    private static class CloseEchoFunction extends BarCloseKeyedProcessFunction {

        private static final long serialVersionUID = 1L;

        @Override
        protected void onBarClose(
                String symbol,
                int closePrice,
                long bucketStartMs,
                Context ctx,
                Collector<StockPattern> out) {
            out.collect(PatternBuilders.buildPattern(
                    symbol,
                    closingMarket(),
                    PatternType.RSI_OVERBOUGHT,
                    bucketStartMs,
                    closingBucketEndMs(),
                    Map.of("close_price", String.valueOf(closePrice)),
                    "test_close_echo"));
        }
    }
}
