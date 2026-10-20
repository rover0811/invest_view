package com.invest_view.stream_detection.move;

import com.invest_view.events.PriceMove;

import java.io.Serializable;
import java.util.List;

/**
 * Move generation rule for one move type. Register new rules in {@link MoveRules}.
 */
public interface MoveRule extends Serializable {

    List<PriceMove> onPriceUpdate(PriceUpdate update, MoveLedger ledger) throws Exception;
}
