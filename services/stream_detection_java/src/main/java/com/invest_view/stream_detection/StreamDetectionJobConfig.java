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
        String checkpointDir = Optional.ofNullable(envReader.apply("CHECKPOINT_DIR"))
                .orElse("file:///opt/flink/checkpoints");
        int parallelism = Integer.parseInt(
                Optional.ofNullable(envReader.apply("PARALLELISM")).orElse("1"));

        return new StreamDetectionJobConfig(
                bootstrap,
                schemaRegistryUrl,
                priceAlertThreshold,
                viProximityThreshold,
                checkpointDir,
                parallelism);
    }
}
