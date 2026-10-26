package com.invest_view.stream_detection.move;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.NameBasedGenerator;
import com.invest_view.events.MoveSignalStatus;
import com.invest_view.events.MoveType;
import com.invest_view.events.PriceMove;
import com.invest_view.events.PriceState;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/**
 * DAILY move (doc 22, section 10): opened the first time the return against
 * the previous KRX close leaves NEUTRAL in a business date, then re-emitted
 * with {@code version + 1} on every later price state transition that day.
 */
public final class DailyMoveRule implements MoveRule {

    private static final long serialVersionUID = 1L;

    private static final UUID NAMESPACE_DNS = UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");
    private static final NameBasedGenerator UUID5_GEN = Generators.nameBasedGenerator(NAMESPACE_DNS);

    public static String moveId(String symbol, LocalDate businessDate) {
        return UUID5_GEN.generate(symbol + "|" + businessDate + "|" + MoveType.DAILY).toString();
    }

    @Override
    public List<PriceMove> onPriceUpdate(PriceUpdate update, MoveLedger ledger) throws Exception {
        String moveId = moveId(update.symbol(), update.businessDate());
        int lastVersion = ledger.lastVersion(moveId);

        boolean opens = lastVersion == 0 && update.currentState() != PriceState.NEUTRAL;
        boolean transitions = lastVersion > 0 && update.isTransition();
        if (!opens && !transitions) {
            return List.of();
        }

        int version = lastVersion + 1;
        ledger.recordVersion(moveId, version);

        CandidateRanges.Range range = CandidateRanges.of(
                MoveType.DAILY, update.businessDate(), update.prevBusinessDate());
        return List.of(PriceMove.newBuilder()
                .setMoveId(moveId)
                .setMoveType(MoveType.DAILY)
                .setSymbol(update.symbol())
                .setWindowStart(Instant.ofEpochMilli(range.startMs()))
                .setWindowEnd(Instant.ofEpochMilli(update.eventTimeMs()))
                .setReturnPct(update.returnPct())
                .setPriceState(update.currentState())
                .setContribGlobal(null)
                .setContribMarket(null)
                .setContribSector(null)
                .setContribIdio(null)
                .setBetaMethod(null)
                .setBetaAsOf(null)
                .setDominantLayer(null)
                .setSignalStatus(MoveSignalStatus.HIDDEN)
                .setVersion(version)
                .build());
    }
}
