package com.invest_view.stream_detection.rule;

import com.invest_view.events.PatternType;
import com.invest_view.events.StockPattern;
import com.invest_view.stream_detection.alert.PatternBuilders;
import com.invest_view.stream_detection.indicator.Indicators;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.util.Collector;

import java.io.Serializable;

public class MacdDetector extends BarCloseKeyedProcessFunction {

    private static final long serialVersionUID = 1L;
    public static final String MACD_STATE = "macd_ema_state";
    public static final int FAST_PERIOD = 12;
    public static final int SLOW_PERIOD = 26;
    public static final int SIGNAL_PERIOD = 9;

    private transient ValueState<MacdState> macdState;

    @Override
    public void open(Configuration parameters) throws Exception {
        super.open(parameters);
        this.macdState = getRuntimeContext().getState(
                new ValueStateDescriptor<>(MACD_STATE, MacdState.class));
    }

    @Override
    protected void onBarClose(String symbol, int closePrice, long bucketStartMs, Context ctx, Collector<StockPattern> out)
            throws Exception {
        MacdState previous = macdState.value();
        MacdState current = nextState(previous, closePrice);

        if (previous != null) {
            long windowEndMs = closingBucketEndMs();
            long windowStartMs = windowEndMs - (SLOW_PERIOD * FIVE_MINUTES_MS);
            if (previous.macd <= previous.signal && current.macd > current.signal) {
                out.collect(PatternBuilders.buildMacd(
                        symbol, closingMarket(), PatternType.MACD_BULLISH,
                        windowStartMs, windowEndMs, closePrice,
                        current.macd, current.signal, current.ema12, current.ema26));
            } else if (previous.macd >= previous.signal && current.macd < current.signal) {
                out.collect(PatternBuilders.buildMacd(
                        symbol, closingMarket(), PatternType.MACD_BEARISH,
                        windowStartMs, windowEndMs, closePrice,
                        current.macd, current.signal, current.ema12, current.ema26));
            }
        }

        macdState.update(current);
    }

    static MacdState nextState(MacdState previous, int closePrice) {
        if (previous == null) {
            return new MacdState(closePrice, closePrice, 0.0, 0.0);
        }
        double ema12 = Indicators.ema(previous.ema12, closePrice, FAST_PERIOD);
        double ema26 = Indicators.ema(previous.ema26, closePrice, SLOW_PERIOD);
        double macd = ema12 - ema26;
        double signal = Indicators.ema(previous.signal, macd, SIGNAL_PERIOD);
        return new MacdState(ema12, ema26, macd, signal);
    }

    public static class MacdState implements Serializable {

        private static final long serialVersionUID = 1L;

        public double ema12;
        public double ema26;
        public double macd;
        public double signal;

        public MacdState() {
        }

        public MacdState(double ema12, double ema26, double macd, double signal) {
            this.ema12 = ema12;
            this.ema26 = ema26;
            this.macd = macd;
            this.signal = signal;
        }
    }
}
