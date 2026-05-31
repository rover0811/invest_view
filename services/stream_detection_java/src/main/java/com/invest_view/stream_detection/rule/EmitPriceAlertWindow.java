package com.invest_view.stream_detection.rule;

import com.invest_view.events.StockAlert;
import com.invest_view.stream_detection.alert.AlertBuilders;
import org.apache.flink.streaming.api.functions.windowing.ProcessWindowFunction;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.util.Collector;

import java.util.Optional;

public class EmitPriceAlertWindow
        extends ProcessWindowFunction<PriceAccumulator, StockAlert, String, TimeWindow> {

    private static final long serialVersionUID = 1L;

    private final double threshold;

    public EmitPriceAlertWindow(double threshold) {
        this.threshold = threshold;
    }

    public static Optional<StockAlert> evaluate(
            PriceAccumulator acc,
            long windowStart,
            long windowEnd,
            double threshold) {
        if (acc.getMinPrice() <= 0 || acc.getMaxPrice() <= 0) {
            return Optional.empty();
        }

        double changeRate = ((double) (acc.getMaxPrice() - acc.getMinPrice())) / (double) acc.getMinPrice();
        if (changeRate < threshold) {
            return Optional.empty();
        }

        return Optional.of(AlertBuilders.buildPriceAlert(
                acc.getSymbol(),
                acc.getMarket(),
                acc.getMinPrice(),
                acc.getMaxPrice(),
                windowStart,
                windowEnd,
                windowEnd,
                threshold));
    }

    @Override
    public void process(
            String key,
            Context ctx,
            Iterable<PriceAccumulator> elements,
            Collector<StockAlert> out) {
        PriceAccumulator acc = null;
        for (PriceAccumulator element : elements) {
            acc = element;
            break;
        }
        if (acc == null) {
            return;
        }

        evaluate(acc, ctx.window().getStart(), ctx.window().getEnd(), threshold).ifPresent(out::collect);
    }
}
