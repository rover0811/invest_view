package com.invest_view.stream_detection.move;

import com.invest_view.events.MoveType;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class CandidateRangesTest {

    @Test
    void dailyRangeIsPrevKrxCloseToLastSessionEnd() {
        CandidateRanges.Range range = CandidateRanges.of(
                MoveType.DAILY, LocalDate.of(2026, 6, 1), LocalDate.of(2026, 5, 29));
        assertEquals(Instant.parse("2026-05-29T06:30:00Z").toEpochMilli(), range.startMs());
        assertEquals(Instant.parse("2026-06-01T11:00:00Z").toEpochMilli(), range.endMs());
    }

    @Test
    void intradaySpikeIsPhaseTwo() {
        assertThrows(UnsupportedOperationException.class, () -> CandidateRanges.of(
                MoveType.INTRADAY_SPIKE, LocalDate.of(2026, 6, 1), LocalDate.of(2026, 5, 29)));
    }

    @Test
    void previousWeekdaySkipsWeekend() {
        assertEquals(LocalDate.of(2026, 5, 29), CandidateRanges.previousWeekday(LocalDate.of(2026, 6, 1)));
        assertEquals(LocalDate.of(2026, 6, 1), CandidateRanges.previousWeekday(LocalDate.of(2026, 6, 2)));
    }
}
