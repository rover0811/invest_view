package com.invest_view.stream_detection;

import com.invest_view.events.AlertType;
import com.invest_view.events.Market;
import com.invest_view.events.Severity;
import com.invest_view.events.StockAlert;
import com.invest_view.stream_detection.sink.AlertKafkaSink;
import com.invest_view.stream_detection.source.TickKafkaSource;
import com.investview.ticks.StockTick;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.util.Collections;
import java.util.Objects;
import java.util.UUID;

/**
 * Wave 2.5 echo job: reads {@code stock-ticks} as Avro
 * {@link StockTick} SpecificRecords, maps every incoming tick to a
 * deterministic dummy {@link StockAlert}, and writes it back to Kafka.
 *
 * <p>This job intentionally contains no rule logic. Its purpose is to
 * validate the end-to-end Avro/Schema-Registry/Flink/Kafka wire format
 * on kind before Wave 3 introduces the three real detection rules.
 *
 * <p>SpecificRecord routing is used (rather than GenericRecord) to
 * sidestep FLINK-11030 — the generated classes carry DecimalConversion
 * on their {@code MODEL$} static field, so Flink's
 * {@code AvroSerializer.copy()} path handles BigDecimal correctly.
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

        final KafkaSource<StockTick> source = TickKafkaSource.build(bootstrap, schemaRegistryUrl);
        final KafkaSink<StockAlert> sink = AlertKafkaSink.build(bootstrap, schemaRegistryUrl);

        final DataStream<StockTick> ticks = env.fromSource(
                source,
                WatermarkStrategy.noWatermarks(),
                "tick-source");

        final DataStream<StockAlert> alerts = ticks.map(new TickToDummyAlertMapper());

        alerts.sinkTo(sink);

        env.execute("EchoJob");
    }

    /**
     * Maps every {@link StockTick} to a deterministic dummy {@link StockAlert}.
     * Field values are intentionally simple ({@code "echo"} for strings,
     * {@code now} for timestamps, empty map for {@code trigger_values}).
     *
     * <p>{@code alert_event_id} uses {@link UUID#randomUUID()} (random v4).
     * Cross-language deterministic UUID v5 is a Wave 3.1 concern.
     */
    private static final class TickToDummyAlertMapper implements MapFunction<StockTick, StockAlert> {

        private static final long serialVersionUID = 1L;

        @Override
        public StockAlert map(StockTick tick) {
            final long now = System.currentTimeMillis();
            final Instant timestamp = Instant.ofEpochMilli(now);
            return StockAlert.newBuilder()
                    .setAlertEventId(UUID.randomUUID().toString())
                    .setSymbol("echo")
                    .setMarket(Market.KRX)
                    .setAlertType(AlertType.PRICE_ALERT)
                    .setSeverity(Severity.INFO)
                    .setObservationStartAt(timestamp)
                    .setObservationEndAt(timestamp)
                    .setTriggeredAt(timestamp)
                    .setTriggerValues(Collections.emptyMap())
                    .setSourceTickEventId(null)
                    .setRuleName("echo")
                    .build();
        }
    }
}
