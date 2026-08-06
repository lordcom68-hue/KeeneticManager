from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from core.connection_worker import RouterMonitor
from core.router_status_worker import RouterStatusWorker


class FakeResult:
    def __init__(self) -> None:
        self.success = True
        self.message = "Готово"
        self.connected_url = "http://router"
        self.connection_state = ""
        self.consecutive_failures = 0
        self.confirmed_offline = False
        self.transient_failure_suppressed = False
        self.last_result_message = ""
        self.failure_kind = ""


class FakeTransport:
    last_result_message = ""

    def record_monitor_state(self, *args) -> None:
        del args


class FakeKeeneticClient:
    call_count = 0
    call_event = threading.Event()
    call_lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        cls.call_count = 0
        cls.call_event = threading.Event()

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.connected = False
        self.base_url = "http://router"
        self.transport = FakeTransport()

    def _result(self) -> FakeResult:
        with self.call_lock:
            type(self).call_count += 1
            type(self).call_event.set()
        self.connected = True
        return FakeResult()

    def connect_to_router(self, **kwargs) -> FakeResult:
        del kwargs
        return self._result()

    def refresh(self) -> FakeResult:
        return self._result()

    def close(self) -> None:
        self.connected = False


class AuthenticationFailureClient(FakeKeeneticClient):
    def _result(self) -> FakeResult:
        result = super()._result()
        self.connected = False
        result.success = False
        result.message = "Неверный логин или пароль."
        result.failure_kind = "authentication"
        return result


class CountingStatusWorker(RouterStatusWorker):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.check_count = 0
        self.check_event = threading.Event()

    def _check_router(self, router: dict) -> tuple[bool, str]:
        del router
        self.check_count += 1
        self.check_event.set()
        return True, "http://router"


class RouterMonitorSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeKeeneticClient.reset()

    def stop_monitor(self, monitor: RouterMonitor) -> None:
        monitor.stop()
        self.assertTrue(monitor.wait(3000))

    def test_paused_monitor_only_runs_requested_initial_snapshot(self) -> None:
        with patch(
            "core.connection_worker.KeeneticClient",
            FakeKeeneticClient,
        ):
            monitor = RouterMonitor(
                {"id": 101, "status": False},
                interval_ms=1_000,
                paused=True,
            )
            monitor.start()

            try:
                self.assertFalse(
                    FakeKeeneticClient.call_event.wait(0.12)
                )
                monitor.request_refresh()
                self.assertTrue(
                    FakeKeeneticClient.call_event.wait(1.0)
                )
                time.sleep(0.18)
                self.assertEqual(
                    FakeKeeneticClient.call_count,
                    1,
                )
            finally:
                self.stop_monitor(monitor)

    def test_repeated_refresh_requests_are_coalesced(self) -> None:
        with patch(
            "core.connection_worker.KeeneticClient",
            FakeKeeneticClient,
        ):
            monitor = RouterMonitor(
                {"id": 102, "status": False},
                interval_ms=1_000,
                paused=True,
            )

            for _ in range(20):
                monitor.request_refresh()

            monitor.start()

            try:
                self.assertTrue(
                    FakeKeeneticClient.call_event.wait(1.0)
                )
                time.sleep(0.18)
                self.assertEqual(
                    FakeKeeneticClient.call_count,
                    1,
                )
            finally:
                self.stop_monitor(monitor)

    def test_unpaused_initial_request_does_not_duplicate_cycle(self) -> None:
        with patch(
            "core.connection_worker.KeeneticClient",
            FakeKeeneticClient,
        ):
            monitor = RouterMonitor(
                {"id": 103, "status": False},
                interval_ms=1_000,
                paused=False,
            )
            monitor.request_refresh()
            monitor.start()

            try:
                self.assertTrue(
                    FakeKeeneticClient.call_event.wait(1.0)
                )
                time.sleep(0.2)
                self.assertEqual(
                    FakeKeeneticClient.call_count,
                    1,
                )
            finally:
                self.stop_monitor(monitor)

    def test_unpausing_monitor_runs_one_immediate_cycle(self) -> None:
        with patch(
            "core.connection_worker.KeeneticClient",
            FakeKeeneticClient,
        ):
            monitor = RouterMonitor(
                {"id": 104, "status": False},
                interval_ms=1_000,
                paused=True,
            )
            monitor.start()

            try:
                self.assertFalse(
                    FakeKeeneticClient.call_event.wait(0.12)
                )
                monitor.set_paused(False)
                self.assertTrue(
                    FakeKeeneticClient.call_event.wait(1.0)
                )
                time.sleep(0.2)
                self.assertEqual(
                    FakeKeeneticClient.call_count,
                    1,
                )
            finally:
                self.stop_monitor(monitor)

    def test_confirmed_offline_uses_bounded_retry_backoff(self) -> None:
        monitor = RouterMonitor(
            {"id": 105, "status": False},
            interval_ms=2_000,
            paused=True,
        )
        offline = FakeResult()
        offline.success = False
        offline.confirmed_offline = True

        delays = [
            monitor.next_cycle_delay_ms(offline)
            for _index in range(6)
        ]

        self.assertEqual(
            delays,
            [5_000, 10_000, 20_000, 30_000, 30_000, 30_000],
        )

        online = FakeResult()
        self.assertEqual(
            monitor.next_cycle_delay_ms(online),
            2_000,
        )
        self.assertEqual(
            monitor.next_cycle_delay_ms(offline),
            5_000,
        )

    def test_authentication_failure_pauses_automatic_retries(self) -> None:
        AuthenticationFailureClient.reset()

        with patch(
            "core.connection_worker.KeeneticClient",
            AuthenticationFailureClient,
        ):
            monitor = RouterMonitor(
                {"id": 106, "status": False},
                interval_ms=100,
                paused=False,
            )
            monitor.start()

            try:
                self.assertTrue(
                    AuthenticationFailureClient.call_event.wait(1.0)
                )
                time.sleep(0.35)
                self.assertEqual(
                    AuthenticationFailureClient.call_count,
                    1,
                )

                AuthenticationFailureClient.call_event.clear()
                monitor.request_refresh()
                self.assertTrue(
                    AuthenticationFailureClient.call_event.wait(1.0)
                )
                time.sleep(0.3)
                self.assertEqual(
                    AuthenticationFailureClient.call_count,
                    2,
                )
            finally:
                self.stop_monitor(monitor)


class ReachabilitySchedulingTests(unittest.TestCase):
    def stop_worker(self, worker: RouterStatusWorker) -> None:
        worker.stop()
        self.assertTrue(worker.wait(3000))

    def test_paused_reachability_allows_one_manual_cycle(self) -> None:
        worker = CountingStatusWorker(
            interval_seconds=1.0,
            timeout_seconds=0.5,
            paused=True,
        )
        worker.set_routers([{"id": 1}])
        worker.start()

        try:
            self.assertFalse(worker.check_event.wait(0.12))
            worker.request_refresh()
            self.assertTrue(worker.check_event.wait(1.0))
            time.sleep(0.18)
            self.assertEqual(worker.check_count, 1)
            self.assertTrue(worker.is_paused())
        finally:
            self.stop_worker(worker)

    def test_unpausing_reachability_starts_immediate_cycle(self) -> None:
        worker = CountingStatusWorker(
            interval_seconds=1.0,
            timeout_seconds=0.5,
            paused=True,
        )
        worker.set_routers([{"id": 1}])
        worker.start()

        try:
            worker.set_paused(False)
            self.assertTrue(worker.check_event.wait(1.0))
            self.assertEqual(worker.check_count, 1)
            self.assertFalse(worker.is_paused())
        finally:
            self.stop_worker(worker)


if __name__ == "__main__":
    unittest.main()
