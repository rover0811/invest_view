package com.invest_view.stream_detection.alert;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.NameBasedGenerator;
import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.Severity;
import com.invest_view.events.StockAlert;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

public final class AlertBuilders {

    private static final UUID NAMESPACE_DNS = UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");
    private static final NameBasedGenerator UUID5_GEN = Generators.nameBasedGenerator(NAMESPACE_DNS);

    private AlertBuilders() {
    }

    public static String makeAlertEventId(String symbol, String alertType, String key) {
        String name = symbol + "|" + alertType + "|" + key;
        return UUID5_GEN.generate(name).toString();
    }

    public static Optional<Market> normalizeMarket(String tickMarket) {
        if ("KRX".equals(tickMarket)) {
            return Optional.of(Market.KRX);
        }
        if ("NXT".equals(tickMarket)) {
            return Optional.of(Market.NXT);
        }
        return Optional.empty();
    }

    public static StockAlert buildPriceAlert(
            String symbol,
            Market market,
            int minPrice,
            int maxPrice,
            long observationStartMs,
            long observationEndMs,
            long triggeredAtMs,
            double threshold) {
        if (minPrice <= 0) {
            throw new IllegalArgumentException("minPrice must be positive");
        }

        double changeRate = ((double) (maxPrice - minPrice)) / (double) minPrice;
        Map<String, String> triggerValues = Map.of(
                "min_price", String.valueOf(minPrice),
                "max_price", String.valueOf(maxPrice),
                "change_rate", round6(changeRate),
                "threshold", round6(threshold));

        return StockAlert.newBuilder()
                .setAlertEventId(makeAlertEventId(symbol, AlertType.PRICE_ALERT.name(), String.valueOf(observationStartMs)))
                .setSymbol(symbol)
                .setMarket(market)
                .setAlertType(AlertType.PRICE_ALERT)
                .setSeverity(Severity.WARNING)
                .setObservationStartAt(Instant.ofEpochMilli(observationStartMs))
                .setObservationEndAt(Instant.ofEpochMilli(observationEndMs))
                .setTriggeredAt(Instant.ofEpochMilli(triggeredAtMs))
                .setTriggerValues(triggerValues)
                .setSourceTickEventId(null)
                .setRuleName("price_alert_5min_3pct")
                .build();
    }

    public static StockAlert buildVIImminent(
            String symbol,
            Market market,
            int price,
            int viTriggerPrice,
            String receivedAt,
            long triggeredAtMs,
            double threshold) {
        if (viTriggerPrice <= 0) {
            throw new IllegalArgumentException("viTriggerPrice must be positive");
        }

        double distanceRatio = Math.abs((double) (price - viTriggerPrice)) / (double) viTriggerPrice;
        Instant triggeredAt = Instant.ofEpochMilli(triggeredAtMs);
        Map<String, String> triggerValues = Map.of(
                "price", String.valueOf(price),
                "vi_trigger_price", String.valueOf(viTriggerPrice),
                "distance_ratio", round6(distanceRatio),
                "threshold", round6(threshold));

        return StockAlert.newBuilder()
                .setAlertEventId(makeAlertEventId(symbol, AlertType.VI_IMMINENT.name(), receivedAt))
                .setSymbol(symbol)
                .setMarket(market)
                .setAlertType(AlertType.VI_IMMINENT)
                .setSeverity(Severity.WARNING)
                .setObservationStartAt(triggeredAt)
                .setObservationEndAt(triggeredAt)
                .setTriggeredAt(triggeredAt)
                .setTriggerValues(triggerValues)
                .setSourceTickEventId(null)
                .setRuleName("vi_imminent_1pct")
                .build();
    }

    public static StockAlert buildTradingHalt(
            String symbol,
            Market market,
            String prevState,
            String newState,
            long transitionTimeMs,
            String tickTradeTime) {
        Instant transitionTime = Instant.ofEpochMilli(transitionTimeMs);
        Map<String, String> triggerValues = Map.of(
                "prev_state", prevState,
                "new_state", newState,
                "transition_time", tickTradeTime);

        return StockAlert.newBuilder()
                .setAlertEventId(makeAlertEventId(symbol, AlertType.TRADING_HALT.name(), String.valueOf(transitionTimeMs)))
                .setSymbol(symbol)
                .setMarket(market)
                .setAlertType(AlertType.TRADING_HALT)
                .setSeverity(Severity.CRITICAL)
                .setObservationStartAt(transitionTime)
                .setObservationEndAt(transitionTime)
                .setTriggeredAt(transitionTime)
                .setTriggerValues(triggerValues)
                .setSourceTickEventId(null)
                .setRuleName("trading_halt_transition")
                .build();
    }

    private static String round6(double value) {
        return BigDecimal.valueOf(value).setScale(6, RoundingMode.HALF_EVEN).toPlainString();
    }
}
