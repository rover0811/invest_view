package com.invest_view.stream_detection.sink;

import com.invest_view.events.StockAlert;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;

/**
 * Factory for the {@code stock-alerts} Kafka sink used by the
 * stream-detection Flink job. Emits Avro {@link StockAlert} SpecificRecord
 * payloads.
 *
 * <p>The schema is expected to be pre-registered in Schema Registry — this
 * sink will not auto-register.
 */
public final class AlertKafkaSink {

    public static final String TOPIC = "stock-alerts";

    public static final String SUBJECT = "stock-alerts-value";

    private AlertKafkaSink() {
    }

    public static KafkaSink<StockAlert> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSink.<StockAlert>builder()
                .setBootstrapServers(bootstrap)
                .setRecordSerializer(
                        KafkaRecordSerializationSchema.<StockAlert>builder()
                                .setTopic(TOPIC)
                                .setValueSerializationSchema(
                                        ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                StockAlert.class, SUBJECT, schemaRegistryUrl))
                                .build())
                .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                .setTransactionalIdPrefix("stock-alerts-sink")
                .setProperty("transaction.timeout.ms", "900000")
                .build();
    }
}
