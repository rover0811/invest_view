package com.invest_view.stream_detection.move;

/**
 * Per-symbol record of the last emitted version of each open move in the current business date.
 */
public interface MoveLedger {

    /** Last emitted version, or 0 when the move has not been emitted yet. */
    int lastVersion(String moveId) throws Exception;

    void recordVersion(String moveId, int version) throws Exception;
}
