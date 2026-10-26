package com.invest_view.stream_detection.sink;

import com.invest_view.events.PriceMove;
import org.apache.flink.api.common.serialization.SerializationSchema;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;

import java.nio.charset.StandardCharsets;

/**
 * Factory for the {@code price-moves} Kafka sink. Avro {@link PriceMove}
 * values, UTF-8 symbol keys so every move of a symbol lands in one partition.
 */
public final class PriceMoveKafkaSink {

    public static final String TOPIC = "price-moves";

    public static final String SUBJECT = "price-moves-value";

    private PriceMoveKafkaSink() {
    }

    public static SerializationSchema<PriceMove> symbolKeySerializer() {
        return move -> move.getSymbol().getBytes(StandardCharsets.UTF_8);
    }

    public static KafkaSink<PriceMove> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSink.<PriceMove>builder()
                .setBootstrapServers(bootstrap)
                .setRecordSerializer(
                        KafkaRecordSerializationSchema.<PriceMove>builder()
                                .setTopic(TOPIC)
                                .setKeySerializationSchema(symbolKeySerializer())
                                .setValueSerializationSchema(
                                        ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                PriceMove.class, SUBJECT, schemaRegistryUrl))
                                .build())
                .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                .setTransactionalIdPrefix("price-moves-sink")
                .setProperty("transaction.timeout.ms", "900000")
                .build();
    }
}
