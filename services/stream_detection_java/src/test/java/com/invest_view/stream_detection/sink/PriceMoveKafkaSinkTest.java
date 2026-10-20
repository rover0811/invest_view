package com.invest_view.stream_detection.sink;

import com.invest_view.events.MoveSignalStatus;
import com.invest_view.events.MoveType;
import com.invest_view.events.PriceMove;
import com.invest_view.events.PriceState;
import org.apache.avro.Schema;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class PriceMoveKafkaSinkTest {

    @Test
    void buildReturnsNonNullSink() {
        assertNotNull(PriceMoveKafkaSink.build("localhost:9092", "http://localhost:8081"));
    }

    @Test
    void keyIsUtf8Symbol() {
        PriceMove move = PriceMove.newBuilder()
                .setMoveId("id")
                .setMoveType(MoveType.DAILY)
                .setSymbol("005930")
                .setWindowStart(Instant.EPOCH)
                .setWindowEnd(Instant.ofEpochMilli(1L))
                .setReturnPct(1.0)
                .setPriceState(PriceState.POSITIVE)
                .setSignalStatus(MoveSignalStatus.HIDDEN)
                .setVersion(1)
                .build();
        assertArrayEquals("005930".getBytes(StandardCharsets.UTF_8),
                PriceMoveKafkaSink.symbolKeySerializer().serialize(move));
    }

    @Test
    void bundledSchemaMatchesRepoContract() throws IOException {
        Schema repo = new Schema.Parser().parse(Files.readString(Path.of("../../schemas/price-moves.avsc")));
        Schema bundled = new Schema.Parser().parse(
                Files.readString(Path.of("src/main/resources/avro/price-moves.avsc")));
        assertEquals(repo, bundled);
        assertEquals(repo.getFullName(), PriceMove.getClassSchema().getFullName());
    }
}
