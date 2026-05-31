package com.invest_view.stream_detection.rule;

import com.invest_view.events.Market;
import com.invest_view.stream_detection.alert.AlertBuilders;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.functions.AggregateFunction;

public class PriceAlertAggregator
        implements AggregateFunction<StockTick, PriceAccumulator, PriceAccumulator> {

    private static final long serialVersionUID = 1L;

    /** Eligibility: trading_halted=="N", price>0, market∈{KRX,NXT}. */
    public static boolean isEligible(StockTick tick) {
        if (!"N".equals(tick.getTradingHalted())) {
            return false;
        }
        if (tick.getPrice() <= 0) {
            return false;
        }
        return AlertBuilders.normalizeMarket(tick.getMarket()).isPresent();
    }

    @Override
    public PriceAccumulator createAccumulator() {
        return new PriceAccumulator();
    }

    @Override
    public PriceAccumulator add(StockTick tick, PriceAccumulator acc) {
        int price = tick.getPrice();
        if (acc.getMinPrice() == 0) {
            acc.setMinPrice(price);
            acc.setMaxPrice(price);
            acc.setSymbol(tick.getSymbol());
            Market market = AlertBuilders.normalizeMarket(tick.getMarket()).orElse(Market.KRX);
            acc.setMarket(market);
        } else {
            if (price < acc.getMinPrice()) {
                acc.setMinPrice(price);
            }
            if (price > acc.getMaxPrice()) {
                acc.setMaxPrice(price);
            }
        }
        return acc;
    }

    @Override
    public PriceAccumulator getResult(PriceAccumulator acc) {
        return acc;
    }

    @Override
    public PriceAccumulator merge(PriceAccumulator a, PriceAccumulator b) {
        if (a.getMinPrice() == 0) {
            return b;
        }
        if (b.getMinPrice() == 0) {
            return a;
        }
        PriceAccumulator out = new PriceAccumulator();
        out.setMinPrice(Math.min(a.getMinPrice(), b.getMinPrice()));
        out.setMaxPrice(Math.max(a.getMaxPrice(), b.getMaxPrice()));
        out.setSymbol(a.getSymbol());
        out.setMarket(a.getMarket());
        return out;
    }
}
