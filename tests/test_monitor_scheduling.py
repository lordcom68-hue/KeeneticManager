from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt

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
        self.log_event = threading.Event()
        self.system_log_service = SimpleNamespace(
            read=self._read_log,
        )

    def _read_log(self, _limit: int):
        self.log_event.set()
        return True, "Готово", []

    def _result(self) -> FakeResult:
        with self.call_lock:
            type(self).call_count += 1
            type(self).call_event.set()
        self.connected = True
        return FakeResult()

    def connect_to_router(self, **kwargs) -> FakeResult:
        del kwargs
        return self._result()

    def refresh(self, *, force_full: bool = False) -> FakeResult:
        del force_full
        return self._result()

    def close(self) -> None:
        self.connected = False

    def abort_pending_io(self) -> None:
        self.close()


class AuthenticationFailureClient(FakeKeeneticClient):
    def _result(self) -> FakeResult:
        result = super()._result()
        self.connected = False
        result.success = False
        result.message = "Неверный логин или пароль."
        result.failure_kind = "authentication"
        return result


class ExplodingClient(FakeKeeneticClient):
    def connect_to_router(self, **kwargs) -> FakeResult:
        del kwargs
        raise RuntimeError("synthetic refresh failure")


class ExplodingConstructorClient(FakeKeeneticClient):
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("synthetic initialization failure")


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


class ExplodingStatusWorker(RouterStatusWorker):
    def _check_router(self, router: dict) -> tuple[bool, str]:
        del router
        raise RuntimeError("synthetic probe failure")


class RouterMonitorSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeKeeneticClient.reset()

    def test_expected_handoff_closes_old_session_and_uses_backoff(self) -> None:
        monitor = RouterMonitor(
            {"id": 120, "status": True},
            interval_ms=1_000,
        )
        client = FakeKeeneticClient()
        client.connected = True

        monitor.begin_expected_handoff(client, "keendns")

        self.assertFalse(client.connected)
        self.assertEqual(monitor.expected_handoff_mode(), "keendns")
        self.assertEqual(
            monitor.next_cycle_delay_ms(FakeResult()),
            monitor.HANDOFF_RETRY_DELAY_MS,
        )
        monitor.complete_expected_handoff()
        self.assertEqual(monitor.expected_handoff_mode(), "")

    def test_stop_aborts_active_http_session(self) -> None:
        monitor = RouterMonitor({"id": 121, "status": True})
        client = FakeKeeneticClient()
        client.connected = True
        monitor._active_client = client

        monitor.stop()

        self.assertFalse(client.connected)
        self.assertFalse(monitor.is_running())

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

    def test_refresh_storm_is_coalesced_to_one_snapshot(self) -> None:
        with patch(
            "core.connection_worker.KeeneticClient",
            FakeKeeneticClient,
        ):
            monitor = RouterMonitor(
                {"id": 112, "status": False},
                interval_ms=1_000,
                paused=True,
            )

            for _index in range(1_000):
                monitor.request_refresh()
            monitor.start()

            try:
                self.assertTrue(FakeKeeneticClient.call_event.wait(1.0))
                time.sleep(0.18)
                self.assertEqual(FakeKeeneticClient.call_count, 1)
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

    def test_read_only_action_does_not_trigger_extra_full_refresh(self) -> None:
        instances: list[FakeKeeneticClient] = []

        class TrackedClient(FakeKeeneticClient):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                instances.append(self)

        with patch("core.connection_worker.KeeneticClient", TrackedClient):
            monitor = RouterMonitor(
                {"id": 109, "status": False},
                interval_ms=1_000,
                paused=False,
            )
            monitor.request_refresh()
            monitor.start()

            try:
                self.assertTrue(TrackedClient.call_event.wait(1.0))
                self.assertTrue(instances)
                monitor.request_system_log(100)
                self.assertTrue(instances[0].log_event.wait(1.0))
                time.sleep(0.2)
                self.assertEqual(TrackedClient.call_count, 1)
            finally:
                self.stop_monitor(monitor)

    def test_explicit_refresh_is_not_lost_beside_read_only_action(self) -> None:
        instances: list[FakeKeeneticClient] = []

        class TrackedClient(FakeKeeneticClient):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                instances.append(self)

        with patch("core.connection_worker.KeeneticClient", TrackedClient):
            monitor = RouterMonitor(
                {"id": 110, "status": False},
                interval_ms=1_000,
                paused=True,
            )
            monitor.request_refresh()
            monitor.start()

            try:
                self.assertTrue(TrackedClient.call_event.wait(1.0))
                self.assertTrue(instances)
                TrackedClient.call_event.clear()
                monitor.request_system_log(100)
                monitor.request_refresh()
                self.assertTrue(TrackedClient.call_event.wait(1.0))
                self.assertTrue(instances[0].log_event.wait(1.0))
                self.assertEqual(TrackedClient.call_count, 2)
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

    def test_unexpected_refresh_failure_is_reported_without_thread_exit(
        self,
    ) -> None:
        received = threading.Event()
        results: list[object] = []

        with (
            patch("core.connection_worker.KeeneticClient", ExplodingClient),
            patch(
                "core.connection_worker.report_background_exception"
            ) as report_exception,
        ):
            monitor = RouterMonitor(
                {"id": 111, "status": False},
                interval_ms=1_000,
                paused=True,
            )
            monitor.updated.connect(
                lambda _router_id, result: (
                    results.append(result),
                    received.set(),
                ),
                Qt.ConnectionType.DirectConnection,
            )
            monitor.request_refresh()
            monitor.start()

            try:
                self.assertTrue(received.wait(1.0))
                self.assertTrue(monitor.isRunning())
                self.assertFalse(results[-1].success)
                report_exception.assert_called_once()
            finally:
                self.stop_monitor(monitor)

    def test_constructor_failure_reports_result_and_stops_cleanly(self) -> None:
        updated = threading.Event()
        stopped = threading.Event()
        results: list[object] = []

        with (
            patch(
                "core.connection_worker.KeeneticClient",
                ExplodingConstructorClient,
            ),
            patch(
                "core.connection_worker.report_background_exception"
            ) as report_exception,
        ):
            monitor = RouterMonitor(
                {"id": 113, "status": False},
                interval_ms=1_000,
                paused=False,
            )
            monitor.updated.connect(
                lambda _router_id, result: (
                    results.append(result),
                    updated.set(),
                ),
                Qt.ConnectionType.DirectConnection,
            )
            monitor.monitor_stopped.connect(
                lambda _router_id: stopped.set(),
                Qt.ConnectionType.DirectConnection,
            )
            monitor.start()

            self.assertTrue(updated.wait(1.0))
            self.assertTrue(stopped.wait(1.0))
            self.assertTrue(monitor.wait(3_000))
            self.assertFalse(results[-1].success)
            self.assertEqual(results[-1].failure_kind, "internal")
            report_exception.assert_called_once()


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

    def test_probe_exception_is_reported_without_worker_exit(self) -> None:
        received = threading.Event()
        statuses: list[bool] = []
        worker = ExplodingStatusWorker(
            interval_seconds=1.0,
            timeout_seconds=0.5,
            paused=True,
        )
        worker.status_changed.connect(
            lambda _router_id, online, _address: (
                statuses.append(bool(online)),
                received.set(),
            ),
            Qt.ConnectionType.DirectConnection,
        )
        worker.set_routers([{"id": 7}])

        with patch(
            "core.router_status_worker.report_background_exception"
        ) as report_exception:
            worker.start()
            try:
                worker.request_refresh()
                self.assertTrue(received.wait(1.0))
                self.assertTrue(worker.isRunning())
                self.assertEqual(statuses, [False])
                report_exception.assert_called_once()
            finally:
                self.stop_worker(worker)


if __name__ == "__main__":
    unittest.main()
