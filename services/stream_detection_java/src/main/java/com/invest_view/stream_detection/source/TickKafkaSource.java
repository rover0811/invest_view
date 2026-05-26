package com.invest_view.stream_detection.source;

import com.invest_view.stream_detection.model.Schemas;
import org.apache.avro.generic.GenericRecord;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;

/**
 * Builds a Flink 1.18 {@link KafkaSource} that reads Avro-encoded
 * {@code stock-ticks} records via Confluent Schema Registry, emitting
 * {@link GenericRecord} payloads decoded against {@link Schemas#TICK}.
 *
 * <p>Bootstrap server and Schema Registry URL are parameters of
 * {@link #build(String, String)} — they are NOT hardcoded. Auto-registration
 * of schemas is intentionally not enabled; subjects are pre-registered by the
 * {@code schema_registry_setup} job in {@code docker-compose.yml}.
 */
public final class TickKafkaSource {

    public static final String TOPIC = "stock-ticks";

    public static final String GROUP_ID = "stream-detection-java";

    private TickKafkaSource() {
    }

    public static KafkaSource<GenericRecord> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSource.<GenericRecord>builder()
                .setBootstrapServers(bootstrap)
                .setTopics(TOPIC)
                .setGroupId(GROUP_ID)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(
                        new DecimalAwareAvroDeserializationSchema(
                                Schemas.TICK_RAW, schemaRegistryUrl))
                .build();
    }
}
