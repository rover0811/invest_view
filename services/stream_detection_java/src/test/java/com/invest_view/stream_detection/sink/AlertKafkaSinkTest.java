package com.invest_view.stream_detection.sink;

import org.apache.avro.generic.GenericRecord;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertNotNull;

public class AlertKafkaSinkTest {

    @Test
    public void buildReturnsNonNullSinkWithInternalHostnames() {
        KafkaSink<GenericRecord> sink = AlertKafkaSink.build(
                "kafka:29092",
                "http://schema-registry:8081");

        assertNotNull(sink, "AlertKafkaSink.build(...) must return a non-null KafkaSink");
    }
}
