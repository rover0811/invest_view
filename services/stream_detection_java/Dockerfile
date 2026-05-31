# Final Dockerfile for stream-detection-java (T5.1).
# Builds on the official Flink 1.18.1 base with Scala 2.12 + Java 17.
# Copies the shaded fat JAR (produced by `mvn package`) plus a log4j config.
#
# Image tag convention:
#   :echoN  — Wave 2 GenericRecord attempts (deprecated, kept for traceability)
#   :spec1  — Wave 2.5 SpecificRecord pivot (current live)
#   :rules1 — Wave 5 final job with 3 rule pipelines (this image)

FROM flink:1.18.1-scala_2.12-java17

# Final fat JAR with 3 detection rules + checkpoint config
COPY target/stream-detection-java-*.jar /opt/flink/usrlib/stream-detection.jar

# Override default Flink console log4j config (INFO root, WARN for chatty clients)
COPY src/main/resources/log4j.properties /opt/flink/conf/log4j-console.properties
