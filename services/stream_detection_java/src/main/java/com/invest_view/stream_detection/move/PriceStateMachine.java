package com.invest_view.stream_detection.move;

import com.invest_view.events.PriceState;

import java.io.Serializable;

/**
 * Hysteresis price state machine (doc 22, section 10.1).
 *
 * <p>Return percentages are in percent units (1.0 means +1%). Entering
 * POSITIVE/NEGATIVE needs {@code |return| >= enterPct}; leaving needs the
 * return to fall back inside {@code exitPct}. A single tick can jump directly
 * between POSITIVE and NEGATIVE when it crosses the opposite enter threshold.
 */
public final class PriceStateMachine implements Serializable {

    private static final long serialVersionUID = 1L;

    public static final double DEFAULT_ENTER_PCT = 1.0;
    public static final double DEFAULT_EXIT_PCT = 0.5;

    private final double enterPct;
    private final double exitPct;

    public PriceStateMachine(double enterPct, double exitPct) {
        if (!(enterPct > 0) || !(exitPct >= 0) || exitPct > enterPct) {
            throw new IllegalArgumentException(
                    "require 0 <= exitPct <= enterPct and enterPct > 0, got enter="
                            + enterPct + " exit=" + exitPct);
        }
        this.enterPct = enterPct;
        this.exitPct = exitPct;
    }

    public static PriceStateMachine defaults() {
        return new PriceStateMachine(DEFAULT_ENTER_PCT, DEFAULT_EXIT_PCT);
    }

    public PriceState next(PriceState current, double returnPct) {
        if (returnPct >= enterPct) {
            return PriceState.POSITIVE;
        }
        if (returnPct <= -enterPct) {
            return PriceState.NEGATIVE;
        }
        switch (current) {
            case POSITIVE:
                return returnPct < exitPct ? PriceState.NEUTRAL : PriceState.POSITIVE;
            case NEGATIVE:
                return returnPct > -exitPct ? PriceState.NEUTRAL : PriceState.NEGATIVE;
            default:
                return PriceState.NEUTRAL;
        }
    }

    public double enterPct() {
        return enterPct;
    }

    public double exitPct() {
        return exitPct;
    }
}
