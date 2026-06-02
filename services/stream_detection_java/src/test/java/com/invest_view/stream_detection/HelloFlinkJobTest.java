package com.invest_view.stream_detection;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

public class HelloFlinkJobTest {

    private static final List<Long> RESULTS = Collections.synchronizedList(new ArrayList<>());

    @Test
    public void testFromSequenceProducesTen() throws Exception {
        RESULTS.clear();
        final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        DataStream<Long> source = env.fromSequence(1L, 10L);

        source.addSink(new SinkFunction<Long>() {
            @Override
            public void invoke(Long value, Context context) {
                RESULTS.add(value);
            }
        });

        env.execute("TestJob");

        assertEquals(10, RESULTS.size());
        List<Long> expected = List.of(1L, 2L, 3L, 4L, 5L, 6L, 7L, 8L, 9L, 10L);
        List<Long> actual = new ArrayList<>(RESULTS);
        Collections.sort(actual);
        assertEquals(expected, actual);
    }
}
