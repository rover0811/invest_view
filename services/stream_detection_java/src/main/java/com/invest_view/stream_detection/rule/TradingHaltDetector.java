package com.invest_view.stream_detection.rule;

import com.invest_view.events.Market;
import com.invest_view.events.StockAlert;
import com.invest_view.stream_detection.alert.AlertBuilders;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;

import java.time.Instant;
import java.util.Optional;

public class TradingHaltDetector
        extends KeyedProcessFunction<String, StockTick, StockAlert> {

    private static final long serialVersionUID = 1L;
    public static final String STATE_NAME = "trading_halt_last_state";

    private transient ValueState<String> lastState;

    @Override
    public void open(Configuration parameters) {
        this.lastState = getRuntimeContext().getState(
                new ValueStateDescriptor<>(STATE_NAME, String.class));
    }

    @Override
    public void processElement(StockTick tick, Context ctx, Collector<StockAlert> out) throws Exception {
        String current = tick.getTradingHalted();
        if (!"N".equals(current) && !"Y".equals(current)) {
            return;
        }

        String prev = lastState.value();
        if ("N".equals(prev) && "Y".equals(current)) {
            Optional<Market> marketOpt = AlertBuilders.normalizeMarket(tick.getMarket());
            if (marketOpt.isPresent()) {
                long transitionTimeMs = Instant.parse(tick.getReceivedAt()).toEpochMilli();
                out.collect(AlertBuilders.buildTradingHalt(
                        tick.getSymbol(),
                        marketOpt.get(),
                        prev,
                        current,
                        transitionTimeMs,
                        tick.getTradeTime()));
            }
        }

        lastState.update(current);
    }
}
