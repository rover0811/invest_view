package com.invest_view.stream_detection;

import java.util.Objects;
import java.util.Optional;
import java.util.function.Function;

/**
 * Configuration for {@link PriceMoveStateJob} sourced from environment variables.
 */
public record PriceMoveStateJobConfig(
        String kafkaBootstrapServers,
        String schemaRegistryUrl,
        String consumerGroupId,
        double priceStateEnterPct,
        double priceStateExitPct,
        String checkpointDir,
        int parallelism) {

    public static PriceMoveStateJobConfig fromEnv() {
        return fromEnv(System::getenv);
    }

    public static PriceMoveStateJobConfig fromEnv(Function<String, String> envReader) {
        String bootstrap = Objects.requireNonNull(
                envReader.apply("KAFKA_BOOTSTRAP_SERVERS"),
                "KAFKA_BOOTSTRAP_SERVERS env var is required (e.g. kafka:29092)");
        String schemaRegistryUrl = Objects.requireNonNull(
                envReader.apply("SCHEMA_REGISTRY_URL"),
                "SCHEMA_REGISTRY_URL env var is required (e.g. http://schema-registry:8081)");
        String consumerGroupId = Optional.ofNullable(envReader.apply("CONSUMER_GROUP_ID"))
                .orElse("price-move-state-java");
        double enterPct = Double.parseDouble(
                Optional.ofNullable(envReader.apply("PRICE_STATE_ENTER_PCT")).orElse("1.0"));
        double exitPct = Double.parseDouble(
                Optional.ofNullable(envReader.apply("PRICE_STATE_EXIT_PCT")).orElse("0.5"));
        String checkpointDir = Optional.ofNullable(envReader.apply("CHECKPOINT_DIR"))
                .orElse("file:///opt/flink/checkpoints/price-move-state");
        int parallelism = Integer.parseInt(
                Optional.ofNullable(envReader.apply("PARALLELISM")).orElse("1"));

        return new PriceMoveStateJobConfig(
                bootstrap,
                schemaRegistryUrl,
                consumerGroupId,
                enterPct,
                exitPct,
                checkpointDir,
                parallelism);
    }
}
