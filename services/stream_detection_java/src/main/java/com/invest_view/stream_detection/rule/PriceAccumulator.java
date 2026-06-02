package com.invest_view.stream_detection.rule;

import com.invest_view.events.Market;

import java.io.Serializable;

public class PriceAccumulator implements Serializable {

    private static final long serialVersionUID = 1L;

    private int minPrice;
    private int maxPrice;
    private String symbol;
    private Market market;

    public PriceAccumulator() {
        this.minPrice = 0;
        this.maxPrice = 0;
        this.symbol = "";
        this.market = null;
    }

    public int getMinPrice() {
        return minPrice;
    }

    public void setMinPrice(int minPrice) {
        this.minPrice = minPrice;
    }

    public int getMaxPrice() {
        return maxPrice;
    }

    public void setMaxPrice(int maxPrice) {
        this.maxPrice = maxPrice;
    }

    public String getSymbol() {
        return symbol;
    }

    public void setSymbol(String symbol) {
        this.symbol = symbol;
    }

    public Market getMarket() {
        return market;
    }

    public void setMarket(Market market) {
        this.market = market;
    }
}
