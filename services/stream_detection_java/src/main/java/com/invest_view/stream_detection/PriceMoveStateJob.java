package com.invest_view.stream_detection;

import com.invest_view.events.PriceMove;
import com.invest_view.stream_detection.move.MoveRules;
import com.invest_view.stream_detection.move.PriceMoveStateFunction;
import com.invest_view.stream_detection.move.PriceStateMachine;
import com.invest_view.stream_detection.serde.AvroSchemaGuard;
import com.invest_view.stream_detection.sink.PriceMoveKafkaSink;
import com.invest_view.stream_detection.source.TickKafkaSource;
import com.invest_view.stream_detection.watermark.TickWatermarkStrategy;
import com.investview.ticks.StockTick;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.runtime.state.hashmap.HashMapStateBackend;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.CheckpointConfig.ExternalizedCheckpointCleanup;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

public final class PriceMoveStateJob {

    private static final Logger LOG = LoggerFactory.getLogger(PriceMoveStateJob.class);

    private PriceMoveStateJob() {
    }

    public static void main(String[] args) throws Exception {
        PriceMoveStateJobConfig config = PriceMoveStateJobConfig.fromEnv();
        LOG.info("PriceMoveStateJob starting with config={}", config);

        new AvroSchemaGuard(
                config.schemaRegistryUrl(),
                List.of("stock-ticks-value", PriceMoveKafkaSink.SUBJECT))
                .verifyAll();

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        env.setParallelism(config.parallelism());
        env.setStateBackend(new HashMapStateBackend());
        env.enableCheckpointing(60_000L, CheckpointingMode.EXACTLY_ONCE);
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(30_000L);
        env.getCheckpointConfig().setCheckpointTimeout(600_000L);
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);
        env.getCheckpointConfig().setExternalizedCheckpointCleanup(
                ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION);
        env.getCheckpointConfig().setCheckpointStorage(config.checkpointDir());

        KafkaSource<StockTick> source = TickKafkaSource.build(
                config.kafkaBootstrapServers(),
                config.schemaRegistryUrl(),
                config.consumerGroupId());

        DataStream<StockTick> ticks = env.fromSource(
                source,
                TickWatermarkStrategy.forTicks(),
                "stock-ticks-source")
                .uid("stock-ticks-source");

        DataStream<PriceMove> moves = ticks
                .keyBy(StockTick::getSymbol)
                .process(new PriceMoveStateFunction(
                        new PriceStateMachine(config.priceStateEnterPct(), config.priceStateExitPct()),
                        MoveRules.phase1()))
                .uid("price-move-state");

        moves.sinkTo(PriceMoveKafkaSink.build(
                config.kafkaBootstrapServers(),
                config.schemaRegistryUrl()))
                .uid("price-moves-sink");

        env.execute("PriceMoveStateJob");
    }
}
