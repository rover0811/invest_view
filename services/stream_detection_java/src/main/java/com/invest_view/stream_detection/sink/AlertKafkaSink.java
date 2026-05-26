package com.invest_view.stream_detection.sink;

import com.invest_view.stream_detection.model.Schemas;
import org.apache.avro.generic.GenericRecord;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;

/**
 * Factory for the {@code stock-alerts} Kafka sink used by the
 * stream-detection Flink job.
 *
 * <p>The sink serializes Avro {@link GenericRecord} payloads using
 * {@link ConfluentRegistryAvroSerializationSchema#forGeneric(String, org.apache.avro.Schema, String)}
 * against the {@code stock-alerts-value} subject. The schema is
 * expected to be pre-registered in Schema Registry — this sink will
 * <strong>not</strong> auto-register (the
 * {@code ConfluentRegistryAvroSerializationSchema} constructor used
 * here does not enable auto-registration, matching the Python sink
 * pattern from {@code services/stream_detection/}).
 *
 * <p>Delivery semantics are explicitly set to
 * {@link DeliveryGuarantee#AT_LEAST_ONCE}. {@code EXACTLY_ONCE} would
 * require additional Kafka transactional configuration that is out of
 * scope for the v1 learning environment.
 *
 * <p>Networking note: in the kind ↔ docker-compose bridged environment
 * used by this project, callers should pass internal hostnames
 * ({@code kafka:29092} for Kafka, {@code http://schema-registry:8081}
 * for Schema Registry) — not {@code host.docker.internal}. See the
 * plan 19 notepad ({@code Task 0.4 CRITICAL DISCOVERY}) for the full
 * reasoning.
 */
public final class AlertKafkaSink {

    /** Kafka topic that carries serialized {@code stock-alerts} records. */
    public static final String TOPIC = "stock-alerts";

    /** Schema Registry subject for the {@code stock-alerts} value. */
    public static final String SUBJECT = "stock-alerts-value";

    private AlertKafkaSink() {
    }

    /**
     * Builds a {@link KafkaSink} that produces Avro-encoded
     * {@link GenericRecord} payloads to the {@code stock-alerts} topic.
     *
     * @param bootstrap          Kafka bootstrap servers (e.g. {@code kafka:29092}).
     * @param schemaRegistryUrl  Schema Registry base URL
     *                           (e.g. {@code http://schema-registry:8081}).
     * @return a configured {@link KafkaSink} with
     *         {@link DeliveryGuarantee#AT_LEAST_ONCE}.
     */
    public static KafkaSink<GenericRecord> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSink.<GenericRecord>builder()
                .setBootstrapServers(bootstrap)
                .setRecordSerializer(
                        KafkaRecordSerializationSchema.<GenericRecord>builder()
                                .setTopic(TOPIC)
                                .setValueSerializationSchema(
                                        ConfluentRegistryAvroSerializationSchema.forGeneric(
                                                SUBJECT,
                                                Schemas.ALERT,
                                                schemaRegistryUrl))
                                .build())
                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                .build();
    }
}
