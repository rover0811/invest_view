package com.invest_view.stream_detection.watermark;

import com.investview.ticks.StockTick;
import org.apache.flink.api.common.eventtime.SerializableTimestampAssigner;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;

import java.time.Duration;
import java.time.Instant;

/**
 * Event-time watermark strategy for {@link StockTick} records.
 *
 * <p>Event time is extracted from {@link StockTick#getReceivedAt()} — an ISO 8601
 * UTC timestamp string emitted by {@code kis_ingestion}. The strategy tolerates
 * bounded out-of-orderness up to 10 seconds, and activates idleness after one
 * minute without input so downstream windows still advance during quiet periods.
 *
 * <p>This wraps the Python parity (see {@code services/stream_detection/src/stream_detection/watermark.py}).
 */
public final class TickWatermarkStrategy {

    private static final Duration OUT_OF_ORDERNESS = Duration.ofSeconds(10);
    private static final Duration IDLE_TIMEOUT = Duration.ofMinutes(1);

    private TickWatermarkStrategy() {
    }

    public static WatermarkStrategy<StockTick> forTicks() {
        return WatermarkStrategy
                .<StockTick>forBoundedOutOfOrderness(OUT_OF_ORDERNESS)
                .withTimestampAssigner((SerializableTimestampAssigner<StockTick>)
                        (tick, recordTimestamp) -> parseIso8601ToMs(tick.getReceivedAt()))
                .withIdleness(IDLE_TIMEOUT);
    }

    /**
     * Parses an ISO 8601 timestamp string to epoch milliseconds.
     *
     * @throws java.time.format.DateTimeParseException if {@code iso} is not a
     *         valid ISO 8601 timestamp.
     */
    public static long parseIso8601ToMs(String iso) {
        return Instant.parse(iso).toEpochMilli();
    }
}
