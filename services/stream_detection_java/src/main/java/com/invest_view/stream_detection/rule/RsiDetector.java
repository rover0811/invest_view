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

public class RsiDetector extends BarCloseKeyedProcessFunction {

    private static final long serialVersionUID = 1L;
    public static final String CLOSES_STATE = "rsi_recent_bar_closes";
    public static final int PERIOD = 14;
    public static final double OVERSOLD = 30.0;
    public static final double OVERBOUGHT = 70.0;

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
        List<Integer> closes = closes();
        closes.add(closePrice);

        if (closes.size() >= PERIOD + 1) {
            double rsi = Indicators.rsi(closes, PERIOD);
            long windowEndMs = closingBucketEndMs();
            long windowStartMs = windowEndMs - (PERIOD * FIVE_MINUTES_MS);
            if (rsi < OVERSOLD) {
                out.collect(PatternBuilders.buildRsi(
                        symbol, closingMarket(), PatternType.RSI_OVERSOLD,
                        windowStartMs, windowEndMs, closePrice, rsi));
            } else if (rsi > OVERBOUGHT) {
                out.collect(PatternBuilders.buildRsi(
                        symbol, closingMarket(), PatternType.RSI_OVERBOUGHT,
                        windowStartMs, windowEndMs, closePrice, rsi));
            }
        }

        trimAndStore(closes, PERIOD + 1);
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
