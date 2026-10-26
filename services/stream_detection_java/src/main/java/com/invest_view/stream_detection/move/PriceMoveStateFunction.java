package com.invest_view.stream_detection.move;

import com.invest_view.events.PriceMove;
import com.invest_view.events.PriceState;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.state.MapState;
import org.apache.flink.api.common.state.MapStateDescriptor;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.List;
import java.util.Optional;

/**
 * Keyed by symbol. Runs the price state machine on every tick, resets it at a
 * new business date, and hands each {@link PriceUpdate} to the registered
 * {@link MoveRule}s.
 *
 * <p>Only regular-session KRX ticks feed the DAILY state. NXT pre-market and
 * after-hours ticks carry the same symbol but a different venue price, so they
 * would move the state machine and bump move versions outside KRX hours.
 */
public class PriceMoveStateFunction extends KeyedProcessFunction<String, StockTick, PriceMove> {

    private static final long serialVersionUID = 1L;

    public static final String BUSINESS_DATE_STATE = "price_move_business_date";
    public static final String PREV_BUSINESS_DATE_STATE = "price_move_prev_business_date";
    public static final String PRICE_STATE_STATE = "price_move_price_state";
    public static final String MOVE_VERSIONS_STATE = "price_move_versions";
    /** stock-ticks {@code market} value emitted by kis_ingestion for the KRX session. */
    public static final String DAILY_MARKET = "KRX";

    private final PriceStateMachine machine;
    private final List<MoveRule> rules;

    private transient ValueState<String> businessDate;
    private transient ValueState<String> prevBusinessDate;
    private transient ValueState<String> priceState;
    private transient MapState<String, Integer> moveVersions;

    public PriceMoveStateFunction(PriceStateMachine machine, List<MoveRule> rules) {
        this.machine = machine;
        this.rules = List.copyOf(rules);
    }

    @Override
    public void open(Configuration parameters) {
        businessDate = getRuntimeContext().getState(
                new ValueStateDescriptor<>(BUSINESS_DATE_STATE, String.class));
        prevBusinessDate = getRuntimeContext().getState(
                new ValueStateDescriptor<>(PREV_BUSINESS_DATE_STATE, String.class));
        priceState = getRuntimeContext().getState(
                new ValueStateDescriptor<>(PRICE_STATE_STATE, String.class));
        moveVersions = getRuntimeContext().getMapState(
                new MapStateDescriptor<>(MOVE_VERSIONS_STATE, String.class, Integer.class));
    }

    @Override
    public void processElement(StockTick tick, Context ctx, Collector<PriceMove> out) throws Exception {
        if (!DAILY_MARKET.equals(tick.getMarket())) {
            return;
        }
        Optional<Integer> prevClose = TickPrices.prevClose(tick);
        if (prevClose.isEmpty()) {
            return;
        }
        LocalDate tickDate;
        long eventTimeMs;
        try {
            tickDate = TickPrices.businessDate(tick);
            eventTimeMs = TickPrices.tradeEventTimeMs(tick);
        } catch (DateTimeParseException e) {
            return;
        }

        String stored = businessDate.value();
        if (stored == null || tickDate.isAfter(LocalDate.parse(stored))) {
            startBusinessDate(tickDate, stored);
        } else if (tickDate.isBefore(LocalDate.parse(stored))) {
            return;
        }

        PriceState previous = PriceState.valueOf(priceState.value());
        double returnPct = TickPrices.returnPct(tick.getPrice(), prevClose.get());
        PriceState current = machine.next(previous, returnPct);
        priceState.update(current.name());

        PriceUpdate update = new PriceUpdate(
                tick.getSymbol(),
                tickDate,
                LocalDate.parse(prevBusinessDate.value()),
                eventTimeMs,
                tick.getPrice(),
                prevClose.get(),
                returnPct,
                previous,
                current);

        MoveLedger ledger = new StateLedger(moveVersions);
        for (MoveRule rule : rules) {
            for (PriceMove move : rule.onPriceUpdate(update, ledger)) {
                out.collect(move);
            }
        }
    }

    private void startBusinessDate(LocalDate tickDate, String stored) throws Exception {
        LocalDate prev = stored == null ? CandidateRanges.previousWeekday(tickDate) : LocalDate.parse(stored);
        businessDate.update(tickDate.toString());
        prevBusinessDate.update(prev.toString());
        priceState.update(PriceState.NEUTRAL.name());
        moveVersions.clear();
    }

    private static final class StateLedger implements MoveLedger {

        private final MapState<String, Integer> versions;

        private StateLedger(MapState<String, Integer> versions) {
            this.versions = versions;
        }

        @Override
        public int lastVersion(String moveId) throws Exception {
            Integer version = versions.get(moveId);
            return version == null ? 0 : version;
        }

        @Override
        public void recordVersion(String moveId, int version) throws Exception {
            versions.put(moveId, version);
        }
    }
}
