package com.invest_view.stream_detection.source;

import com.investview.ticks.StockTick;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;

/**
 * Builds a Flink 1.18 {@link KafkaSource} that reads Avro-encoded
 * {@code stock-ticks} records via Confluent Schema Registry, emitting
 * {@link StockTick} SpecificRecord payloads.
 *
 * <p>The Avro-generated {@link StockTick} class registers
 * {@link org.apache.avro.Conversions.DecimalConversion} on its static
 * {@code MODEL$} field via {@code enableDecimalLogicalType=true} codegen
 * (see {@code pom.xml} avro-maven-plugin config). This ensures Flink's
 * {@code AvroSerializer.copy()} and the {@code SpecificDatumReader} both
 * honor BigDecimal end-to-end, sidestepping FLINK-11030.
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

    public static KafkaSource<StockTick> build(String bootstrap, String schemaRegistryUrl) {
        return KafkaSource.<StockTick>builder()
                .setBootstrapServers(bootstrap)
                .setTopics(TOPIC)
                .setGroupId(GROUP_ID)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(
                        ConfluentRegistryAvroDeserializationSchema.forSpecific(
                                StockTick.class, schemaRegistryUrl))
                .build();
    }
}
