package com.invest_view.stream_detection;

import com.invest_view.events.StockAlert;
import com.invest_view.events.StockPattern;
import com.invest_view.stream_detection.rule.CrossDetector;
import com.invest_view.stream_detection.rule.EmitPriceAlertWindow;
import com.invest_view.stream_detection.rule.MacdDetector;
import com.invest_view.stream_detection.rule.PriceAlertAggregator;
import com.invest_view.stream_detection.rule.RsiDetector;
import com.invest_view.stream_detection.rule.TradingHaltDetector;
import com.invest_view.stream_detection.rule.VIImminentFlatMap;
import com.invest_view.stream_detection.serde.AvroSchemaGuard;
import com.invest_view.stream_detection.sink.AlertKafkaSink;
import com.invest_view.stream_detection.sink.PatternKafkaSink;
import com.invest_view.stream_detection.source.TickKafkaSource;
import com.invest_view.stream_detection.watermark.TickWatermarkStrategy;
import com.investview.ticks.StockTick;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.runtime.state.hashmap.HashMapStateBackend;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.CheckpointConfig.ExternalizedCheckpointCleanup;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.SlidingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

public final class StreamDetectionJob {

    private static final Logger LOG = LoggerFactory.getLogger(StreamDetectionJob.class);

    private StreamDetectionJob() {
    }

    public static void main(String[] args) throws Exception {
        StreamDetectionJobConfig config = StreamDetectionJobConfig.fromEnv();
        LOG.info("StreamDetectionJob starting with config={}", config);

        new AvroSchemaGuard(
                config.schemaRegistryUrl(),
                List.of("stock-ticks-value", "stock-alerts-value", "stock-patterns-value"))
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
                config.schemaRegistryUrl());

        DataStream<StockTick> ticks = env.fromSource(
                source,
                TickWatermarkStrategy.forTicks(),
                "stock-ticks-source")
                .uid("stock-ticks-source");

        DataStream<StockAlert> priceAlerts = ticks
                .filter(PriceAlertAggregator::isEligible)
                .uid("price-alert-filter")
                .keyBy(StockTick::getSymbol)
                .window(SlidingEventTimeWindows.of(Time.seconds(300), Time.seconds(60)))
                .aggregate(new PriceAlertAggregator(), new EmitPriceAlertWindow(config.priceAlertThreshold()))
                .uid("price-alert-window");

        DataStream<StockAlert> viAlerts = ticks
                .flatMap(new VIImminentFlatMap(config.viProximityThreshold()))
                .uid("vi-imminent-flatmap");

        DataStream<StockAlert> haltAlerts = ticks
                .keyBy(StockTick::getSymbol)
                .process(new TradingHaltDetector())
                .uid("trading-halt-detector");

        DataStream<StockAlert> allAlerts = priceAlerts.union(viAlerts, haltAlerts);

        KafkaSink<StockAlert> sink = AlertKafkaSink.build(
                config.kafkaBootstrapServers(),
                config.schemaRegistryUrl());
        allAlerts.sinkTo(sink).uid("stock-alerts-sink");

        DataStream<StockPattern> crossPatterns = ticks
                .keyBy(StockTick::getSymbol)
                .process(new CrossDetector(config.maShortPeriod(), config.maLongPeriod()))
                .uid("ma-cross-detector");

        DataStream<StockPattern> rsiPatterns = ticks
                .keyBy(StockTick::getSymbol)
                .process(new RsiDetector(config.rsiPeriod(), config.rsiOversold(), config.rsiOverbought()))
                .uid("rsi-detector");

        DataStream<StockPattern> macdPatterns = ticks
                .keyBy(StockTick::getSymbol)
                .process(new MacdDetector(config.macdFastPeriod(), config.macdSlowPeriod(), config.macdSignalPeriod()))
                .uid("macd-detector");

        DataStream<StockPattern> patterns = crossPatterns.union(rsiPatterns, macdPatterns);

        patterns.sinkTo(PatternKafkaSink.build(
                config.kafkaBootstrapServers(),
                config.schemaRegistryUrl()))
                .uid("stock-patterns-sink");

        env.execute("StreamDetectionJob");
    }
}
