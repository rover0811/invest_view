package com.invest_view.stream_detection.sink;

import com.invest_view.events.StockPattern;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;

/**
 * Factory for the {@code stock-patterns} Kafka sink used by the
 * stream-detection Flink job. Emits Avro {@link StockPattern} SpecificRecord
 * payloads.
 */
public final class PatternKafkaSink {

    public static final String TOPIC = "stock-patterns";

    public static final String SUBJECT = "stock-patterns-value";

    private PatternKafkaSink() {
    }

    public static KafkaSink<StockPattern> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSink.<StockPattern>builder()
                .setBootstrapServers(bootstrap)
                .setRecordSerializer(
                        KafkaRecordSerializationSchema.<StockPattern>builder()
                                .setTopic(TOPIC)
                                .setValueSerializationSchema(
                                        ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                StockPattern.class, SUBJECT, schemaRegistryUrl))
                                .build())
                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                .build();
    }
}
