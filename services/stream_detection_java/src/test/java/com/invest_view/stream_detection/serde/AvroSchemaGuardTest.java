package com.invest_view.stream_detection.serde;

import io.confluent.kafka.schemaregistry.avro.AvroSchema;
import io.confluent.kafka.schemaregistry.client.MockSchemaRegistryClient;
import org.apache.avro.Schema;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AvroSchemaGuardTest {

    private static final String DUMMY_AVRO_SCHEMA =
            "{\"type\":\"record\",\"name\":\"X\",\"fields\":[]}";

    @Test
    void verifyAllPassesWhenAllSubjectsRegistered() throws Exception {
        MockSchemaRegistryClient mock = new MockSchemaRegistryClient();
        Schema dummy = new Schema.Parser().parse(DUMMY_AVRO_SCHEMA);
        mock.register("stock-ticks-value", new AvroSchema(dummy));
        mock.register("stock-alerts-value", new AvroSchema(dummy));

        AvroSchemaGuard guard = new AvroSchemaGuard(mock,
                List.of("stock-ticks-value", "stock-alerts-value"));

        guard.verifyAll();
    }

    @Test
    void verifyAllThrowsWhenSubjectMissing() throws Exception {
        MockSchemaRegistryClient mock = new MockSchemaRegistryClient();
        Schema dummy = new Schema.Parser().parse(DUMMY_AVRO_SCHEMA);
        mock.register("stock-ticks-value", new AvroSchema(dummy));
        // intentionally NOT registering stock-alerts-value

        AvroSchemaGuard guard = new AvroSchemaGuard(mock,
                List.of("stock-ticks-value", "stock-alerts-value"));

        IOException ex = assertThrows(IOException.class, guard::verifyAll);
        assertTrue(ex.getMessage().contains("stock-alerts-value"),
                "Exception message must name the missing subject; got: " + ex.getMessage());
    }
}
