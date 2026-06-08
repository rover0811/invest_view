"""Dev-only launcher: runs the FastAPI app with a stubbed Kafka consumer.

Not for production. Lets the chat/agent + read APIs be exercised locally
without a running Kafka/Schema Registry. Delete after the demo.
"""
from __future__ import annotations

import uvicorn

from alert_service.api.app import create_app
from alert_service.config import AlertServiceConfig
from alert_service.container import Container


class _NoopConsumer:
    async def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True


def main() -> None:
    config = AlertServiceConfig()
    container = Container(config)
    container.alert_consumer = _NoopConsumer()  # type: ignore[assignment]
    app = create_app(container)
    uvicorn.run(app, host="0.0.0.0", port=config.http_port)


if __name__ == "__main__":
    main()
