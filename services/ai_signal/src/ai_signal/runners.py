"""Runner registry for ``python -m ai_signal``.

Each pipeline component (processors, sinks, relay, market data job) exposes a
``Runner`` and registers it in ``RUNNERS`` with one line. ``all`` starts every
registered runner in the same process. Adding a component never changes the
dispatch code below.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass

from ai_signal.config import AiSignalSettings
from ai_signal.consumers.registry import CONSUMERS
from ai_signal.market_data.job import run_market_data
from ai_signal.outbox.relay import run_outbox_relay
from ai_signal.signals.retry import run_signal_retry

logger = logging.getLogger(__name__)

RunnerFn = Callable[[AiSignalSettings], Coroutine[object, object, None]]


@dataclass(frozen=True)
class Runner:
    name: str
    run: RunnerFn


RUNNERS: tuple[Runner, ...] = (
    Runner("outbox_relay", run_outbox_relay),
    Runner("market_data", run_market_data),
    Runner("signal_retry", run_signal_retry),
    *(Runner(spec.name, spec.run) for spec in CONSUMERS),
)


def runner_names(registry: tuple[Runner, ...] = RUNNERS) -> tuple[str, ...]:
    return ("all", *(runner.name for runner in registry))


def select(name: str, registry: tuple[Runner, ...] = RUNNERS) -> tuple[Runner, ...]:
    if name == "all":
        return registry
    by_name: Mapping[str, Runner] = {runner.name: runner for runner in registry}
    if name not in by_name:
        raise KeyError(f"unknown runner: {name}")
    return (by_name[name],)


async def run_selected(
    name: str,
    settings: AiSignalSettings,
    registry: tuple[Runner, ...] = RUNNERS,
) -> None:
    selected = select(name, registry)
    if not selected:
        logger.info("no runners registered for %s; nothing to start", name)
        return
    logger.info("starting runners: %s", ", ".join(r.name for r in selected))
    async with asyncio.TaskGroup() as group:
        for runner in selected:
            _ = group.create_task(runner.run(settings), name=runner.name)
