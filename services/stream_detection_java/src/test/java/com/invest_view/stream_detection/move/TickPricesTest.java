package com.invest_view.stream_detection.move;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;

class TickPricesTest {

    @Test
    void prevCloseFromUnsignedChangeAndSignCode() {
        assertEquals(Optional.of(10_000), TickPrices.prevClose(MoveTestTicks.tick("A", "20260601", "090000", 10_150, "2", 150)));
        assertEquals(Optional.of(10_000), TickPrices.prevClose(MoveTestTicks.tick("A", "20260601", "090000", 9_850, "5", 150)));
    }

    @Test
    void prevCloseFromSignedNegativeChange() {
        assertEquals(Optional.of(10_000), TickPrices.prevClose(MoveTestTicks.tick("A", "20260601", "090000", 9_850, "5", -150)));
    }

    @Test
    void prevCloseEmptyForUnknownSignOrZeroPrice() {
        assertEquals(Optional.empty(), TickPrices.prevClose(MoveTestTicks.tick("A", "20260601", "090000", 9_850, "9", 150)));
        assertEquals(Optional.empty(), TickPrices.prevClose(MoveTestTicks.tick("A", "20260601", "090000", 0, "3", 0)));
    }

    @Test
    void returnPctInPercentUnits() {
        assertEquals(1.5, TickPrices.returnPct(10_150, 10_000), 1e-9);
        assertEquals(-2.0, TickPrices.returnPct(9_800, 10_000), 1e-9);
    }

    @Test
    void tradeEventTimeIsKst() {
        long ms = TickPrices.tradeEventTimeMs(MoveTestTicks.tick("A", "20260601", "091500", 10_000));
        assertEquals(Instant.parse("2026-06-01T00:15:00Z").toEpochMilli(), ms);
    }
}
