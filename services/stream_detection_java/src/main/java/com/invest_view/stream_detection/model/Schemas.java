package com.invest_view.stream_detection.model;

import com.invest_view.events.StockAlert;
import com.investview.ticks.StockTick;
import org.apache.avro.Schema;

/**
 * Exposes the Avro schemas of the SpecificRecord types used by this job.
 *
 * <p>With the SpecificRecord pivot (T2.5), the canonical schemas live in
 * the generated {@code StockTick.SCHEMA$} / {@code StockAlert.SCHEMA$}
 * fields. This class is a thin compatibility shim so existing callers
 * (e.g. {@link com.invest_view.stream_detection.serde.AvroSchemaGuard}
 * subject pre-registration in {@code StreamDetectionJob}) keep working
 * with a single import.
 */
public final class Schemas {

    public static final Schema TICK = StockTick.getClassSchema();
    public static final Schema ALERT = StockAlert.getClassSchema();

    private Schemas() {
    }
}
