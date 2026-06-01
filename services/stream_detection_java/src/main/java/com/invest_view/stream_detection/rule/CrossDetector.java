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

    private final int shortPeriod;
    private final int longPeriod;

    private transient ListState<Integer> closesState;

    public CrossDetector() {
        this(SHORT_PERIOD, LONG_PERIOD);
    }

    public CrossDetector(int shortPeriod, int longPeriod) {
        if (shortPeriod <= 0 || longPeriod <= 0) {
            throw new IllegalArgumentException("MA periods must be positive");
        }
        if (shortPeriod >= longPeriod) {
            throw new IllegalArgumentException("short MA period must be less than long MA period");
        }
        this.shortPeriod = shortPeriod;
        this.longPeriod = longPeriod;
    }

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

        if (before.size() >= longPeriod) {
            double previousShort = Indicators.sma(before, shortPeriod);
            double previousLong = Indicators.sma(before, longPeriod);
            double currentShort = Indicators.sma(after, shortPeriod);
            double currentLong = Indicators.sma(after, longPeriod);
            long windowEndMs = closingBucketEndMs();
            long windowStartMs = windowEndMs - (longPeriod * FIVE_MINUTES_MS);

            if (previousShort <= previousLong && currentShort > currentLong) {
                out.collect(PatternBuilders.buildMovingAverageCross(
                        symbol, closingMarket(), PatternType.GOLDEN_CROSS,
                        windowStartMs, windowEndMs, closePrice, currentShort, currentLong,
                        shortPeriod, longPeriod));
            } else if (previousShort >= previousLong && currentShort < currentLong) {
                out.collect(PatternBuilders.buildMovingAverageCross(
                        symbol, closingMarket(), PatternType.DEAD_CROSS,
                        windowStartMs, windowEndMs, closePrice, currentShort, currentLong,
                        shortPeriod, longPeriod));
            }
        }

        trimAndStore(after, longPeriod + 1);
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
