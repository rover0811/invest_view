package com.invest_view.stream_detection.move;

import com.investview.ticks.StockTick;

import java.math.BigDecimal;

final class MoveTestTicks {

    static final int PREV_CLOSE = 10_000;

    private MoveTestTicks() {
    }

    static StockTick tick(String symbol, String businessDate, String tradeTime, int price) {
        int change = price - PREV_CLOSE;
        String sign = change > 0 ? "2" : change < 0 ? "5" : "3";
        return tick(symbol, businessDate, tradeTime, price, sign, Math.abs(change));
    }

    static StockTick tick(
            String symbol, String businessDate, String tradeTime, int price, String changeSign, int change) {
        return tick("KRX", symbol, businessDate, tradeTime, price, changeSign, change);
    }

    static StockTick nxtTick(String symbol, String businessDate, String tradeTime, int price) {
        int change = price - PREV_CLOSE;
        String sign = change > 0 ? "2" : change < 0 ? "5" : "3";
        return tick("NXT", symbol, businessDate, tradeTime, price, sign, Math.abs(change));
    }

    private static StockTick tick(
            String market, String symbol, String businessDate, String tradeTime, int price,
            String changeSign, int change) {
        return StockTick.newBuilder()
                .setSourceTrId("test")
                .setMarket(market)
                .setReceivedAt("2026-06-01T00:00:00Z")
                .setSymbol(symbol)
                .setTradeTime(tradeTime)
                .setPrice(price)
                .setChangeSign(changeSign)
                .setChange(change)
                .setChangeRate(BigDecimal.ZERO)
                .setVwap(BigDecimal.ZERO)
                .setOpen(price)
                .setHigh(price)
                .setLow(price)
                .setAskPrice1(price)
                .setBidPrice1(price)
                .setTradeVolume(1)
                .setCumulativeVolume(1)
                .setCumulativeAmount(1)
                .setSellCount(0)
                .setBuyCount(0)
                .setNetBuyCount(0)
                .setTradeStrength(BigDecimal.ZERO)
                .setTotalSellVolume(0)
                .setTotalBuyVolume(0)
                .setTradeType("0")
                .setBuyRatio(BigDecimal.ZERO)
                .setPrevDayVolumeRate(BigDecimal.ZERO)
                .setOpenTime(tradeTime)
                .setOpenVsSign("2")
                .setOpenVsPrice(0)
                .setHighTime(tradeTime)
                .setHighVsSign("2")
                .setHighVsPrice(0)
                .setLowTime(tradeTime)
                .setLowVsSign("5")
                .setLowVsPrice(0)
                .setBusinessDate(businessDate)
                .setMarketSessionCode("1")
                .setTradingHalted("N")
                .setAskRemain1(0)
                .setBidRemain1(0)
                .setTotalAskRemain(0)
                .setTotalBidRemain(0)
                .setVolumeTurnover(BigDecimal.ZERO)
                .setPrevSameHourVolume(0)
                .setPrevSameHourVolumeRate(BigDecimal.ZERO)
                .setHourClassCode("0")
                .setMarketTerminationCode("0")
                .setViTriggerPrice(0)
                .build();
    }
}
