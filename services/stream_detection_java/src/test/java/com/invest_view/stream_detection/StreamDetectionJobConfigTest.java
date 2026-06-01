package com.invest_view.stream_detection;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StreamDetectionJobConfigTest {

    private static Function<String, String> env(Map<String, String> entries) {
        return entries::get;
    }

    @Test
    void testMissingKafkaBootstrapThrows() {
        Map<String, String> entries = new HashMap<>();
        entries.put("SCHEMA_REGISTRY_URL", "http://sr:8081");

        assertThrows(NullPointerException.class, () -> StreamDetectionJobConfig.fromEnv(env(entries)));
    }

    @Test
    void testMissingSchemaRegistryThrows() {
        Map<String, String> entries = new HashMap<>();
        entries.put("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092");

        assertThrows(NullPointerException.class, () -> StreamDetectionJobConfig.fromEnv(env(entries)));
    }

    @Test
    void testDefaultsApplied() {
        Map<String, String> entries = new HashMap<>();
        entries.put("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092");
        entries.put("SCHEMA_REGISTRY_URL", "http://sr:8081");

        StreamDetectionJobConfig config = StreamDetectionJobConfig.fromEnv(env(entries));

        assertEquals("kafka:29092", config.kafkaBootstrapServers());
        assertEquals("http://sr:8081", config.schemaRegistryUrl());
        assertEquals(0.03, config.priceAlertThreshold(), 1e-9);
        assertEquals(0.01, config.viProximityThreshold(), 1e-9);
        assertEquals(14, config.rsiPeriod());
        assertEquals(30.0, config.rsiOversold(), 1e-9);
        assertEquals(70.0, config.rsiOverbought(), 1e-9);
        assertEquals(5, config.maShortPeriod());
        assertEquals(20, config.maLongPeriod());
        assertEquals(12, config.macdFastPeriod());
        assertEquals(26, config.macdSlowPeriod());
        assertEquals(9, config.macdSignalPeriod());
        assertEquals("file:///opt/flink/checkpoints", config.checkpointDir());
        assertEquals(1, config.parallelism());
    }

    @Test
    void testOptionalOverrides() {
        Map<String, String> entries = new HashMap<>();
        entries.put("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092");
        entries.put("SCHEMA_REGISTRY_URL", "http://sr:8081");
        entries.put("PRICE_ALERT_THRESHOLD", "0.05");
        entries.put("VI_PROXIMITY_THRESHOLD", "0.02");
        entries.put("RSI_PERIOD", "10");
        entries.put("RSI_OVERSOLD", "25.5");
        entries.put("RSI_OVERBOUGHT", "75.5");
        entries.put("MA_SHORT_PERIOD", "3");
        entries.put("MA_LONG_PERIOD", "8");
        entries.put("MACD_FAST_PERIOD", "6");
        entries.put("MACD_SLOW_PERIOD", "13");
        entries.put("MACD_SIGNAL_PERIOD", "5");
        entries.put("CHECKPOINT_DIR", "file:///tmp/cp");
        entries.put("PARALLELISM", "4");

        StreamDetectionJobConfig config = StreamDetectionJobConfig.fromEnv(env(entries));

        assertEquals(0.05, config.priceAlertThreshold(), 1e-9);
        assertEquals(0.02, config.viProximityThreshold(), 1e-9);
        assertEquals(10, config.rsiPeriod());
        assertEquals(25.5, config.rsiOversold(), 1e-9);
        assertEquals(75.5, config.rsiOverbought(), 1e-9);
        assertEquals(3, config.maShortPeriod());
        assertEquals(8, config.maLongPeriod());
        assertEquals(6, config.macdFastPeriod());
        assertEquals(13, config.macdSlowPeriod());
        assertEquals(5, config.macdSignalPeriod());
        assertEquals("file:///tmp/cp", config.checkpointDir());
        assertEquals(4, config.parallelism());
    }
}
