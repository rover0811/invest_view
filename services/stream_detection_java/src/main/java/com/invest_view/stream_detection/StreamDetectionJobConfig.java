package com.invest_view.stream_detection;

import java.util.Objects;
import java.util.Optional;
import java.util.function.Function;

/**
 * Configuration for {@link StreamDetectionJob} sourced from environment variables.
 */
public record StreamDetectionJobConfig(
        String kafkaBootstrapServers,
        String schemaRegistryUrl,
        double priceAlertThreshold,
        double viProximityThreshold,
        int rsiPeriod,
        double rsiOversold,
        double rsiOverbought,
        int maShortPeriod,
        int maLongPeriod,
        int macdFastPeriod,
        int macdSlowPeriod,
        int macdSignalPeriod,
        String checkpointDir,
        int parallelism) {

    public static StreamDetectionJobConfig fromEnv() {
        return fromEnv(System::getenv);
    }

    public static StreamDetectionJobConfig fromEnv(Function<String, String> envReader) {
        String bootstrap = Objects.requireNonNull(
                envReader.apply("KAFKA_BOOTSTRAP_SERVERS"),
                "KAFKA_BOOTSTRAP_SERVERS env var is required (e.g. kafka:29092)");
        String schemaRegistryUrl = Objects.requireNonNull(
                envReader.apply("SCHEMA_REGISTRY_URL"),
                "SCHEMA_REGISTRY_URL env var is required (e.g. http://schema-registry:8081)");

        double priceAlertThreshold = Double.parseDouble(
                Optional.ofNullable(envReader.apply("PRICE_ALERT_THRESHOLD")).orElse("0.03"));
        double viProximityThreshold = Double.parseDouble(
                Optional.ofNullable(envReader.apply("VI_PROXIMITY_THRESHOLD")).orElse("0.01"));
        int rsiPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("RSI_PERIOD")).orElse("14"));
        double rsiOversold = Double.parseDouble(
                Optional.ofNullable(envReader.apply("RSI_OVERSOLD")).orElse("30"));
        double rsiOverbought = Double.parseDouble(
                Optional.ofNullable(envReader.apply("RSI_OVERBOUGHT")).orElse("70"));
        int maShortPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("MA_SHORT_PERIOD")).orElse("5"));
        int maLongPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("MA_LONG_PERIOD")).orElse("20"));
        int macdFastPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("MACD_FAST_PERIOD")).orElse("12"));
        int macdSlowPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("MACD_SLOW_PERIOD")).orElse("26"));
        int macdSignalPeriod = Integer.parseInt(
                Optional.ofNullable(envReader.apply("MACD_SIGNAL_PERIOD")).orElse("9"));
        String checkpointDir = Optional.ofNullable(envReader.apply("CHECKPOINT_DIR"))
                .orElse("file:///opt/flink/checkpoints");
        int parallelism = Integer.parseInt(
                Optional.ofNullable(envReader.apply("PARALLELISM")).orElse("1"));

        return new StreamDetectionJobConfig(
                bootstrap,
                schemaRegistryUrl,
                priceAlertThreshold,
                viProximityThreshold,
                rsiPeriod,
                rsiOversold,
                rsiOverbought,
                maShortPeriod,
                maLongPeriod,
                macdFastPeriod,
                macdSlowPeriod,
                macdSignalPeriod,
                checkpointDir,
                parallelism);
    }
}
