package com.invest_view.stream_detection;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PriceMoveStateJobConfigTest {

    @Test
    void defaults() {
        PriceMoveStateJobConfig config = PriceMoveStateJobConfig.fromEnv(Map.of(
                "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092",
                "SCHEMA_REGISTRY_URL", "http://sr:8081")::get);

        assertEquals("price-move-state-java", config.consumerGroupId());
        assertEquals(1.0, config.priceStateEnterPct());
        assertEquals(0.5, config.priceStateExitPct());
        assertEquals("file:///opt/flink/checkpoints/price-move-state", config.checkpointDir());
        assertEquals(1, config.parallelism());
    }

    @Test
    void requiresBootstrap() {
        assertThrows(NullPointerException.class, () -> PriceMoveStateJobConfig.fromEnv(Map.of(
                "SCHEMA_REGISTRY_URL", "http://sr:8081")::get));
    }
}
