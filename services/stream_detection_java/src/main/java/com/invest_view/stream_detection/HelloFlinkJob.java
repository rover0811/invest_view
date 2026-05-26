package com.invest_view.stream_detection;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class HelloFlinkJob {

    private static final Logger LOG = LoggerFactory.getLogger(HelloFlinkJob.class);

    private HelloFlinkJob() {
    }

    public static void main(String[] args) throws Exception {
        LOG.info("HelloFlinkJob starting");

        final StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        DataStream<Long> source = env.fromSequence(1L, 10L);

        source.print();

        env.execute("HelloFlinkJob");
    }
}
