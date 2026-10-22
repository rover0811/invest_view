"""``python -m ai_signal.replay {run,export,prepare} --symbol 000660 --date 2026-10-20``."""
from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from ai_signal.replay import driver, runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_signal.replay",
        epilog=(
            "Run the runners with AI_SIGNAL_LLM_CACHE=postgres during a replay: "
            "identical prompts are then answered from gold.llm_cache, which the "
            "replay cleanup keeps."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "export", "prepare"):
        p = sub.add_parser(command)
        p.add_argument("--symbol", required=True)
        p.add_argument("--date", required=True, type=date.fromisoformat)
        if command in ("run", "export"):
            p.add_argument(
                "--pg-dsn", default=os.environ.get("REPLAY_PG_DSN", driver.DEFAULT_DSN)
            )
        if command == "run":
            p.add_argument(
                "--bootstrap",
                default=os.environ.get("REPLAY_BOOTSTRAP", "localhost:9092"),
            )
            p.add_argument(
                "--schema-registry",
                default=os.environ.get("REPLAY_SCHEMA_REGISTRY", "http://localhost:8081"),
            )
            p.add_argument("--max-llm-calls", type=int, default=400)
        if command == "export":
            p.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        driver.prepare_replay(args.symbol, args.date)
    elif args.command == "run":
        runner.run_replay(
            args.symbol,
            args.date,
            bootstrap=args.bootstrap,
            schema_registry=args.schema_registry,
            pg_dsn=args.pg_dsn,
            max_llm_calls=args.max_llm_calls,
        )
    else:
        out = args.out or driver.EXPORT_DIR / f"{args.symbol}-{args.date.isoformat()}.json"
        runner.export_replay(args.symbol, args.date, pg_dsn=args.pg_dsn, out_path=out)


if __name__ == "__main__":
    main()
