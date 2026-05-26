package com.invest_view.stream_detection.source;

import org.apache.avro.generic.GenericRecord;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

public class TickKafkaSourceTest {

    private static final String BOOTSTRAP = "kafka:29092";
    private static final String SCHEMA_REGISTRY_URL = "http://schema-registry:8081";

    @Test
    public void buildReturnsConfiguredKafkaSource() {
        KafkaSource<GenericRecord> source = TickKafkaSource.build(BOOTSTRAP, SCHEMA_REGISTRY_URL);
        assertNotNull(source, "KafkaSource builder must produce a non-null source");
    }

    @Test
    public void constantsExposeStableContract() {
        assertEquals("stock-ticks", TickKafkaSource.TOPIC);
        assertEquals("stream-detection-java", TickKafkaSource.GROUP_ID);
    }
}
