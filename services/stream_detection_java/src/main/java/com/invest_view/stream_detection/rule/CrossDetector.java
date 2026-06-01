package com.invest_view.stream_detection.rule;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
import com.invest_view.stream_detection.alert.PatternBuilders;
import com.invest_view.stream_detection.indicator.Indicators;
import org.apache.flink.api.common.state.ListState;
import org.apache.flink.api.common.state.ListStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.util.Collector;

import java.util.ArrayList;
import java.util.List;

public class CrossDetector extends BarCloseKeyedProcessFunction {

    private static final long serialVersionUID = 1L;
    public static final String CLOSES_STATE = "cross_recent_bar_closes";
    public static final int SHORT_PERIOD = 5;
    public static final int LONG_PERIOD = 20;

    private transient ListState<Integer> closesState;

    @Override
    public void open(Configuration parameters) throws Exception {
        super.open(parameters);
        this.closesState = getRuntimeContext().getListState(
                new ListStateDescriptor<>(CLOSES_STATE, Integer.class));
    }

    @Override
    protected void onBarClose(String symbol, int closePrice, long bucketStartMs, Context ctx, Collector<StockPattern> out)
            throws Exception {
        List<Integer> before = closes();
        List<Integer> after = new ArrayList<>(before);
        after.add(closePrice);

        if (before.size() >= LONG_PERIOD) {
            double previousShort = Indicators.sma(before, SHORT_PERIOD);
            double previousLong = Indicators.sma(before, LONG_PERIOD);
            double currentShort = Indicators.sma(after, SHORT_PERIOD);
            double currentLong = Indicators.sma(after, LONG_PERIOD);
            long windowEndMs = closingBucketEndMs();
            long windowStartMs = windowEndMs - (LONG_PERIOD * FIVE_MINUTES_MS);

            if (previousShort <= previousLong && currentShort > currentLong) {
                out.collect(PatternBuilders.buildMovingAverageCross(
                        symbol, closingMarket(), PatternType.GOLDEN_CROSS,
                        windowStartMs, windowEndMs, closePrice, currentShort, currentLong));
            } else if (previousShort >= previousLong && currentShort < currentLong) {
                out.collect(PatternBuilders.buildMovingAverageCross(
                        symbol, closingMarket(), PatternType.DEAD_CROSS,
                        windowStartMs, windowEndMs, closePrice, currentShort, currentLong));
            }
        }

        trimAndStore(after, LONG_PERIOD + 1);
    }

    private List<Integer> closes() throws Exception {
        List<Integer> closes = new ArrayList<>();
        for (Integer close : closesState.get()) {
            closes.add(close);
        }
        return closes;
    }

    private void trimAndStore(List<Integer> closes, int maxSize) throws Exception {
        int fromIndex = Math.max(0, closes.size() - maxSize);
        closesState.update(closes.subList(fromIndex, closes.size()));
    }
}
