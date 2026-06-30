#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false, reportUnusedImport=false, reportMissingTypeStubs=false
"""Agent-eval-harness environment doctor.

Run on a collaborator's machine to verify the local dev environment is ready to
develop and run the agent evaluation harness (docs/design/19-agent-harness-eval-design.md).

It checks, in two tiers:

  LOCAL (no secrets)  — must pass to develop sensors / run the offline test gate.
  REMOTE (secrets)    — only needed to produce the actual baseline (Wave 1.5):
                        GCP Vertex AI (Gemini) auth + homelab read-only Postgres.

Nothing here deploys anything. The eval harness runs as a plain local Python
process that *calls out* to GCP (HTTPS) and homelab Postgres (TCP). REMOTE checks
are advisory: they print SKIP (not FAIL) when their env vars are absent, so the
script still exits 0 if only the local tier is set up.

Usage:
    uv run python scripts/eval_env_doctor.py            # from services/alert_service
    uv run python scripts/eval_env_doctor.py --strict   # treat REMOTE skips as failures

Exit code: 0 if all REQUIRED checks pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    OK = "OK"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    required: bool


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def _c(status: Status) -> str:
    color = {Status.OK: GREEN, Status.FAIL: RED, Status.SKIP: YELLOW}[status]
    return f"{color}{status.value:<4}{RESET}"


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return Check(
        "python >= 3.11",
        Status.OK if ok else Status.FAIL,
        f"{v.major}.{v.minor}.{v.micro}",
        required=True,
    )


def check_uv() -> Check:
    path = shutil.which("uv")
    if not path:
        return Check("uv installed", Status.FAIL, "not on PATH", required=True)
    try:
        out = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return Check("uv installed", Status.FAIL, str(exc), required=True)
    return Check("uv installed", Status.OK, out, required=True)


def check_imports() -> Check:
    missing: list[str] = []
    try:
        import strands  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        missing.append(f"strands ({exc})")
    try:
        from strands.models.gemini import GeminiModel  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        missing.append(f"strands.models.gemini ({exc})")
    try:
        from google import genai  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        missing.append(f"google-genai ({exc})")
    try:
        from alert_service.agent.market_analyst import (  # noqa: F401
            AGENT_TOOLS,
            build_market_analyst_agent,
        )
    except Exception as exc:  # noqa: BLE001
        missing.append(f"alert_service.agent ({exc})")
    if missing:
        return Check(
            "agent imports (no secrets)",
            Status.FAIL,
            "; ".join(missing) + "  -> run `uv sync`",
            required=True,
        )
    from alert_service.agent.market_analyst import AGENT_TOOLS

    return Check(
        "agent imports (no secrets)",
        Status.OK,
        f"strands + genai + builder OK, {len(AGENT_TOOLS)} tools",
        required=True,
    )


def check_docker() -> Check:
    if not shutil.which("docker"):
        return Check(
            "docker daemon (testcontainers)",
            Status.FAIL,
            "docker not on PATH",
            required=True,
        )
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=15
        )
    except Exception as exc:  # noqa: BLE001
        return Check("docker daemon (testcontainers)", Status.FAIL, str(exc), required=True)
    if proc.returncode != 0:
        return Check(
            "docker daemon (testcontainers)",
            Status.FAIL,
            "installed but not running -> start Docker Desktop",
            required=True,
        )
    return Check("docker daemon (testcontainers)", Status.OK, "running", required=True)


def check_gcp_adc(strict: bool) -> Check:
    if not shutil.which("gcloud"):
        return Check(
            "GCP ADC (Vertex/Gemini)",
            Status.FAIL if strict else Status.SKIP,
            "gcloud not installed",
            required=strict,
        )
    try:
        proc = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        return Check("GCP ADC (Vertex/Gemini)", Status.FAIL if strict else Status.SKIP, str(exc), required=strict)
    if proc.returncode != 0:
        return Check(
            "GCP ADC (Vertex/Gemini)",
            Status.FAIL if strict else Status.SKIP,
            "no ADC -> `gcloud auth application-default login`",
            required=strict,
        )
    project = os.environ.get("ALERT_SERVICE_GCP_PROJECT", "(ALERT_SERVICE_GCP_PROJECT unset)")
    location = os.environ.get("ALERT_SERVICE_GCP_LOCATION", "(default us-central1)")
    return Check(
        "GCP ADC (Vertex/Gemini)",
        Status.OK,
        f"token OK; project={project} location={location}",
        required=False,
    )


def check_db(strict: bool) -> Check:
    url = os.environ.get("ALERT_SERVICE_DATABASE_URL")
    if not url:
        return Check(
            "homelab DB (read-only)",
            Status.FAIL if strict else Status.SKIP,
            "ALERT_SERVICE_DATABASE_URL unset (needs Tailnet + read-only role)",
            required=strict,
        )

    async def _probe() -> tuple[bool, str]:
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
        except Exception as exc:  # noqa: BLE001
            return False, f"sqlalchemy import failed ({exc})"
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                who = (await conn.execute(text("SELECT current_user"))).scalar_one()
                # Probe one agent-readable table (allowlist) to confirm read grant.
                await conn.execute(text("SELECT 1 FROM serving.symbol_snapshot LIMIT 1"))
            return True, f"connected as {who}; serving.symbol_snapshot readable"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc).splitlines()[0]
        finally:
            await engine.dispose()

    try:
        ok, detail = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)
    return Check(
        "homelab DB (read-only)",
        Status.OK if ok else (Status.FAIL if strict else Status.SKIP),
        detail,
        required=strict,
    )


def check_offline_gate() -> Check:
    try:
        proc = subprocess.run(
            ["uv", "run", "pytest", "-m", "not qa", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ},
        )
    except Exception as exc:  # noqa: BLE001
        return Check("offline test gate collects", Status.FAIL, str(exc), required=True)
    tail = [ln for ln in proc.stdout.splitlines() if "collected" in ln or "error" in ln.lower()]
    detail = tail[-1].strip() if tail else "collected (see pytest output)"
    return Check(
        "offline test gate collects",
        Status.OK if proc.returncode == 0 else Status.FAIL,
        detail,
        required=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent-eval env doctor")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat REMOTE (GCP/DB) skips as failures",
    )
    parser.add_argument(
        "--skip-offline-gate",
        action="store_true",
        help="skip the pytest collect check (faster)",
    )
    args = parser.parse_args()

    print(f"\n{DIM}Agent eval-harness environment doctor{RESET}")
    print(f"{DIM}cwd: {os.getcwd()}{RESET}\n")

    print("LOCAL (no secrets — required to develop & run offline gate):")
    local = [check_python(), check_uv(), check_imports(), check_docker()]
    if not args.skip_offline_gate:
        local.append(check_offline_gate())
    for c in local:
        req = "" if c.required else f" {DIM}(optional){RESET}"
        print(f"  [{_c(c.status)}] {c.name}{req}  {DIM}{c.detail}{RESET}")

    print("\nREMOTE (secrets — only needed to produce the baseline, Wave 1.5):")
    remote = [check_gcp_adc(args.strict), check_db(args.strict)]
    for c in remote:
        req = "" if c.required else f" {DIM}(advisory){RESET}"
        print(f"  [{_c(c.status)}] {c.name}{req}  {DIM}{c.detail}{RESET}")

    all_checks = local + remote
    failed = [c for c in all_checks if c.required and c.status is Status.FAIL]

    print()
    if failed:
        print(f"{RED}DOCTOR: {len(failed)} required check(s) failed.{RESET}")
        for c in failed:
            print(f"  - {c.name}: {c.detail}")
        return 1

    skipped = [c for c in all_checks if c.status is Status.SKIP]
    if skipped:
        ready = f"{GREEN}DOCTOR: local environment ready.{RESET}"
        n_skip = f"{YELLOW}{len(skipped)} remote check(s) skipped{RESET}"
        hint = f"{DIM}(set GCP ADC + ALERT_SERVICE_DATABASE_URL to enable baseline runs).{RESET}"
        print(f"{ready} {n_skip} {hint}")
    else:
        print(f"{GREEN}DOCTOR: all checks passed — ready for baseline runs.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
