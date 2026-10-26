package com.invest_view.stream_detection.move;

import com.invest_view.events.PriceState;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PriceStateMachineTest {

    private final PriceStateMachine machine = PriceStateMachine.defaults();

    @ParameterizedTest
    @CsvSource({
            "NEUTRAL, 0.0, NEUTRAL",
            "NEUTRAL, 0.99, NEUTRAL",
            "NEUTRAL, 1.0, POSITIVE",
            "NEUTRAL, -0.99, NEUTRAL",
            "NEUTRAL, -1.0, NEGATIVE",
            "POSITIVE, 0.5, POSITIVE",
            "POSITIVE, 0.49, NEUTRAL",
            "POSITIVE, -1.0, NEGATIVE",
            "NEGATIVE, -0.5, NEGATIVE",
            "NEGATIVE, -0.49, NEUTRAL",
            "NEGATIVE, 1.0, POSITIVE",
    })
    void transitions(PriceState from, double returnPct, PriceState expected) {
        assertEquals(expected, machine.next(from, returnPct));
    }

    @Test
    void hysteresisKeepsStateBetweenExitAndEnter() {
        PriceState state = machine.next(PriceState.NEUTRAL, 1.2);
        state = machine.next(state, 0.8);
        state = machine.next(state, 0.6);
        assertEquals(PriceState.POSITIVE, state);
        assertEquals(PriceState.NEUTRAL, machine.next(state, 0.3));
    }

    @Test
    void rejectsExitAboveEnter() {
        assertThrows(IllegalArgumentException.class, () -> new PriceStateMachine(1.0, 1.5));
    }
}
