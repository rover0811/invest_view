package com.invest_view.stream_detection;

import com.invest_view.stream_detection.model.Schemas;
import com.invest_view.stream_detection.sink.AlertKafkaSink;
import com.invest_view.stream_detection.source.TickKafkaSource;
import org.apache.avro.Schema;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Collections;
import java.util.Objects;
import java.util.UUID;

/**
 * Wave 2.5 echo job: reads {@code stock-ticks}, maps every incoming tick to a
 * deterministic dummy {@code stock-alerts} record, and writes it back to Kafka.
 *
 * <p>This job intentionally contains <strong>no rule logic</strong>. Its only
 * purpose is to validate the end-to-end Avro/Schema-Registry/Flink/Kafka wire
 * format on kind before Wave 3 introduces the three real detection rules.
 *
 * <p>The {@code StreamDetectionJob} class wired up in Wave 4 will replace this
 * echo job once the per-rule pipelines (price alert, VI imminent, trading
 * halt) are in place. Until then this job is the smoke test used by
 * {@code k8s/flinkdeployment-echo.yaml}.
 *
 * <p>Required environment variables (read from {@link System#getenv()}):
 * <ul>
 *   <li>{@code KAFKA_BOOTSTRAP_SERVERS} — e.g. {@code kafka:29092}</li>
 *   <li>{@code SCHEMA_REGISTRY_URL} — e.g. {@code http://schema-registry:8081}</li>
 * </ul>
 * Both fail fast with a clear error message via
 * {@link Objects#requireNonNull(Object, String)} when missing.
 */
public final class EchoJob {

    private static final Logger LOG = LoggerFactory.getLogger(EchoJob.class);

    private EchoJob() {
    }

    public static void main(String[] args) throws Exception {
        LOG.info("EchoJob starting");

        final String bootstrap = Objects.requireNonNull(
                System.getenv("KAFKA_BOOTSTRAP_SERVERS"),
                "KAFKA_BOOTSTRAP_SERVERS env var is required (e.g. kafka:29092)");
        final String schemaRegistryUrl = Objects.requireNonNull(
                System.getenv("SCHEMA_REGISTRY_URL"),
                "SCHEMA_REGISTRY_URL env var is required (e.g. http://schema-registry:8081)");

        LOG.info("EchoJob configured with bootstrap={} schemaRegistryUrl={}",
                bootstrap, schemaRegistryUrl);

        final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        final KafkaSource<GenericRecord> source = TickKafkaSource.build(bootstrap, schemaRegistryUrl);
        final KafkaSink<GenericRecord> sink = AlertKafkaSink.build(bootstrap, schemaRegistryUrl);

        final DataStream<GenericRecord> ticks = env.fromSource(
                source,
                WatermarkStrategy.noWatermarks(),
                "tick-source");

        final DataStream<GenericRecord> alerts = ticks.map(new TickToDummyAlertMapper());

        alerts.sinkTo(sink);

        env.execute("EchoJob");
    }

    /**
     * Maps every tick {@link GenericRecord} to a deterministic dummy alert
     * {@link GenericRecord} built against {@link Schemas#ALERT}. Field values
     * are intentionally simple ({@code "echo"} for strings, {@code 0L} for
     * timestamps fallback, empty map for {@code trigger_values}) — this is a
     * wire-format echo, not a rule.
     *
     * <p>{@code alert_event_id} uses {@link UUID#randomUUID()} (random v4).
     * Cross-language deterministic UUID v5 is a Wave 3.1 concern, not this
     * job's.
     */
    private static final class TickToDummyAlertMapper implements MapFunction<GenericRecord, GenericRecord> {

        private static final long serialVersionUID = 1L;

        @Override
        public GenericRecord map(GenericRecord tick) {
            final long now = System.currentTimeMillis();

            final Schema marketEnum = Schemas.ALERT.getField("market").schema();
            final Schema alertTypeEnum = Schemas.ALERT.getField("alert_type").schema();
            final Schema severityEnum = Schemas.ALERT.getField("severity").schema();

            final GenericData.Record alert = new GenericData.Record(Schemas.ALERT);
            alert.put("alert_event_id", UUID.randomUUID().toString());
            alert.put("symbol", "echo");
            alert.put("market", new GenericData.EnumSymbol(marketEnum, "KRX"));
            alert.put("alert_type", new GenericData.EnumSymbol(alertTypeEnum, "PRICE_ALERT"));
            alert.put("severity", new GenericData.EnumSymbol(severityEnum, "INFO"));
            alert.put("observation_start_at", now);
            alert.put("observation_end_at", now);
            alert.put("triggered_at", now);
            alert.put("trigger_values", Collections.<CharSequence, CharSequence>emptyMap());
            alert.put("source_tick_event_id", null);
            alert.put("rule_name", "echo");
            return alert;
        }
    }
}
