package com.invest_view.stream_detection.alert;

import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.Severity;
import com.invest_view.events.StockAlert;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class AlertBuildersTest {

    @Test
    public void testUuid5_NamespaceDnsBytes() {
        UUID namespaceDns = UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");

        assertEquals("6ba7b810-9dad-11d1-80b4-00c04fd430c8", namespaceDns.toString());
        assertEquals(0x6ba7b8109dad11d1L, namespaceDns.getMostSignificantBits());
        assertEquals(0x80b400c04fd430c8L, namespaceDns.getLeastSignificantBits());
    }

    @Test
    public void testUuid5_CrossLanguageFixture_PRICE_ALERT() {
        assertEquals(
                "53df0202-0963-5214-be81-4e12dc202aa2",
                AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1716530400000"));
    }

    @Test
    public void testUuid5_CrossLanguageFixture_PRICE_ALERT_alt() {
        assertEquals(
                "d5b96658-b229-5832-af37-b8a407cb5376",
                AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1700000000000"));
    }

    @Test
    public void testUuid5_CrossLanguageFixture_VI_IMMINENT() {
        assertEquals(
                "5c860760-6f6e-5d8c-abef-acbba7107624",
                AlertBuilders.makeAlertEventId("005930", "VI_IMMINENT", "2026-05-24T09:30:00+00:00"));
    }

    @Test
    public void testUuid5_CrossLanguageFixture_TRADING_HALT() {
        assertEquals(
                "aab1ff0c-378d-55aa-88e5-bf2a54043068",
                AlertBuilders.makeAlertEventId("005930", "TRADING_HALT", "1716530400000"));
    }

    @Test
    public void testUuid5_Deterministic() {
        String first = AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1716530400000");
        String second = AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1716530400000");

        assertEquals(first, second);
    }

    @Test
    public void testUuid5_DifferentInputsDifferentUuids() {
        String first = AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1716530400000");
        String second = AlertBuilders.makeAlertEventId("000660", "PRICE_ALERT", "1716530400000");
        String third = AlertBuilders.makeAlertEventId("005930", "VI_IMMINENT", "1716530400000");
        String fourth = AlertBuilders.makeAlertEventId("005930", "PRICE_ALERT", "1700000000000");

        assertNotEquals(first, second);
        assertNotEquals(first, third);
        assertNotEquals(first, fourth);
    }

    @Test
    public void testNormalizeMarket_KRX() {
        assertEquals(Optional.of(Market.KRX), AlertBuilders.normalizeMarket("KRX"));
    }

    @Test
    public void testNormalizeMarket_NXT() {
        assertEquals(Optional.of(Market.NXT), AlertBuilders.normalizeMarket("NXT"));
    }

    @Test
    public void testNormalizeMarket_lowercase_isEmpty() {
        assertTrue(AlertBuilders.normalizeMarket("krx").isEmpty());
    }

    @Test
    public void testNormalizeMarket_unknown_isEmpty() {
        assertTrue(AlertBuilders.normalizeMarket("NYSE").isEmpty());
    }

    @Test
    public void testNormalizeMarket_null_isEmpty() {
        assertTrue(AlertBuilders.normalizeMarket(null).isEmpty());
    }

    @Test
    public void testBuildPriceAlert_allFieldsPopulated() {
        String symbol = "005930";
        Market market = Market.KRX;
        int minPrice = 70000;
        int maxPrice = 72100;
        long observationStartMs = 1716530400000L;
        long observationEndMs = 1716530700000L;
        long triggeredAtMs = 1716530712345L;
        double threshold = 0.03;

        StockAlert alert = AlertBuilders.buildPriceAlert(
                symbol, market, minPrice, maxPrice,
                observationStartMs, observationEndMs, triggeredAtMs, threshold);

        assertEquals(AlertBuilders.makeAlertEventId(symbol, "PRICE_ALERT", String.valueOf(observationStartMs)), alert.getAlertEventId());
        assertEquals(symbol, alert.getSymbol());
        assertEquals(market, alert.getMarket());
        assertEquals(AlertType.PRICE_ALERT, alert.getAlertType());
        assertEquals(Severity.WARNING, alert.getSeverity());
        assertEquals(Instant.ofEpochMilli(observationStartMs), alert.getObservationStartAt());
        assertEquals(Instant.ofEpochMilli(observationEndMs), alert.getObservationEndAt());
        assertEquals(Instant.ofEpochMilli(triggeredAtMs), alert.getTriggeredAt());
        assertEquals(Set.of("min_price", "max_price", "change_rate", "threshold"), alert.getTriggerValues().keySet());
        assertEquals("70000", alert.getTriggerValues().get("min_price"));
        assertEquals("72100", alert.getTriggerValues().get("max_price"));
        assertEquals("0.030000", alert.getTriggerValues().get("change_rate"));
        assertEquals("0.030000", alert.getTriggerValues().get("threshold"));
        assertNull(alert.getSourceTickEventId());
        assertEquals("price_alert_5min_3pct", alert.getRuleName());
    }

    @Test
    public void testBuildPriceAlert_changeRateRoundedTo6Decimals() {
        StockAlert alert = AlertBuilders.buildPriceAlert(
                "005930", Market.KRX, 10000, 10350,
                1716530400000L, 1716530700000L, 1716530712345L, 0.03);

        assertEquals("0.035000", alert.getTriggerValues().get("change_rate"));
    }

    @Test
    public void testBuildPriceAlert_throwsIfMinPriceZero() {
        assertThrows(IllegalArgumentException.class, () -> AlertBuilders.buildPriceAlert(
                "005930", Market.KRX, 0, 10350,
                1716530400000L, 1716530700000L, 1716530712345L, 0.03));
    }

    @Test
    public void testBuildPriceAlert_throwsIfMinPriceNegative() {
        assertThrows(IllegalArgumentException.class, () -> AlertBuilders.buildPriceAlert(
                "005930", Market.KRX, -1, 10350,
                1716530400000L, 1716530700000L, 1716530712345L, 0.03));
    }

    @Test
    public void testBuildVIImminent_allFieldsPopulated() {
        String symbol = "005930";
        Market market = Market.NXT;
        int price = 70000;
        int viTriggerPrice = 70700;
        String receivedAt = "2026-05-24T09:30:00+00:00";
        long triggeredAtMs = 1716530400000L;
        double threshold = 0.01;

        StockAlert alert = AlertBuilders.buildVIImminent(
                symbol, market, price, viTriggerPrice, receivedAt, triggeredAtMs, threshold);

        assertEquals(AlertBuilders.makeAlertEventId(symbol, "VI_IMMINENT", receivedAt), alert.getAlertEventId());
        assertEquals(symbol, alert.getSymbol());
        assertEquals(market, alert.getMarket());
        assertEquals(AlertType.VI_IMMINENT, alert.getAlertType());
        assertEquals(Severity.WARNING, alert.getSeverity());
        assertEquals(Instant.ofEpochMilli(triggeredAtMs), alert.getObservationStartAt());
        assertEquals(Instant.ofEpochMilli(triggeredAtMs), alert.getObservationEndAt());
        assertEquals(Instant.ofEpochMilli(triggeredAtMs), alert.getTriggeredAt());
        assertEquals(Set.of("price", "vi_trigger_price", "distance_ratio", "threshold"), alert.getTriggerValues().keySet());
        assertEquals("70000", alert.getTriggerValues().get("price"));
        assertEquals("70700", alert.getTriggerValues().get("vi_trigger_price"));
        assertEquals("0.009901", alert.getTriggerValues().get("distance_ratio"));
        assertEquals("0.010000", alert.getTriggerValues().get("threshold"));
        assertNull(alert.getSourceTickEventId());
        assertEquals("vi_imminent_1pct", alert.getRuleName());
    }

    @Test
    public void testBuildVIImminent_distanceRatioRoundedTo6Decimals() {
        StockAlert alert = AlertBuilders.buildVIImminent(
                "005930", Market.KRX, 70000, 70700,
                "2026-05-24T09:30:00+00:00", 1716530400000L, 0.01);

        assertEquals("0.009901", alert.getTriggerValues().get("distance_ratio"));
    }

    @Test
    public void testBuildVIImminent_throwsIfViTriggerZero() {
        assertThrows(IllegalArgumentException.class, () -> AlertBuilders.buildVIImminent(
                "005930", Market.KRX, 70000, 0,
                "2026-05-24T09:30:00+00:00", 1716530400000L, 0.01));
    }

    @Test
    public void testBuildTradingHalt_allFieldsPopulated() {
        String symbol = "005930";
        Market market = Market.KRX;
        String prevState = "N";
        String newState = "Y";
        long transitionTimeMs = 1716530400000L;
        String tickTradeTime = "093000";

        StockAlert alert = AlertBuilders.buildTradingHalt(
                symbol, market, prevState, newState, transitionTimeMs, tickTradeTime);

        assertEquals(AlertBuilders.makeAlertEventId(symbol, "TRADING_HALT", String.valueOf(transitionTimeMs)), alert.getAlertEventId());
        assertEquals(symbol, alert.getSymbol());
        assertEquals(market, alert.getMarket());
        assertEquals(AlertType.TRADING_HALT, alert.getAlertType());
        assertEquals(Severity.CRITICAL, alert.getSeverity());
        assertEquals(Instant.ofEpochMilli(transitionTimeMs), alert.getObservationStartAt());
        assertEquals(Instant.ofEpochMilli(transitionTimeMs), alert.getObservationEndAt());
        assertEquals(Instant.ofEpochMilli(transitionTimeMs), alert.getTriggeredAt());
        assertEquals(Set.of("prev_state", "new_state", "transition_time"), alert.getTriggerValues().keySet());
        assertEquals("N", alert.getTriggerValues().get("prev_state"));
        assertEquals("Y", alert.getTriggerValues().get("new_state"));
        assertEquals(tickTradeTime, alert.getTriggerValues().get("transition_time"));
        assertNull(alert.getSourceTickEventId());
        assertEquals("trading_halt_transition", alert.getRuleName());
    }
}
