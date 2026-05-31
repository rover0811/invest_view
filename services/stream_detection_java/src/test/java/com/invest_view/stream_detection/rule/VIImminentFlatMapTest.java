package com.invest_view.stream_detection.rule;

import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.Severity;
import com.invest_view.events.StockAlert;
import com.investview.ticks.StockTick;
import org.apache.flink.util.Collector;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class VIImminentFlatMapTest {

    private static final double THRESHOLD = 0.01;

    @Test
    public void testProximityWithinThreshold_emitsAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "N", "KRX", 70700), THRESHOLD);

        assertTrue(alert.isPresent());
        StockAlert a = alert.get();
        assertEquals(AlertType.VI_IMMINENT, a.getAlertType());
        assertEquals(Severity.WARNING, a.getSeverity());
        assertEquals("005930", a.getSymbol());
        assertEquals(Market.KRX, a.getMarket());
        assertEquals("70000", a.getTriggerValues().get("price"));
        assertEquals("70700", a.getTriggerValues().get("vi_trigger_price"));
        assertEquals("0.009901", a.getTriggerValues().get("distance_ratio"));
        assertEquals("0.010000", a.getTriggerValues().get("threshold"));
        assertEquals("vi_imminent_1pct", a.getRuleName());
    }

    @Test
    public void testProximityBeyondThreshold_noAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "N", "KRX", 72000), THRESHOLD);

        assertTrue(alert.isEmpty());
    }

    @Test
    public void testZeroViTriggerPrice_noAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "N", "KRX", 0), THRESHOLD);

        assertTrue(alert.isEmpty());
        assertFalse(VIImminentFlatMap.isEligible(tick("005930", 70000, "N", "KRX", 0)));
    }

    @Test
    public void testTradingHalted_noAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "Y", "KRX", 70700), THRESHOLD);

        assertTrue(alert.isEmpty());
        assertFalse(VIImminentFlatMap.isEligible(tick("005930", 70000, "Y", "KRX", 70700)));
    }

    @Test
    public void testExactlyAtThreshold_emitsAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 99000, "N", "KRX", 100000), THRESHOLD);

        assertTrue(alert.isPresent(), "distance_ratio == threshold should be INCLUSIVE");
        assertEquals("0.010000", alert.get().getTriggerValues().get("distance_ratio"));
    }

    @Test
    public void testUnknownMarket_noAlert() {
        Optional<StockAlert> alert = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "N", "NYSE", 70700), THRESHOLD);

        assertTrue(alert.isEmpty());
        assertFalse(VIImminentFlatMap.isEligible(tick("005930", 70000, "N", "NYSE", 70700)));
    }

    @Test
    public void testFlatMapDelegatesToEvaluate() throws Exception {
        VIImminentFlatMap fn = new VIImminentFlatMap(THRESHOLD);
        CollectingCollector<StockAlert> out = new CollectingCollector<>();

        fn.flatMap(tick("005930", 70000, "N", "KRX", 70700), out);
        fn.flatMap(tick("005930", 70000, "N", "KRX", 72000), out);
        fn.flatMap(tick("005930", 70000, "Y", "KRX", 70700), out);

        assertEquals(1, out.collected.size(), "only the within-threshold non-halted tick should emit");
        StockAlert emitted = out.collected.get(0);
        assertNotNull(emitted);
        assertEquals(AlertType.VI_IMMINENT, emitted.getAlertType());
        assertEquals("005930", emitted.getSymbol());

        Optional<StockAlert> direct = VIImminentFlatMap.evaluate(
                tick("005930", 70000, "N", "KRX", 70700), THRESHOLD);
        assertTrue(direct.isPresent());
        assertEquals(direct.get().getAlertEventId(), emitted.getAlertEventId(),
                "flatMap must produce identical alert to static evaluate");
    }

    @Test
    public void testEligibility_NXTMarketAccepted() {
        assertTrue(VIImminentFlatMap.isEligible(tick("005930", 70000, "N", "NXT", 70700)));
        assertTrue(VIImminentFlatMap.isEligible(tick("005930", 70000, "N", "KRX", 70700)));
        assertFalse(VIImminentFlatMap.isEligible(tick("005930", 0, "N", "KRX", 70700)));
    }

    private static StockTick tick(String symbol, int price, String halted, String market, int viTriggerPrice) {
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
                .setViTriggerPrice(viTriggerPrice)
                .build();
    }

    private static final class CollectingCollector<T> implements Collector<T> {
        private final List<T> collected = new ArrayList<>();

        @Override
        public void collect(T record) {
            collected.add(record);
        }

        @Override
        public void close() {
        }
    }
}
