package com.invest_view.stream_detection.rule;

import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.StockAlert;
import com.investview.ticks.StockTick;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class PriceAlertAggregatorTest {

    private static final double THRESHOLD = 0.03;
    private static final long WINDOW_START = 1716530400000L;
    private static final long WINDOW_END = 1716530700000L;

    @Test
    public void testAggregatorAccumulates_3pctSpread_emitsAlert() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator acc = agg.createAccumulator();

        acc = agg.add(tick("005930", 10000, "N", "KRX"), acc);
        acc = agg.add(tick("005930", 10300, "N", "KRX"), acc);

        Optional<StockAlert> alert = EmitPriceAlertWindow.evaluate(agg.getResult(acc), WINDOW_START, WINDOW_END, THRESHOLD);

        assertTrue(alert.isPresent());
        assertEquals(AlertType.PRICE_ALERT, alert.get().getAlertType());
        assertEquals("005930", alert.get().getSymbol());
        assertEquals(Market.KRX, alert.get().getMarket());
        assertEquals("0.030000", alert.get().getTriggerValues().get("change_rate"));
    }

    @Test
    public void testAggregatorAccumulates_2pctSpread_noAlert() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator acc = agg.createAccumulator();

        acc = agg.add(tick("005930", 10000, "N", "KRX"), acc);
        acc = agg.add(tick("005930", 10200, "N", "KRX"), acc);

        Optional<StockAlert> alert = EmitPriceAlertWindow.evaluate(agg.getResult(acc), WINDOW_START, WINDOW_END, THRESHOLD);

        assertTrue(alert.isEmpty());
    }

    @Test
    public void testAggregatorIgnoresHaltedTicks() {
        assertFalse(PriceAlertAggregator.isEligible(tick("005930", 10000, "Y", "KRX")));
        assertTrue(PriceAlertAggregator.isEligible(tick("005930", 10000, "N", "KRX")));
        assertTrue(PriceAlertAggregator.isEligible(tick("005930", 10000, "N", "NXT")));
        assertFalse(PriceAlertAggregator.isEligible(tick("005930", 0, "N", "KRX")));
        assertFalse(PriceAlertAggregator.isEligible(tick("005930", 10000, "N", "NYSE")));
    }

    @Test
    public void testAggregatorColdStart_singleTick_noAlert() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator acc = agg.add(tick("005930", 10000, "N", "KRX"), agg.createAccumulator());

        Optional<StockAlert> alert = EmitPriceAlertWindow.evaluate(agg.getResult(acc), WINDOW_START, WINDOW_END, THRESHOLD);

        assertTrue(alert.isEmpty());
    }

    @Test
    public void testAccumulatorAdd_initializes() {
        PriceAlertAggregator agg = new PriceAlertAggregator();

        PriceAccumulator acc = agg.add(tick("005930", 70000, "N", "NXT"), agg.createAccumulator());

        assertEquals(70000, acc.getMinPrice());
        assertEquals(70000, acc.getMaxPrice());
        assertEquals("005930", acc.getSymbol());
        assertEquals(Market.NXT, acc.getMarket());
    }

    @Test
    public void testAccumulatorAdd_updatesMinMax() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator acc = agg.createAccumulator();

        acc = agg.add(tick("005930", 70000, "N", "KRX"), acc);
        acc = agg.add(tick("005930", 69000, "N", "KRX"), acc);
        acc = agg.add(tick("005930", 72500, "N", "KRX"), acc);

        assertEquals(69000, acc.getMinPrice());
        assertEquals(72500, acc.getMaxPrice());
    }

    @Test
    public void testAccumulatorMerge_handlesEmpty() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator empty = agg.createAccumulator();
        PriceAccumulator initialized = agg.add(tick("005930", 70000, "N", "KRX"), agg.createAccumulator());

        assertSame(initialized, agg.merge(empty, initialized));
        assertSame(initialized, agg.merge(initialized, empty));
    }

    @Test
    public void testEmitWindow_setsTriggeredAtToWindowEnd() {
        PriceAlertAggregator agg = new PriceAlertAggregator();
        PriceAccumulator acc = agg.createAccumulator();

        acc = agg.add(tick("005930", 10000, "N", "KRX"), acc);
        acc = agg.add(tick("005930", 10300, "N", "KRX"), acc);

        StockAlert alert = EmitPriceAlertWindow.evaluate(acc, WINDOW_START, WINDOW_END, THRESHOLD).orElseThrow();

        assertEquals(Instant.ofEpochMilli(WINDOW_END), alert.getTriggeredAt());
        assertEquals(Instant.ofEpochMilli(WINDOW_START), alert.getObservationStartAt());
        assertEquals(Instant.ofEpochMilli(WINDOW_END), alert.getObservationEndAt());
    }

    private static StockTick tick(String symbol, int price, String halted, String market) {
        return StockTick.newBuilder()
                .setSourceTrId("test")
                .setMarket(market)
                .setReceivedAt("2026-05-26T03:00:00.000Z")
                .setSymbol(symbol)
                .setTradeTime("030000")
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
                .setOpenTime("030000")
                .setOpenVsSign("2")
                .setOpenVsPrice(0)
                .setHighTime("030000")
                .setHighVsSign("2")
                .setHighVsPrice(0)
                .setLowTime("030000")
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
