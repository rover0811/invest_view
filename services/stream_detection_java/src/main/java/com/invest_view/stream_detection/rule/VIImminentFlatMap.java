package com.invest_view.stream_detection.rule;

import com.invest_view.events.Market;
import com.invest_view.events.StockAlert;
import com.invest_view.stream_detection.alert.AlertBuilders;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.util.Collector;

import java.time.Instant;
import java.util.Optional;

public class VIImminentFlatMap implements FlatMapFunction<StockTick, StockAlert> {

    private static final long serialVersionUID = 1L;
    private final double threshold;

    public VIImminentFlatMap(double threshold) {
        this.threshold = threshold;
    }

    public static boolean isEligible(StockTick tick) {
        if (!"N".equals(tick.getTradingHalted())) return false;
        if (tick.getPrice() <= 0) return false;
        if (tick.getViTriggerPrice() <= 0) return false;
        return AlertBuilders.normalizeMarket(tick.getMarket()).isPresent();
    }

    public static Optional<StockAlert> evaluate(StockTick tick, double threshold) {
        if (!isEligible(tick)) return Optional.empty();

        int price = tick.getPrice();
        int vi = tick.getViTriggerPrice();
        double distanceRatio = Math.abs((double) (price - vi)) / (double) vi;
        if (distanceRatio > threshold) return Optional.empty();

        Market market = AlertBuilders.normalizeMarket(tick.getMarket())
                .orElseThrow(() -> new IllegalStateException("market should be present after eligibility"));
        long triggeredAtMs = Instant.parse(tick.getReceivedAt()).toEpochMilli();

        return Optional.of(AlertBuilders.buildVIImminent(
                tick.getSymbol(),
                market,
                price,
                vi,
                tick.getReceivedAt(),
                triggeredAtMs,
                threshold));
    }

    @Override
    public void flatMap(StockTick tick, Collector<StockAlert> out) {
        evaluate(tick, threshold).ifPresent(out::collect);
    }
}
