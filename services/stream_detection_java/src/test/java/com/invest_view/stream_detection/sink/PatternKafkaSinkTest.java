package com.invest_view.stream_detection.sink;

import com.invest_view.events.StockPattern;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertNotNull;

public class PatternKafkaSinkTest {

    @Test
    public void buildReturnsNonNullSinkWithInternalHostnames() {
        KafkaSink<StockPattern> sink = PatternKafkaSink.build(
                "kafka:29092",
                "http://schema-registry:8081");

        assertNotNull(sink, "PatternKafkaSink.build(...) must return a non-null KafkaSink");
    }
}
