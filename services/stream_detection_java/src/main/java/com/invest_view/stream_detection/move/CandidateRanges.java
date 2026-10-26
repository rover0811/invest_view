package com.invest_view.stream_detection.move;

import com.invest_view.events.MoveType;

import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.LocalTime;

/**
 * Candidate range per move type (doc 22, section 12.1). Phase 2 adds the
 * INTRADAY_SPIKE branch (spike window end plus 60 minutes).
 */
public final class CandidateRanges {

    public static final LocalTime KRX_CLOSE = LocalTime.of(15, 30);
    public static final LocalTime LAST_SESSION_END = LocalTime.of(20, 0);

    public record Range(long startMs, long endMs) {
    }

    private CandidateRanges() {
    }

    public static Range of(MoveType moveType, LocalDate businessDate, LocalDate prevBusinessDate) {
        switch (moveType) {
            case DAILY:
                return new Range(
                        prevBusinessDate.atTime(KRX_CLOSE).atZone(TickPrices.KST).toInstant().toEpochMilli(),
                        businessDate.atTime(LAST_SESSION_END).atZone(TickPrices.KST).toInstant().toEpochMilli());
            default:
                throw new UnsupportedOperationException("candidate range not defined for " + moveType);
        }
    }

    /** Fallback when no earlier business date was observed: previous weekday (no holiday calendar). */
    public static LocalDate previousWeekday(LocalDate date) {
        LocalDate prev = date.minusDays(1);
        while (prev.getDayOfWeek() == DayOfWeek.SATURDAY || prev.getDayOfWeek() == DayOfWeek.SUNDAY) {
            prev = prev.minusDays(1);
        }
        return prev;
    }
}
