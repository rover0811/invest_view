from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import date, timedelta

from ai_signal.market_data.config import load_market_data_settings
from ai_signal.market_data.job import RunWindow, execute, kst_today


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ai_signal.market_data",
        description="One-shot market data run: KIS closes, index weights, symbol betas.",
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, help="First beta date (KST)."
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, help="Last beta date. Default: today."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Also fetch history_days of closes before --start so OLS betas have history.",
    )
    parser.add_argument(
        "--skip-weights",
        action="store_true",
        help="Do not fetch the current index weight snapshot.",
    )
    return parser.parse_args(argv)


def build_window(
    args: argparse.Namespace, history_days: int, refresh_days: int
) -> RunWindow:
    end = args.end or kst_today()
    start = args.start or end - timedelta(days=refresh_days)
    if start > end:
        raise SystemExit("--start must not be after --end")
    fetch_start = start - timedelta(days=history_days) if args.backfill else start
    return RunWindow(
        beta_start=start,
        end=end,
        fetch_start=fetch_start,
        fetch_weights=not args.skip_weights,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ.get("AI_SIGNAL_DATABASE_URL")
    if not database_url:
        raise SystemExit("AI_SIGNAL_DATABASE_URL is required")
    log_level = os.environ.get("AI_SIGNAL_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    market_settings = load_market_data_settings()
    window = build_window(
        args, market_settings.history_days, market_settings.refresh_days
    )
    asyncio.run(execute(database_url, market_settings, window))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
