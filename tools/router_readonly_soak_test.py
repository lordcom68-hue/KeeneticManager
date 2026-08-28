from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.database import Database  # noqa: E402
from core.keenetic_client import KeeneticClient  # noqa: E402
from core.services.runtime_paths import DIAGNOSTICS_HISTORY_DIR  # noqa: E402


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(
        0,
        min(
            len(ordered) - 1,
            math.ceil((len(ordered) - 1) * percentile),
        ),
    )
    return round(ordered[index], 3)


def run_readonly_soak(
    router: dict[str, Any],
    *,
    duration_seconds: float,
    interval_seconds: float,
    reconnect_every: int,
    maximum_cycles: int | None = None,
    client_factory: Callable[..., KeeneticClient] = KeeneticClient,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Exercise only connect/refresh calls and return an anonymized summary."""
    started_at = datetime.now().astimezone().isoformat()
    started = clock()
    deadline = started + max(0.0, float(duration_seconds))
    interval = max(0.0, float(interval_seconds))
    reconnect_period = max(0, int(reconnect_every))
    cycle_limit = (
        max(0, int(maximum_cycles))
        if maximum_cycles is not None
        else None
    )
    durations: list[float] = []
    failure_kinds: Counter[str] = Counter()
    successful_cycles = 0
    failed_cycles = 0
    reconnects = 0
    cycles = 0
    next_progress_at = started + 60.0

    client = client_factory(
        username=str(router.get("username") or ""),
        password=str(router.get("password") or ""),
        router_id=int(router["id"]),
    )
    try:
        while True:
            now = clock()
            if cycle_limit is not None:
                if cycles >= cycle_limit:
                    break
            elif cycles > 0 and now >= deadline:
                break

            cycle_started = clock()
            if not client.connected:
                result = client.connect_to_router(
                    local_ip=router.get("local_ip"),
                    keendns=router.get("keendns"),
                    connection_mode=str(
                        router.get("connection_mode") or "auto"
                    ),
                )
            else:
                result = client.refresh(force_full=False)
            durations.append(max(0.0, clock() - cycle_started))
            cycles += 1

            if bool(getattr(result, "success", False)):
                successful_cycles += 1
            else:
                failed_cycles += 1
                failure_kind = str(
                    getattr(result, "failure_kind", "") or "unknown"
                ).strip().lower()
                failure_kinds[failure_kind or "unknown"] += 1
                client.close()

            if (
                reconnect_period > 0
                and cycles % reconnect_period == 0
                and bool(getattr(result, "success", False))
            ):
                client.close()
                reconnects += 1

            now = clock()
            if progress is not None and now >= next_progress_at:
                progress(
                    {
                        "elapsed_seconds": round(now - started, 1),
                        "cycles": cycles,
                        "successful_cycles": successful_cycles,
                        "failed_cycles": failed_cycles,
                        "reconnects": reconnects,
                    }
                )
                next_progress_at = now + 60.0

            if cycle_limit is None:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                sleeper(min(interval, remaining))
            elif cycles < cycle_limit and interval > 0:
                sleeper(interval)
    finally:
        client.close()

    elapsed = max(0.0, clock() - started)
    success_ratio = successful_cycles / cycles if cycles else 0.0
    return {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "configuration_changed": False,
        "elapsed_seconds": round(elapsed, 3),
        "cycles": cycles,
        "successful_cycles": successful_cycles,
        "failed_cycles": failed_cycles,
        "success_ratio": round(success_ratio, 4),
        "local_session_reconnects": reconnects,
        "failure_kinds": dict(sorted(failure_kinds.items())),
        "latency_seconds": {
            "minimum": round(min(durations), 3) if durations else 0.0,
            "average": (
                round(sum(durations) / len(durations), 3)
                if durations
                else 0.0
            ),
            "p95": _percentile(durations, 0.95),
            "maximum": round(max(durations), 3) if durations else 0.0,
        },
        "contains_credentials": False,
        "contains_router_address": False,
        "contains_client_identifiers": False,
    }


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _select_router(database: Database, router_id: int | None) -> dict[str, Any]:
    routers = database.get_routers()
    if router_id is None:
        if len(routers) != 1:
            raise RuntimeError(
                "Для теста должен быть сохранён ровно один роутер "
                "или нужно указать --router-id."
            )
        router_id = int(routers[0]["id"])
    router = database.get_router(router_id, include_password=True)
    if router is None:
        raise RuntimeError("Указанный роутер не найден.")
    return router


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Keenetic connection and refresh soak test."
    )
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--reconnect-every", type=int, default=12)
    parser.add_argument("--router-id", type=int)
    parser.add_argument("--cycles", type=int)
    parser.add_argument(
        "--output",
        type=Path,
        default=DIAGNOSTICS_HISTORY_DIR / "readonly_soak_test.json",
    )
    arguments = parser.parse_args()

    try:
        router = _select_router(Database(), arguments.router_id)
        summary = run_readonly_soak(
            router,
            duration_seconds=max(0.0, arguments.minutes * 60.0),
            interval_seconds=arguments.interval_seconds,
            reconnect_every=arguments.reconnect_every,
            maximum_cycles=arguments.cycles,
            progress=lambda value: print(
                json.dumps(value, ensure_ascii=False),
                flush=True,
            ),
        )
        _write_summary(arguments.output, summary)
    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2

    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["success_ratio"] >= 0.9 else 3


if __name__ == "__main__":
    raise SystemExit(main())
