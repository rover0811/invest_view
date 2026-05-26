package com.invest_view.stream_detection.model;

import org.apache.avro.Schema;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
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
 *
 * <p><b>FLINK-11030 workaround.</b> Flink 1.18's
 * {@code ConfluentRegistryAvroDeserializationSchema} does not register
 * {@code Conversions.DecimalConversion} on the {@code GenericDatumReader}
 * it constructs internally. When the reader schema retains a
 * {@code logicalType=decimal} annotation on a {@code bytes} field, the
 * decoder mis-advances the cursor and the next {@code int} field throws
 * {@link org.apache.avro.InvalidNumberEncodingException}. The fix is to
 * strip every {@code logicalType} annotation from the reader schema and
 * read the underlying primitive type ({@code bytes}, {@code long}, ...)
 * as-is. Schema resolution against the writer-side schema stored in
 * Schema Registry (which keeps its logical-type annotations) is
 * unaffected: the writer's annotations are simply ignored by the reader.
 */
public final class Schemas {

    private Schemas() {
    }

    public static final Schema TICK = stripLogicalTypes(load("/avro/stock-ticks.avsc"));
    public static final Schema ALERT = stripLogicalTypes(load("/avro/stock-alerts.avsc"));

    /** Un-stripped TICK schema — preserves logicalType=decimal annotations. For tests + future Decimal-aware paths. */
    public static final Schema TICK_RAW = load("/avro/stock-ticks.avsc");
    /** Un-stripped ALERT schema — preserves timestamp-millis logicalTypes. Symmetric companion to TICK_RAW. */
    public static final Schema ALERT_RAW = load("/avro/stock-alerts.avsc");

    private static Schema load(String resourcePath) {
        try (InputStream in = Schemas.class.getResourceAsStream(resourcePath)) {
            Objects.requireNonNull(in, "Avro schema not found on classpath: " + resourcePath);
            String content = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            return new Schema.Parser().parse(content);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to load Avro schema: " + resourcePath, e);
        }
    }

    private static Schema stripLogicalTypes(Schema schema) {
        return stripLogicalTypes(schema, new IdentityHashMap<>());
    }

    private static Schema stripLogicalTypes(Schema schema, Map<Schema, Schema> memo) {
        Schema cached = memo.get(schema);
        if (cached != null) {
            return cached;
        }
        switch (schema.getType()) {
            case RECORD:
                return stripRecord(schema, memo);
            case UNION:
                return stripUnion(schema, memo);
            case ARRAY:
                return stripArray(schema, memo);
            case MAP:
                return stripMap(schema, memo);
            case ENUM:
            case FIXED:
                memo.put(schema, schema);
                return schema;
            default:
                return stripPrimitive(schema, memo);
        }
    }

    private static Schema stripRecord(Schema schema, Map<Schema, Schema> memo) {
        Schema rebuilt = Schema.createRecord(
                schema.getName(),
                schema.getDoc(),
                schema.getNamespace(),
                schema.isError());
        for (String alias : schema.getAliases()) {
            rebuilt.addAlias(alias);
        }
        copyProps(schema, rebuilt);
        memo.put(schema, rebuilt);
        List<Schema.Field> newFields = new ArrayList<>(schema.getFields().size());
        for (Schema.Field oldField : schema.getFields()) {
            Schema newFieldSchema = stripLogicalTypes(oldField.schema(), memo);
            Schema.Field newField = new Schema.Field(
                    oldField.name(),
                    newFieldSchema,
                    oldField.doc(),
                    oldField.defaultVal(),
                    oldField.order());
            for (String fAlias : oldField.aliases()) {
                newField.addAlias(fAlias);
            }
            copyFieldProps(oldField, newField);
            newFields.add(newField);
        }
        rebuilt.setFields(newFields);
        return rebuilt;
    }

    private static Schema stripUnion(Schema schema, Map<Schema, Schema> memo) {
        List<Schema> branches = new ArrayList<>(schema.getTypes().size());
        for (Schema branch : schema.getTypes()) {
            branches.add(stripLogicalTypes(branch, memo));
        }
        Schema rebuilt = Schema.createUnion(branches);
        memo.put(schema, rebuilt);
        return rebuilt;
    }

    private static Schema stripArray(Schema schema, Map<Schema, Schema> memo) {
        Schema rebuilt = Schema.createArray(stripLogicalTypes(schema.getElementType(), memo));
        copyProps(schema, rebuilt);
        memo.put(schema, rebuilt);
        return rebuilt;
    }

    private static Schema stripMap(Schema schema, Map<Schema, Schema> memo) {
        Schema rebuilt = Schema.createMap(stripLogicalTypes(schema.getValueType(), memo));
        copyProps(schema, rebuilt);
        memo.put(schema, rebuilt);
        return rebuilt;
    }

    private static Schema stripPrimitive(Schema schema, Map<Schema, Schema> memo) {
        if (schema.getLogicalType() == null && schema.getObjectProps().isEmpty()) {
            memo.put(schema, schema);
            return schema;
        }
        Schema rebuilt = Schema.create(schema.getType());
        memo.put(schema, rebuilt);
        return rebuilt;
    }

    private static void copyProps(Schema from, Schema to) {
        for (Map.Entry<String, Object> e : from.getObjectProps().entrySet()) {
            if ("logicalType".equals(e.getKey())
                    || "precision".equals(e.getKey())
                    || "scale".equals(e.getKey())) {
                continue;
            }
            to.addProp(e.getKey(), e.getValue());
        }
    }

    private static void copyFieldProps(Schema.Field from, Schema.Field to) {
        for (Map.Entry<String, Object> e : from.getObjectProps().entrySet()) {
            to.addProp(e.getKey(), e.getValue());
        }
    }
}
