"""Entrypoint: load config, build container, run uvicorn."""
from __future__ import annotations

import logging

import uvicorn

from alert_service.api.app import create_app
from alert_service.config import AlertServiceConfig
from alert_service.container import Container


def main() -> None:
    config = AlertServiceConfig()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    container = Container(config)
    app = create_app(container)
    uvicorn.run(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
