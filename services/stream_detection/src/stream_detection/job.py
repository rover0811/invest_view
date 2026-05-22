from __future__ import annotations

import logging

from .config import StreamDetectionSettings

logger = logging.getLogger(__name__)


def build_pipeline(settings: StreamDetectionSettings) -> None:
    raise NotImplementedError(
        "stream-detection pipeline skeleton. "
        "Sliding window + alert/pattern emitter will land in follow-up PRs."
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = StreamDetectionSettings()
    logger.info(
        "stream-detection starting source=%s alert=%s pattern=%s window=%ds slide=%ds",
        settings.source_topic,
        settings.alert_topic,
        settings.pattern_topic,
        settings.window_size_seconds,
        settings.window_slide_seconds,
    )
    build_pipeline(settings)


if __name__ == "__main__":
    main()
