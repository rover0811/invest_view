package com.invest_view.stream_detection.rule;

import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.Severity;
import com.invest_view.events.StockAlert;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.java.functions.KeySelector;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class TradingHaltDetectorTest {

    private static final String SYMBOL = "005930";
    private static final String RECEIVED_AT = "2026-05-26T03:00:00.123Z";
    private static final String TRADE_TIME = "030000";

    @Test
    public void testColdStart_firstTickN_noEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("N", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testColdStart_firstTickY_noEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "Y", "KRX")));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("Y", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testNtoYTransition_emitsAlert() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));
            h.processElement(new StreamRecord<>(tick(SYMBOL, "Y", "KRX")));

            List<StreamRecord<? extends StockAlert>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            StockAlert alert = output.get(0).getValue();
            assertEquals(SYMBOL, alert.getSymbol());
            assertEquals(Market.KRX, alert.getMarket());
            assertEquals(AlertType.TRADING_HALT, alert.getAlertType());
            assertEquals(Severity.CRITICAL, alert.getSeverity());
            assertEquals("N", alert.getTriggerValues().get("prev_state"));
            assertEquals("Y", alert.getTriggerValues().get("new_state"));
            assertEquals(TRADE_TIME, alert.getTriggerValues().get("transition_time"));
            assertEquals(Instant.parse(RECEIVED_AT), alert.getTriggeredAt());
            assertEquals(Instant.parse(RECEIVED_AT), alert.getObservationStartAt());
            assertEquals(Instant.parse(RECEIVED_AT), alert.getObservationEndAt());
            assertEquals("trading_halt_transition", alert.getRuleName());
            assertEquals("Y", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testYtoNTransition_noEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "Y", "KRX")));
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("N", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testNtoNStable_noEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("N", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testMalformedValue_skippedAndStateUnchanged() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));
            h.processElement(new StreamRecord<>(tick(SYMBOL, "X", "KRX")));
            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("N", lastState(h, SYMBOL));

            h.processElement(new StreamRecord<>(tick(SYMBOL, "Y", "KRX")));

            List<StreamRecord<? extends StockAlert>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals("N", output.get(0).getValue().getTriggerValues().get("prev_state"));
            assertEquals("Y", output.get(0).getValue().getTriggerValues().get("new_state"));
            assertEquals("Y", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testUnknownMarket_noEmit_butStateUpdated() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick(SYMBOL, "N", "KRX")));
            h.processElement(new StreamRecord<>(tick(SYMBOL, "Y", "NYSE")));

            assertTrue(h.extractOutputStreamRecords().isEmpty());
            assertEquals("Y", lastState(h, SYMBOL));
        }
    }

    @Test
    public void testMultipleSymbols_independentState() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h = harness()) {
            h.processElement(new StreamRecord<>(tick("005930", "N", "KRX")));
            h.processElement(new StreamRecord<>(tick("000660", "Y", "KRX")));
            h.processElement(new StreamRecord<>(tick("005930", "Y", "KRX")));

            List<StreamRecord<? extends StockAlert>> output = h.extractOutputStreamRecords();
            assertEquals(1, output.size());
            assertEquals("005930", output.get(0).getValue().getSymbol());
            assertEquals("Y", lastState(h, "005930"));
            assertEquals("Y", lastState(h, "000660"));
        }
    }

    private static KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> harness() throws Exception {
        KeyedProcessOperator<String, StockTick, StockAlert> op =
                new KeyedProcessOperator<>(new TradingHaltDetector());
        KeySelector<StockTick, String> keySelector = StockTick::getSymbol;
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h =
                new KeyedOneInputStreamOperatorTestHarness<>(op, keySelector, TypeInformation.of(String.class));
        h.open();
        return h;
    }

    private static String lastState(
            KeyedOneInputStreamOperatorTestHarness<String, StockTick, StockAlert> h,
            String symbol) throws Exception {
        h.getOperator().setCurrentKey(symbol);
        ValueState<String> state = h.getOperator().getKeyedStateStore().getState(
                new ValueStateDescriptor<>(TradingHaltDetector.STATE_NAME, String.class));
        return state.value();
    }

    private static StockTick tick(String symbol, String halted, String market) {
        int price = 70000;
        return StockTick.newBuilder()
                .setSourceTrId("test")
                .setMarket(market)
                .setReceivedAt(RECEIVED_AT)
                .setSymbol(symbol)
                .setTradeTime(TRADE_TIME)
                .setPrice(price)
                .setChangeSign("2")
                .setChange(0)
                .setChangeRate(BigDecimal.ZERO)
                .setVwap(BigDecimal.ZERO)
                .setOpen(price)
                .setHigh(price)
                .setLow(price)
                .setAskPrice1(price)
                .setBidPrice1(price)
                .setTradeVolume(1)
                .setCumulativeVolume(1)
                .setCumulativeAmount(1)
                .setSellCount(0)
                .setBuyCount(0)
                .setNetBuyCount(0)
                .setTradeStrength(BigDecimal.ZERO)
                .setTotalSellVolume(0)
                .setTotalBuyVolume(0)
                .setTradeType("0")
                .setBuyRatio(BigDecimal.ZERO)
                .setPrevDayVolumeRate(BigDecimal.ZERO)
                .setOpenTime(TRADE_TIME)
                .setOpenVsSign("2")
                .setOpenVsPrice(0)
                .setHighTime(TRADE_TIME)
                .setHighVsSign("2")
                .setHighVsPrice(0)
                .setLowTime(TRADE_TIME)
                .setLowVsSign("5")
                .setLowVsPrice(0)
                .setBusinessDate("20260526")
                .setMarketSessionCode("1")
                .setTradingHalted(halted)
                .setAskRemain1(0)
                .setBidRemain1(0)
                .setTotalAskRemain(0)
                .setTotalBidRemain(0)
                .setVolumeTurnover(BigDecimal.ZERO)
                .setPrevSameHourVolume(0)
                .setPrevSameHourVolumeRate(BigDecimal.ZERO)
                .setHourClassCode("0")
                .setMarketTerminationCode("0")
                .setViTriggerPrice(0)
                .build();
    }
}
