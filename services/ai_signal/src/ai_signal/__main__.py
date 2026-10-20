from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence

from ai_signal.config import load_settings
from ai_signal.runners import run_selected, runner_names

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI signal service components.")
    parser.add_argument("runner", choices=runner_names())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("AI signal service starting runner=%s", args.runner)
    asyncio.run(run_selected(args.runner, settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
