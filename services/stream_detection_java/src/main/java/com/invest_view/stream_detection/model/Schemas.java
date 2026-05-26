package com.invest_view.stream_detection.model;

import org.apache.avro.Schema;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

/**
 * Loads and caches the Avro schemas that ship with this Flink job.
 * The {@code .avsc} files are copied byte-identical from
 * {@code schemas/} (project-root source of truth) into this module's
 * {@code src/main/resources/avro/} at build time.
 *
 * <p>Each schema is parsed exactly once at class-init time with its own
 * fresh {@link Schema.Parser} instance, so the parser's named-type
 * registry cannot collide between the two schemas (which live in
 * distinct namespaces anyway).
 */
public final class Schemas {

    private Schemas() {
    }

    public static final Schema TICK = load("/avro/stock-ticks.avsc");
    public static final Schema ALERT = load("/avro/stock-alerts.avsc");

    private static Schema load(String resourcePath) {
        try (InputStream in = Schemas.class.getResourceAsStream(resourcePath)) {
            Objects.requireNonNull(in, "Avro schema not found on classpath: " + resourcePath);
            String content = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            return new Schema.Parser().parse(content);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to load Avro schema: " + resourcePath, e);
        }
    }
}
