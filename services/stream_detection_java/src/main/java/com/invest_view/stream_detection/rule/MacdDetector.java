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

    private final int fastPeriod;
    private final int slowPeriod;
    private final int signalPeriod;
    private final int warmupClosedBars;

    private transient ValueState<MacdState> macdState;

    public MacdDetector() {
        this(FAST_PERIOD, SLOW_PERIOD, SIGNAL_PERIOD);
    }

    public MacdDetector(int fastPeriod, int slowPeriod, int signalPeriod) {
        if (fastPeriod <= 0 || slowPeriod <= 0 || signalPeriod <= 0) {
            throw new IllegalArgumentException("MACD periods must be positive");
        }
        if (fastPeriod >= slowPeriod) {
            throw new IllegalArgumentException("MACD fast period must be less than slow period");
        }
        this.fastPeriod = fastPeriod;
        this.slowPeriod = slowPeriod;
        this.signalPeriod = signalPeriod;
        this.warmupClosedBars = slowPeriod + signalPeriod;
    }

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
        MacdState current = nextState(previous, closePrice, fastPeriod, slowPeriod, signalPeriod);

        if (previous != null && current.closedBarCount >= warmupClosedBars) {
            long windowEndMs = closingBucketEndMs();
            long windowStartMs = windowEndMs - (slowPeriod * FIVE_MINUTES_MS);
            if (previous.macd <= previous.signal && current.macd > current.signal) {
                out.collect(PatternBuilders.buildMacd(
                        symbol, closingMarket(), PatternType.MACD_BULLISH,
                        windowStartMs, windowEndMs, closePrice,
                        current.macd, current.signal, current.fastEma, current.slowEma,
                        fastPeriod, slowPeriod, signalPeriod));
            } else if (previous.macd >= previous.signal && current.macd < current.signal) {
                out.collect(PatternBuilders.buildMacd(
                        symbol, closingMarket(), PatternType.MACD_BEARISH,
                        windowStartMs, windowEndMs, closePrice,
                        current.macd, current.signal, current.fastEma, current.slowEma,
                        fastPeriod, slowPeriod, signalPeriod));
            }
        }

        macdState.update(current);
    }

    static MacdState nextState(MacdState previous, int closePrice) {
        return nextState(previous, closePrice, FAST_PERIOD, SLOW_PERIOD, SIGNAL_PERIOD);
    }

    static MacdState nextState(MacdState previous, int closePrice, int fastPeriod, int slowPeriod, int signalPeriod) {
        if (previous == null) {
            return new MacdState(closePrice, closePrice, 0.0, 0.0, 1);
        }
        double fastEma = Indicators.ema(previous.fastEma, closePrice, fastPeriod);
        double slowEma = Indicators.ema(previous.slowEma, closePrice, slowPeriod);
        double macd = fastEma - slowEma;
        double signal = Indicators.ema(previous.signal, macd, signalPeriod);
        return new MacdState(fastEma, slowEma, macd, signal, previous.closedBarCount + 1);
    }

    public static class MacdState implements Serializable {

        private static final long serialVersionUID = 1L;

        public double fastEma;
        public double slowEma;
        public double macd;
        public double signal;
        public int closedBarCount;

        public MacdState() {
        }

        public MacdState(double fastEma, double slowEma, double macd, double signal) {
            this(fastEma, slowEma, macd, signal, 0);
        }

        public MacdState(double fastEma, double slowEma, double macd, double signal, int closedBarCount) {
            this.fastEma = fastEma;
            this.slowEma = slowEma;
            this.macd = macd;
            this.signal = signal;
            this.closedBarCount = closedBarCount;
        }
    }
}
