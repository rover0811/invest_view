package com.invest_view.stream_detection.move;

import java.util.List;

public final class MoveRules {

    private MoveRules() {
    }

    public static List<MoveRule> phase1() {
        return List.of(
                new DailyMoveRule());
    }
}
