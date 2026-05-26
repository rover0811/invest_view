package com.invest_view.stream_detection.model;

import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

public class TickRecordTest {

    @Test
    public void schemasLoad() {
        assertNotNull(Schemas.TICK, "stock-ticks schema must load");
        assertNotNull(Schemas.ALERT, "stock-alerts schema must load");
        assertEquals("StockTick", Schemas.TICK.getName());
        assertEquals("StockAlert", Schemas.ALERT.getName());
    }

    @Test
    public void tickRecordAccessors() {
        GenericData.Record tick = new GenericData.Record(Schemas.TICK);
        tick.put("source_tr_id", "test");
        tick.put("market", "KRX");
        tick.put("received_at", "2026-05-26T03:00:00.000Z");
        tick.put("symbol", "005930");
        tick.put("trade_time", "030000");
        tick.put("price", 70000);
        tick.put("change_sign", "5");
        tick.put("change", 0);
        ByteBuffer zero = ByteBuffer.wrap(BigInteger.ZERO.toByteArray());
        tick.put("change_rate", zero.duplicate());
        tick.put("vwap", zero.duplicate());
        tick.put("open", 70000);
        tick.put("high", 70000);
        tick.put("low", 70000);
        tick.put("ask_price_1", 70000);
        tick.put("bid_price_1", 70000);
        tick.put("trade_volume", 0);
        tick.put("cumulative_volume", 0);
        tick.put("cumulative_amount", 0);
        tick.put("sell_count", 0);
        tick.put("buy_count", 0);
        tick.put("net_buy_count", 0);
        tick.put("trade_strength", zero.duplicate());
        tick.put("total_sell_volume", 0);
        tick.put("total_buy_volume", 0);
        tick.put("trade_type", "1");
        tick.put("buy_ratio", zero.duplicate());
        tick.put("prev_day_volume_rate", zero.duplicate());
        tick.put("open_time", "090000");
        tick.put("open_vs_sign", "3");
        tick.put("open_vs_price", 0);
        tick.put("high_time", "090000");
        tick.put("high_vs_sign", "3");
        tick.put("high_vs_price", 0);
        tick.put("low_time", "090000");
        tick.put("low_vs_sign", "3");
        tick.put("low_vs_price", 0);
        tick.put("business_date", "20260526");
        tick.put("market_session_code", "20");
        tick.put("trading_halted", "N");
        tick.put("ask_remain_1", 0);
        tick.put("bid_remain_1", 0);
        tick.put("total_ask_remain", 0);
        tick.put("total_bid_remain", 0);
        tick.put("volume_turnover", zero.duplicate());
        tick.put("prev_same_hour_volume", 0);
        tick.put("prev_same_hour_volume_rate", zero.duplicate());
        tick.put("hour_class_code", "0");
        tick.put("market_termination_code", "0");
        tick.put("vi_trigger_price", 0);

        assertEquals("005930", TickRecord.getSymbol(tick));
        assertEquals("KRX", TickRecord.getMarket(tick));
        assertEquals(70000, TickRecord.getPrice(tick));
        assertEquals("N", TickRecord.getTradingHalted(tick));
        assertEquals(0, TickRecord.getViTriggerPrice(tick));
        assertEquals("2026-05-26T03:00:00.000Z", TickRecord.getReceivedAt(tick));
    }

    @Test
    public void alertRecordBuildWithEnum() {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("alert_event_id", "00000000-0000-0000-0000-000000000000");
        fields.put("symbol", "005930");
        fields.put("market", "KRX");
        fields.put("alert_type", "PRICE_ALERT");
        fields.put("severity", "WARNING");
        fields.put("observation_start_at", 0L);
        fields.put("observation_end_at", 0L);
        fields.put("triggered_at", 0L);
        fields.put("trigger_values", Map.of("k", "v"));
        fields.put("source_tick_event_id", null);
        fields.put("rule_name", "price_alert_5min_3pct");

        GenericRecord alert = AlertRecord.build(Schemas.ALERT, fields);
        assertEquals("005930", alert.get("symbol").toString());
        assertNotNull(alert.get("market"));
        assertTrue(
                alert.get("market") instanceof GenericData.EnumSymbol,
                "market field must be EnumSymbol so ConfluentRegistryAvroSerializationSchema accepts it"
        );
        assertEquals("KRX", alert.get("market").toString());
        assertEquals("PRICE_ALERT", alert.get("alert_type").toString());
        assertEquals("WARNING", alert.get("severity").toString());
    }
}
