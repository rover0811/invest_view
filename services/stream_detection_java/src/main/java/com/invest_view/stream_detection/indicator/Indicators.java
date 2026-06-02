package com.invest_view.stream_detection.indicator;

import java.util.List;

public final class Indicators {

    private Indicators() {
    }

    public static double sma(List<Integer> closes, int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("period must be positive");
        }
        if (closes.size() < period) {
            throw new IllegalArgumentException("not enough closes for SMA period");
        }

        long sum = 0;
        for (int i = closes.size() - period; i < closes.size(); i++) {
            sum += closes.get(i);
        }
        return (double) sum / (double) period;
    }

    public static double rsi(List<Integer> closes, int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("period must be positive");
        }
        if (closes.size() < period + 1) {
            throw new IllegalArgumentException("RSI requires period + 1 closes");
        }

        double gains = 0.0;
        double losses = 0.0;
        int start = closes.size() - period;
        for (int i = start; i < closes.size(); i++) {
            int delta = closes.get(i) - closes.get(i - 1);
            if (delta > 0) {
                gains += delta;
            } else {
                losses += -delta;
            }
        }

        double averageGain = gains / period;
        double averageLoss = losses / period;
        if (averageGain == 0.0 && averageLoss == 0.0) {
            return 50.0;
        }
        if (averageLoss == 0.0) {
            return 100.0;
        }
        if (averageGain == 0.0) {
            return 0.0;
        }

        double relativeStrength = averageGain / averageLoss;
        return 100.0 - (100.0 / (1.0 + relativeStrength));
    }

    public static double ema(double previousEma, double value, int period) {
        if (period <= 0) {
            throw new IllegalArgumentException("period must be positive");
        }
        double multiplier = 2.0 / (period + 1.0);
        return (value * multiplier) + (previousEma * (1.0 - multiplier));
    }
}
