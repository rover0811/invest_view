package com.invest_view.stream_detection.source;

import io.confluent.kafka.schemaregistry.client.CachedSchemaRegistryClient;
import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient;
import org.apache.avro.Conversions;
import org.apache.avro.Schema;
import org.apache.avro.data.TimeConversions;
import org.apache.avro.generic.GenericData;
import org.apache.avro.generic.GenericDatumReader;
import org.apache.avro.generic.GenericRecord;
import org.apache.avro.io.Decoder;
import org.apache.avro.io.DecoderFactory;
import org.apache.flink.api.common.serialization.DeserializationSchema;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.formats.avro.typeutils.GenericRecordAvroTypeInfo;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Objects;

/**
 * Confluent Schema Registry Avro deserializer that pre-registers
 * {@link Conversions.DecimalConversion} and
 * {@link TimeConversions.TimestampMillisConversion} on the internal
 * {@link GenericData} so that Avro {@code bytes+logicalType=decimal}
 * fields (and {@code long+timestamp-millis} fields) decode correctly.
 *
 * <p><b>Why this exists.</b> Flink 1.18 / 1.19 / 1.20's stock
 * {@code ConfluentRegistryAvroDeserializationSchema.forGeneric(...)}
 * constructs a {@link GenericDatumReader} backed by a fresh
 * {@code GenericData} that has <em>no</em> logical-type conversions
 * registered. When the writer schema fetched from Schema Registry
 * declares a {@code bytes} field as {@code logicalType=decimal} (which
 * our {@code stock-ticks} schema does for 7 fields — {@code change_rate},
 * {@code vwap}, {@code trade_strength}, {@code buy_ratio},
 * {@code prev_day_volume_rate}, {@code volume_turnover},
 * {@code prev_same_hour_volume_rate}), the decoder mis-advances the
 * binary cursor at the bytes-length zig-zag prefix and the next
 * {@code int} field throws {@code InvalidNumberEncodingException}. This
 * is tracked as <a href="https://issues.apache.org/jira/browse/FLINK-11030">FLINK-11030</a>.
 * The upstream fix targets Flink 2.1.0 (PR #27770); until then, we
 * implement the same effective behaviour by managing the
 * {@link GenericDatumReader} initialisation ourselves.
 *
 * <p>This class is a from-scratch composition rather than a subclass of
 * {@code ConfluentRegistryAvroDeserializationSchema} because the latter's
 * constructors and {@code checkAvroInitialized} method are package-private
 * and cannot be overridden from outside the
 * {@code org.apache.flink.formats.avro} packages.
 */
public final class DecimalAwareAvroDeserializationSchema
        implements DeserializationSchema<GenericRecord> {

    private static final long serialVersionUID = 1L;
    private static final Logger LOG = LoggerFactory.getLogger(DecimalAwareAvroDeserializationSchema.class);
    private transient boolean diagLogged;

    /** Schema in its toString() form so the instance is serializable. */
    private final String readerSchemaString;

    /** Schema Registry URL, used when the schema-coder factory is lazy-built on the TaskManager. */
    private final String schemaRegistryUrl;

    /** Test-time injection point. {@code null} in production paths. */
    private final transient SchemaRegistryClient injectedClient;

    // --- Lazy fields rebuilt on each TaskManager after deserialization ----
    private transient Schema readerSchema;
    private transient GenericData genericData;
    private transient GenericDatumReader<GenericRecord> datumReader;
    private transient Decoder decoder;
    private transient SchemaRegistryClient schemaRegistryClient;

    public DecimalAwareAvroDeserializationSchema(Schema readerSchema, String schemaRegistryUrl) {
        this(readerSchema, schemaRegistryUrl, null);
    }

    /**
     * Package-private test constructor that accepts a pre-built
     * {@link SchemaRegistryClient} (typically a
     * {@code MockSchemaRegistryClient}) so unit tests do not require a
     * live Schema Registry.
     */
    DecimalAwareAvroDeserializationSchema(
            Schema readerSchema,
            String schemaRegistryUrl,
            SchemaRegistryClient injectedClient) {
        this.readerSchemaString = Objects.requireNonNull(readerSchema, "readerSchema").toString();
        this.schemaRegistryUrl = schemaRegistryUrl;
        this.injectedClient = injectedClient;
    }

    @Override
    public GenericRecord deserialize(byte[] message) throws IOException {
        if (message == null) {
            return null;
        }
        checkInitialized();
        if (message.length < 5 || message[0] != 0x00) {
            throw new IOException("Not a Confluent-encoded Avro message (bad magic byte)");
        }
        final int schemaId = ((message[1] & 0xff) << 24)
                | ((message[2] & 0xff) << 16)
                | ((message[3] & 0xff) << 8)
                | (message[4] & 0xff);
        final Schema writerSchema;
        try {
            writerSchema = schemaRegistryClient.getById(schemaId);
        } catch (Exception e) {
            throw new IOException("Failed to fetch schema id=" + schemaId, e);
        }
        decoder = DecoderFactory.get().binaryDecoder(
                message, 5, message.length - 5, (org.apache.avro.io.BinaryDecoder) decoder);
        GenericDatumReader<GenericRecord> reader =
                new GenericDatumReader<>(writerSchema, readerSchema, genericData);
        GenericRecord result = reader.read(null, decoder);
        if (!diagLogged) {
            diagLogged = true;
            LOG.warn("DIAG decode SUCCESS symbol={} change_rate={}",
                    result.get("symbol"), result.get("change_rate"));
        }
        return result;
    }

    @Override
    public boolean isEndOfStream(GenericRecord nextElement) {
        return false;
    }

    @Override
    public TypeInformation<GenericRecord> getProducedType() {
        if (readerSchema == null) {
            readerSchema = new Schema.Parser().parse(readerSchemaString);
        }
        return new GenericRecordAvroTypeInfo(readerSchema);
    }

    private void checkInitialized() {
        if (datumReader != null) {
            return;
        }
        final ClassLoader cl = Thread.currentThread().getContextClassLoader();
        this.readerSchema = new Schema.Parser().parse(readerSchemaString);
        this.genericData = new GenericData(cl);
        this.genericData.addLogicalTypeConversion(new Conversions.DecimalConversion());
        this.genericData.addLogicalTypeConversion(new TimeConversions.TimestampMillisConversion());
        this.datumReader = new GenericDatumReader<>(null, this.readerSchema, this.genericData);
        this.schemaRegistryClient =
                (injectedClient != null)
                        ? injectedClient
                        : new CachedSchemaRegistryClient(schemaRegistryUrl, 1000);
    }
}
