package com.invest_view.stream_detection.move;

import com.invest_view.events.MoveSignalStatus;
import com.invest_view.events.MoveType;
import com.invest_view.events.PriceMove;
import com.invest_view.events.PriceState;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.api.java.functions.KeySelector;
import org.apache.flink.streaming.api.operators.KeyedProcessOperator;
import org.apache.flink.streaming.runtime.streamrecord.StreamRecord;
import org.apache.flink.streaming.util.KeyedOneInputStreamOperatorTestHarness;
import org.apache.flink.runtime.checkpoint.OperatorSubtaskState;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PriceMoveStateFunctionTest {

    private static final String SYMBOL = "005930";
    private static final String MONDAY = "20260601";
    private static final String TUESDAY = "20260602";

    @Test
    void noMoveWhileNeutral() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "090000", 10_050);
            feed(h, MONDAY, "090100", 9_950);
            feed(h, MONDAY, "090200", 10_099);
            assertTrue(moves(h).isEmpty());
        }
    }

    @Test
    void firstCrossingOpensDailyMoveVersionOne() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "090000", 10_050);
            feed(h, MONDAY, "091500", 10_120);

            List<PriceMove> moves = moves(h);
            assertEquals(1, moves.size());
            PriceMove move = moves.get(0);
            assertEquals(DailyMoveRule.moveId(SYMBOL, LocalDate.of(2026, 6, 1)), move.getMoveId());
            assertEquals(MoveType.DAILY, move.getMoveType());
            assertEquals(SYMBOL, move.getSymbol());
            assertEquals(PriceState.POSITIVE, move.getPriceState());
            assertEquals(1.2, move.getReturnPct(), 1e-9);
            assertEquals(1, move.getVersion());
            assertEquals(MoveSignalStatus.HIDDEN, move.getSignalStatus());
            assertEquals(Instant.parse("2026-05-29T06:30:00Z"), move.getWindowStart());
            assertEquals(Instant.parse("2026-06-01T00:15:00Z"), move.getWindowEnd());
            assertNull(move.getContribGlobal());
            assertNull(move.getContribMarket());
            assertNull(move.getContribSector());
            assertNull(move.getContribIdio());
            assertNull(move.getBetaMethod());
            assertNull(move.getBetaAsOf());
            assertNull(move.getDominantLayer());
        }
    }

    @Test
    void ticksInsideHysteresisDoNotReEmit() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "091500", 10_120);
            feed(h, MONDAY, "091600", 10_200);
            feed(h, MONDAY, "091700", 10_060);
            assertEquals(1, moves(h).size());
        }
    }

    @Test
    void laterTransitionsBumpVersionOfSameMove() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "091500", 10_120);
            feed(h, MONDAY, "100000", 10_040);
            feed(h, MONDAY, "110000", 9_880);

            List<PriceMove> moves = moves(h);
            assertEquals(List.of(PriceState.POSITIVE, PriceState.NEUTRAL, PriceState.NEGATIVE),
                    moves.stream().map(PriceMove::getPriceState).collect(Collectors.toList()));
            assertEquals(List.of(1, 2, 3),
                    moves.stream().map(PriceMove::getVersion).collect(Collectors.toList()));
            assertEquals(1, moves.stream().map(PriceMove::getMoveId).distinct().count());
        }
    }

    @Test
    void newBusinessDateResetsStateAndUsesObservedPrevDate() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "091500", 10_120);
            feed(h, TUESDAY, "090000", 10_150);

            List<PriceMove> moves = moves(h);
            assertEquals(2, moves.size());
            PriceMove tuesday = moves.get(1);
            assertNotEquals(moves.get(0).getMoveId(), tuesday.getMoveId());
            assertEquals(1, tuesday.getVersion());
            assertEquals(Instant.parse("2026-06-01T06:30:00Z"), tuesday.getWindowStart());
        }
    }

    @Test
    void lateTickFromPreviousDateIsIgnored() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, TUESDAY, "090000", 10_000);
            feed(h, MONDAY, "153000", 10_500);
            assertTrue(moves(h).isEmpty());
        }
    }

    @Test
    void invalidTicksAreSkipped() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            h.processElement(new StreamRecord<>(MoveTestTicks.tick(SYMBOL, MONDAY, "090000", 10_500, "9", 500)));
            h.processElement(new StreamRecord<>(MoveTestTicks.tick(SYMBOL, "bad", "090000", 10_500)));
            assertTrue(moves(h).isEmpty());
        }
    }

    @Test
    void nxtTicksDoNotTouchDailyState() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            h.processElement(new StreamRecord<>(MoveTestTicks.nxtTick(SYMBOL, MONDAY, "083000", 10_300)));
            assertTrue(moves(h).isEmpty());

            feed(h, MONDAY, "091500", 10_120);
            h.processElement(new StreamRecord<>(MoveTestTicks.nxtTick(SYMBOL, MONDAY, "160000", 9_800)));
            h.processElement(new StreamRecord<>(MoveTestTicks.nxtTick(SYMBOL, TUESDAY, "080500", 9_800)));

            List<PriceMove> moves = moves(h);
            assertEquals(1, moves.size());
            assertEquals(1, moves.get(0).getVersion());
            assertEquals(PriceState.POSITIVE, moves.get(0).getPriceState());
            assertEquals(Instant.parse("2026-06-01T00:15:00Z"), moves.get(0).getWindowEnd());
        }
    }

    @Test
    void symbolsAreIndependent() throws Exception {
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            h.processElement(new StreamRecord<>(MoveTestTicks.tick("000660", MONDAY, "091500", 9_890)));
            feed(h, MONDAY, "091500", 10_000);
            List<PriceMove> moves = moves(h);
            assertEquals(1, moves.size());
            assertEquals("000660", moves.get(0).getSymbol());
            assertEquals(PriceState.NEGATIVE, moves.get(0).getPriceState());
        }
    }

    @Test
    void stateSurvivesSnapshotRestore() throws Exception {
        OperatorSubtaskState snapshot;
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness()) {
            feed(h, MONDAY, "091500", 10_120);
            snapshot = h.snapshot(1L, 1L);
        }
        try (KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h = harness(snapshot)) {
            feed(h, MONDAY, "091600", 10_200);
            feed(h, MONDAY, "100000", 10_000);
            List<PriceMove> moves = moves(h);
            assertEquals(1, moves.size());
            assertEquals(PriceState.NEUTRAL, moves.get(0).getPriceState());
            assertEquals(2, moves.get(0).getVersion());
        }
    }

    private static void feed(
            KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h,
            String businessDate, String tradeTime, int price) throws Exception {
        h.processElement(new StreamRecord<>(MoveTestTicks.tick(SYMBOL, businessDate, tradeTime, price)));
    }

    private static List<PriceMove> moves(KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h) {
        return h.extractOutputValues();
    }

    private static KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> harness() throws Exception {
        return harness(null);
    }

    private static KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> harness(
            OperatorSubtaskState restore) throws Exception {
        KeyedProcessOperator<String, StockTick, PriceMove> operator = new KeyedProcessOperator<>(
                new PriceMoveStateFunction(PriceStateMachine.defaults(), MoveRules.phase1()));
        KeyedOneInputStreamOperatorTestHarness<String, StockTick, PriceMove> h =
                new KeyedOneInputStreamOperatorTestHarness<>(
                        operator,
                        (KeySelector<StockTick, String>) StockTick::getSymbol,
                        TypeInformation.of(String.class));
        if (restore != null) {
            h.initializeState(restore);
        }
        h.open();
        return h;
    }
}
