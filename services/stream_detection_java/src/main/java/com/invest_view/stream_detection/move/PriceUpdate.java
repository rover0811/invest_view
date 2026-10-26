package com.invest_view.stream_detection.move;

import com.invest_view.events.PriceState;

import java.time.LocalDate;

/**
 * One tick after the price state machine has run. Input to every {@link MoveRule}.
 */
public record PriceUpdate(
        String symbol,
        LocalDate businessDate,
        LocalDate prevBusinessDate,
        long eventTimeMs,
        int price,
        int prevClose,
        double returnPct,
        PriceState previousState,
        PriceState currentState) {

    public boolean isTransition() {
        return previousState != currentState;
    }
}
