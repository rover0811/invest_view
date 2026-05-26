package com.invest_view.stream_detection.model;

import org.apache.avro.generic.GenericRecord;

/**
 * Static accessors over an Avro {@link GenericRecord} that conforms to
 * {@code stock-ticks.avsc}. This is intentionally NOT a wrapper type — rules
 * keep operating on {@code GenericRecord} (mirroring the dict-style API used
 * in PyFlink) and call these helpers to read the handful of fields the
 * detection rules need.
 *
 * <p>Decimal-logicalType field accessors are deliberately omitted; they are
 * deferred to Task 4.4 (decimal converters).
 *
 * <p>Note: Avro decodes {@code string} fields as
 * {@link org.apache.avro.util.Utf8}, so we call {@code toString()} rather
 * than casting to {@code String}.
 */
public final class TickRecord {

    private TickRecord() {
    }

    public static String getSymbol(GenericRecord tick) {
        return tick.get("symbol").toString();
    }

    public static String getMarket(GenericRecord tick) {
        return tick.get("market").toString();
    }

    public static int getPrice(GenericRecord tick) {
        return (Integer) tick.get("price");
    }

    public static String getTradingHalted(GenericRecord tick) {
        Object v = tick.get("trading_halted");
        return v == null ? null : v.toString();
    }

    public static int getViTriggerPrice(GenericRecord tick) {
        return (Integer) tick.get("vi_trigger_price");
    }

    public static String getReceivedAt(GenericRecord tick) {
        return tick.get("received_at").toString();
    }

    public static String getTradeTime(GenericRecord tick) {
        Object v = tick.get("trade_time");
        return v == null ? null : v.toString();
    }
}
