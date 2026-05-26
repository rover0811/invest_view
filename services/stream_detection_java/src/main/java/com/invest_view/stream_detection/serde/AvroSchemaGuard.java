package com.invest_view.stream_detection.serde;

import io.confluent.kafka.schemaregistry.client.CachedSchemaRegistryClient;
import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient;
import io.confluent.kafka.schemaregistry.client.rest.exceptions.RestClientException;

import java.io.IOException;
import java.util.List;

/**
 * Startup pre-registration guard for Confluent Schema Registry subjects.
 *
 * <p>Verifies that every {@code expectedSubject} has at least one schema
 * version registered. If any subject is missing or SR is unreachable,
 * {@link #verifyAll()} throws {@link IOException} so the Flink job fails
 * fast at startup instead of attempting (and failing) lazy auto-registration
 * inside the data path.
 *
 * <p>This matches the Python {@code check_schemas_registered} guard in
 * {@code services/stream_detection/src/stream_detection/serde.py}.
 */
public final class AvroSchemaGuard {

    private final SchemaRegistryClient client;
    private final List<String> expectedSubjects;

    public AvroSchemaGuard(String schemaRegistryUrl, List<String> expectedSubjects) {
        this(new CachedSchemaRegistryClient(schemaRegistryUrl, 100), expectedSubjects);
    }

    AvroSchemaGuard(SchemaRegistryClient client, List<String> expectedSubjects) {
        this.client = client;
        this.expectedSubjects = List.copyOf(expectedSubjects);
    }

    public void verifyAll() throws IOException {
        for (String subject : expectedSubjects) {
            try {
                client.getLatestSchemaMetadata(subject);
            } catch (RestClientException e) {
                throw new IOException("Schema subject not registered: " + subject, e);
            } catch (IOException e) {
                throw new IOException("Schema Registry unreachable while checking subject: " + subject, e);
            }
        }
    }
}
