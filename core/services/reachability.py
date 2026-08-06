from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ReachabilityResult:
    """Итоговое состояние после подавления кратковременного сбоя."""

    online: bool
    address: str
    consecutive_failures: int
    transient_failure_suppressed: bool


@dataclass
class _RouterReachabilityState:
    online: bool = False
    last_good_address: str = ""
    consecutive_failures: int = 0


class ReachabilityTracker:
    """
    Подавляет одиночные ошибки фоновой проверки доступности.

    После подтверждённого успешного соединения роутер становится офлайн
    только после нескольких последовательных неудачных проверок. Это
    предотвращает мигание зелёного/красного индикатора при единичном
    тайм-ауте или кратковременном HTTP 503 от KeenDNS.
    """

    def __init__(self, failure_threshold: int = 3) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self._states: dict[int, _RouterReachabilityState] = {}
        self._lock = threading.RLock()

    def seed(
        self,
        *,
        router_id: int,
        online: bool,
        address: str = "",
    ) -> None:
        """Задаёт известное состояние из SQLite перед первым опросом."""
        with self._lock:
            self._states[int(router_id)] = _RouterReachabilityState(
                online=bool(online),
                last_good_address=(str(address or "") if online else ""),
                consecutive_failures=0,
            )

    def update(
        self,
        *,
        router_id: int,
        probe_online: bool,
        address: str,
    ) -> ReachabilityResult:
        with self._lock:
            state = self._states.setdefault(
                int(router_id),
                _RouterReachabilityState(),
            )

            if probe_online:
                state.online = True
                state.last_good_address = str(address or "")
                state.consecutive_failures = 0
                return ReachabilityResult(
                    online=True,
                    address=state.last_good_address,
                    consecutive_failures=0,
                    transient_failure_suppressed=False,
                )

            state.consecutive_failures += 1

            if (
                state.online
                and state.consecutive_failures < self.failure_threshold
            ):
                return ReachabilityResult(
                    online=True,
                    address=state.last_good_address,
                    consecutive_failures=state.consecutive_failures,
                    transient_failure_suppressed=True,
                )

            state.online = False
            return ReachabilityResult(
                online=False,
                address="",
                consecutive_failures=state.consecutive_failures,
                transient_failure_suppressed=False,
            )

    def retain_only(self, router_ids: set[int]) -> None:
        normalized = {int(router_id) for router_id in router_ids}
        with self._lock:
            self._states = {
                router_id: state
                for router_id, state in self._states.items()
                if router_id in normalized
            }

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
