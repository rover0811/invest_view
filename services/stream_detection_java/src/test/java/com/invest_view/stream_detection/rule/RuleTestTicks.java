package com.invest_view.stream_detection.rule;

import com.investview.ticks.StockTick;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

final class RuleTestTicks {

    private static final LocalDate BASE_DATE = LocalDate.of(2026, 6, 1);
    private static final LocalTime BASE_TIME = LocalTime.of(9, 0);
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.BASIC_ISO_DATE;
    private static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ofPattern("HHmmss");

    private RuleTestTicks() {
    }

    static StockTick tickAtBucket(int bucketIndex, int price) {
        return tickAt(bucketIndex, 0, price, "KRX");
    }

    static StockTick tickAt(int bucketIndex, int secondsIntoBucket, int price, String market) {
        LocalDateTime tradeDateTime = LocalDateTime.of(BASE_DATE, BASE_TIME)
                .plusMinutes((long) bucketIndex * 5L)
                .plusSeconds(secondsIntoBucket);
        String businessDate = tradeDateTime.toLocalDate().format(DATE_FORMATTER);
        String tradeTime = tradeDateTime.toLocalTime().format(TIME_FORMATTER);
        String receivedAt = tradeDateTime.atZone(KST).toInstant().toString();
        return tick("005930", market, businessDate, tradeTime, receivedAt, price);
    }

    private static StockTick tick(
            String symbol,
            String market,
            String businessDate,
            String tradeTime,
            String receivedAt,
            int price) {
        return StockTick.newBuilder()
                .setSourceTrId("test")
                .setMarket(market)
                .setReceivedAt(receivedAt)
                .setSymbol(symbol)
                .setTradeTime(tradeTime)
                .setPrice(price)
                .setChangeSign("2")
                .setChange(0)
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
