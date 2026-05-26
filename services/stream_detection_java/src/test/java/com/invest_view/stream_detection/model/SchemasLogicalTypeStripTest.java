package com.invest_view.stream_detection.model;

import org.apache.avro.Schema;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * Verifies that {@link Schemas} loads each Avro reader schema with all
 * {@code logicalType} annotations stripped (FLINK-11030 workaround).
 *
 * <p>Flink 1.18's {@code ConfluentRegistryAvroDeserializationSchema} does not
 * register {@code Conversions.DecimalConversion} on its internal
 * {@code GenericDatumReader}. When the reader schema retains a
 * {@code logicalType=decimal} annotation on a {@code bytes} field, the
 * decoder mis-advances the cursor and the next {@code int} field throws
 * {@link org.apache.avro.InvalidNumberEncodingException}. Stripping the
 * annotation from the reader schema (the underlying primitive type stays
 * intact) avoids that code path entirely.
 */
class SchemasLogicalTypeStripTest {

    @Test
    void tickChangeRateLogicalTypeIsStripped() {
        Schema field = Schemas.TICK.getField("change_rate").schema();
        assertNull(field.getLogicalType(),
                "change_rate must not carry a logicalType on the reader side");
    }

    @Test
    void tickChangeRateTypeIsBytes() {
        Schema field = Schemas.TICK.getField("change_rate").schema();
        assertEquals(Schema.Type.BYTES, field.getType(),
                "change_rate underlying type must remain BYTES after stripping");
    }

    @Test
    void noNestedSubSchemaCarriesLogicalType() {
        assertAllLogicalTypesNull(Schemas.TICK);
        assertAllLogicalTypesNull(Schemas.ALERT);
    }

    @Test
    void fieldNamesArePreservedAgainstRawAvsc() throws IOException {
        Schema rawTick = parseRaw("/avro/stock-ticks.avsc");
        Schema rawAlert = parseRaw("/avro/stock-alerts.avsc");

        assertEquals(fieldNames(rawTick), fieldNames(Schemas.TICK),
                "TICK field name list must match raw avsc exactly");
        assertEquals(fieldNames(rawAlert), fieldNames(Schemas.ALERT),
                "ALERT field name list must match raw avsc exactly");

        assertEquals(rawTick.getFields().size(), Schemas.TICK.getFields().size(),
                "TICK field count must match raw avsc");
        assertEquals(rawAlert.getFields().size(), Schemas.ALERT.getFields().size(),
                "ALERT field count must match raw avsc");
    }

    private static Schema parseRaw(String resourcePath) throws IOException {
        try (InputStream in = Schemas.class.getResourceAsStream(resourcePath)) {
            assertNotNull(in, "Test resource not on classpath: " + resourcePath);
            String content = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            return new Schema.Parser().parse(content);
        }
    }

    private static List<String> fieldNames(Schema record) {
        List<String> names = new ArrayList<>();
        for (Schema.Field f : record.getFields()) {
            names.add(f.name());
        }
        return names;
    }

    private static void assertAllLogicalTypesNull(Schema root) {
        Set<Schema> visited = Collections.newSetFromMap(new IdentityHashMap<>());
        walk(root, visited);
    }

    private static void walk(Schema s, Set<Schema> visited) {
        if (s == null || !visited.add(s)) {
            return;
        }
        assertNull(s.getLogicalType(),
                "Sub-schema at type=" + s.getType() + " (name=" + safeName(s)
                        + ") still has logicalType=" + s.getLogicalType());
        switch (s.getType()) {
            case RECORD:
                for (Schema.Field f : s.getFields()) {
                    walk(f.schema(), visited);
                }
                break;
            case UNION:
                for (Schema branch : s.getTypes()) {
                    walk(branch, visited);
                }
                break;
            case ARRAY:
                walk(s.getElementType(), visited);
                break;
            case MAP:
                walk(s.getValueType(), visited);
                break;
            default:
                break;
        }
    }

    private static String safeName(Schema s) {
        try {
            return s.getName();
        } catch (RuntimeException e) {
            return "<anonymous>";
        }
    }
}
