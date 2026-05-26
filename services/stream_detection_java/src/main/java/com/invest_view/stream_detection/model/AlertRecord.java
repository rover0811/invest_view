package com.invest_view.stream_detection.model;

import org.apache.avro.Schema;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericRecord;

import java.util.Map;

/**
 * Builder helper for {@code stock-alerts.avsc} {@link GenericRecord}
 * instances. Used by the rule builders in Task 3.1.
 *
 * <p>The supplied field map MUST contain all 11 required fields of the
 * schema. Enum fields ({@code market}, {@code alert_type}, {@code severity})
 * MUST be passed as their canonical string symbol — this helper converts
 * them into {@link GenericData.EnumSymbol} so that downstream
 * {@code ConfluentRegistryAvroSerializationSchema} accepts the record.
 *
 * <p>Nullable union fields like {@code source_tick_event_id}
 * ({@code ["null", "string"]}) are handled by unwrapping the non-null
 * branch before conversion.
 */
public final class AlertRecord {

    private AlertRecord() {
    }

    public static GenericRecord build(Schema alertSchema, Map<String, Object> fields) {
        GenericData.Record record = new GenericData.Record(alertSchema);
        for (Map.Entry<String, Object> entry : fields.entrySet()) {
            String name = entry.getKey();
            Object value = entry.getValue();
            Schema.Field field = alertSchema.getField(name);
            if (field == null) {
                throw new IllegalArgumentException("Unknown field in stock-alerts schema: " + name);
            }
            Object converted = convertForField(field, value);
            record.put(name, converted);
        }
        return record;
    }

    private static Object convertForField(Schema.Field field, Object value) {
        if (value == null) {
            return null;
        }
        Schema schema = field.schema();
        // Unwrap union [null, X] for nullable fields like source_tick_event_id
        if (schema.getType() == Schema.Type.UNION) {
            for (Schema branch : schema.getTypes()) {
                if (branch.getType() != Schema.Type.NULL) {
                    schema = branch;
                    break;
                }
            }
        }
        if (schema.getType() == Schema.Type.ENUM && value instanceof String s) {
            return new GenericData.EnumSymbol(schema, s);
        }
        return value;
    }
}
