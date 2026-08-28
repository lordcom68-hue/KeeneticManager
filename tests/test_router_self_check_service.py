from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import requests

from core.services.rci_transport import RciTransport
from core.connection_worker import RouterMonitor
from core.services.router_self_check_service import (
    RouterSelfCheckCancelled,
    RouterSelfCheckItem,
    RouterSelfCheckResult,
    RouterSelfCheckService,
)


class StepClock:
    def __init__(self, step: float = 0.1) -> None:
        self.value = 100.0
        self.step = float(step)

    def __call__(self) -> float:
        value = self.value
        self.value += self.step
        return value


class FakeProbeTransport:
    created: list[FakeProbeTransport] = []
    failures: dict[str, BaseException] = {}

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.connection_type = ""
        self.base_url = ""
        self.closed = False
        type(self).created.append(self)

    def connect_candidate(self, *, base_url: str, connection_type: str) -> None:
        self.base_url = base_url
        self.connection_type = connection_type
        failure = type(self).failures.get(connection_type)
        if failure is not None:
            raise failure

    def get_json(self, *, endpoint: str, required: bool) -> dict[str, Any]:
        assert endpoint == "show/version"
        assert required
        return {"release": "5.1.0"}

    def close(self) -> None:
        self.closed = True


def compatibility_profile(
    *,
    wifi_state: str = "supported",
    log_state: str = "supported",
    optional_features: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "level": "supported",
        "features": [
            {
                "key": "wifi_management",
                "label": "Управление Wi-Fi",
                "state": wifi_state,
            },
            {
                "key": "system_log",
                "label": "Системный журнал",
                "state": log_state,
            },
            *(optional_features or []),
        ],
    }


def successful_result(
    *,
    profile: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        model="N6 AX1800 (KN-2311)",
        version="5.01.C.3.0-1",
        system_data={"uptime": 100},
        internet_connections=[{"id": "ISP"}],
        clients=[],
        ports=[{"id": "GigabitEthernet0"}],
        wifi_networks=[{"ssid": "Home"}],
        compatibility_profile=profile or compatibility_profile(),
    )


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.connected = True
        self.session = object()
        self.base_url = "http://192.0.2.1"
        self.connection_type = "Локальная сеть"
        self.username = "admin"
        self.password = "top-secret"
        self.timeout = 3.0
        self.transport = SimpleNamespace(build_url=RciTransport.build_url)
        self.result = result
        self.refresh_calls: list[bool] = []
        self.system_log_service = SimpleNamespace(
            read=lambda _limit: (True, "ok", [{"message": "ready"}])
        )

    def refresh(self, *, force_full: bool = False) -> Any:
        self.refresh_calls.append(bool(force_full))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class RouterSelfCheckServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeProbeTransport.created = []
        FakeProbeTransport.failures = {}

    def test_successful_check_probes_both_channels_and_saves_safe_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "self_check.json"
            client = FakeClient(successful_result())
            progress: list[int] = []
            service = RouterSelfCheckService(
                client,
                router_id=7,
                path=path,
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            result = service.run(
                local_ip="192.0.2.1",
                keendns="private-router.keenetic.pro",
                progress=lambda value, _message: progress.append(value),
            )

            self.assertEqual(result.overall_status, "passed")
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(client.refresh_calls, [True])
            self.assertEqual(progress[-1], 100)
            self.assertEqual(
                [item.base_url for item in FakeProbeTransport.created],
                [
                    "http://192.0.2.1",
                    "https://private-router.keenetic.pro",
                ],
            )
            self.assertTrue(
                all(
                    item.kwargs["persist_history"] is False
                    for item in FakeProbeTransport.created
                )
            )
            encoded = path.read_text(encoding="utf-8")
            self.assertNotIn("192.0.2.1", encoded)
            self.assertNotIn("private-router", encoded)
            self.assertNotIn("top-secret", encoded)
            self.assertFalse(json.loads(encoded)["privacy"]["contains_addresses"])

    def test_missing_channels_and_unsupported_features_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = compatibility_profile(
                wifi_state="unavailable",
                log_state="unavailable",
            )
            client = FakeClient(successful_result(profile=profile))
            service = RouterSelfCheckService(
                client,
                router_id=4,
                path=Path(directory) / "self_check.json",
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            result = service.run(local_ip="", keendns="")
            by_key = {item.key: item for item in result.checks}

            self.assertEqual(by_key["local_http"].status, "skipped")
            self.assertEqual(by_key["keendns_https"].status, "skipped")
            self.assertEqual(by_key["wifi_data"].status, "skipped")
            self.assertEqual(by_key["system_log"].status, "skipped")
            self.assertEqual(by_key["compatibility_profile"].status, "warning")
            self.assertIn(
                "Недоступны: Управление Wi-Fi, Системный журнал.",
                by_key["compatibility_profile"].summary,
            )

    def test_optional_features_do_not_raise_compatibility_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = compatibility_profile(
                optional_features=[
                    {
                        "key": "usb_storage",
                        "label": "USB и накопители",
                        "state": "unavailable",
                    }
                ]
            )
            client = FakeClient(successful_result(profile=profile))
            service = RouterSelfCheckService(
                client,
                router_id=4,
                path=Path(directory) / "self_check.json",
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            result = service.run(
                local_ip="192.0.2.1",
                keendns="router.keenetic.pro",
            )
            check = next(
                item
                for item in result.checks
                if item.key == "compatibility_profile"
            )

            self.assertEqual(result.overall_status, "passed")
            self.assertEqual(check.status, "passed")
            self.assertIn("Основные функции доступны.", check.summary)
            self.assertIn(
                "Необязательные функции отсутствуют: USB и накопители.",
                check.summary,
            )

    def test_probe_failure_is_sanitized_and_does_not_fail_main_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            FakeProbeTransport.failures = {
                "KeenDNS": requests.ConnectionError(
                    "failed for https://private-router.keenetic.pro"
                )
            }
            service = RouterSelfCheckService(
                FakeClient(successful_result()),
                router_id=5,
                path=Path(directory) / "self_check.json",
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            result = service.run(
                local_ip="192.0.2.1",
                keendns="private-router.keenetic.pro",
            )
            item = next(
                check for check in result.checks
                if check.key == "keendns_https"
            )

            self.assertEqual(result.overall_status, "warning")
            self.assertEqual(item.summary, "Адрес не отвечает.")
            self.assertNotIn("private-router", str(result.to_dict()))

    def test_failed_full_refresh_marks_report_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = RouterSelfCheckService(
                FakeClient(RuntimeError("secret internal failure")),
                router_id=6,
                path=Path(directory) / "self_check.json",
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            result = service.run(local_ip="", keendns="")
            by_key = {item.key: item for item in result.checks}

            self.assertEqual(result.overall_status, "failed")
            self.assertEqual(by_key["full_refresh"].status, "failed")
            self.assertNotIn("secret internal failure", str(result.to_dict()))

    def test_cancellation_stops_before_router_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = RouterSelfCheckService(
                FakeClient(successful_result()),
                router_id=8,
                path=Path(directory) / "self_check.json",
                transport_factory=FakeProbeTransport,
            )

            with self.assertRaises(RouterSelfCheckCancelled):
                service.run(
                    local_ip="192.0.2.1",
                    keendns="router.keenetic.pro",
                    cancelled=lambda: True,
                )

            self.assertEqual(FakeProbeTransport.created, [])

    def test_transport_can_disable_persistent_history_for_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = RciTransport(
                username="admin",
                password="secret",
                router_id=3,
                history_root=root,
                persist_history=False,
            )

            transport.record_connection_plan(
                local_ip="192.0.2.1",
                keendns="",
                connection_mode="local",
                candidates=[("http://192.0.2.1", "Локальная сеть")],
            )

            self.assertFalse(transport.history_path.exists())
            self.assertTrue(transport.diagnostics.connection_attempts)

    def test_history_is_bounded_and_latest_result_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "self_check.json"
            history_path = Path(directory) / "history.json"
            service = RouterSelfCheckService(
                FakeClient(successful_result()),
                router_id=9,
                path=path,
                history_path=history_path,
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )

            for index in range(12):
                item = RouterSelfCheckResult(
                    schema_version=2,
                    router_id=9,
                    started_at=f"2026-08-16T12:00:{index:02d}+03:00",
                    finished_at=f"2026-08-16T12:00:{index:02d}+03:00",
                    overall_status="passed",
                    overall_label="Проверка пройдена",
                    duration_ms=100,
                    model="KN-2311",
                    version="5.1",
                    connection_type="Локальная сеть",
                    checks=(),
                )
                self.assertTrue(service.save(item))

            history = service.load_history(history_path)

            self.assertEqual(len(history), 10)
            self.assertTrue(history[0].finished_at.endswith("02+03:00"))
            self.assertTrue(history[-1].finished_at.endswith("11+03:00"))
            self.assertEqual(service.load_latest(), history[-1])

    def test_partial_run_repeats_only_problem_and_marks_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "self_check.json"
            previous = RouterSelfCheckResult(
                schema_version=2,
                router_id=5,
                started_at="2026-08-16T12:00:00+03:00",
                finished_at="2026-08-16T12:00:01+03:00",
                overall_status="warning",
                overall_label="Есть предупреждения",
                duration_ms=1_000,
                model="KN-2311",
                version="5.1",
                connection_type="Локальная сеть",
                checks=(
                    RouterSelfCheckItem(
                        key="local_http",
                        category="Подключение",
                        title="Локальный HTTP",
                        status="passed",
                        summary="Работает.",
                    ),
                    RouterSelfCheckItem(
                        key="keendns_https",
                        category="Подключение",
                        title="KeenDNS HTTPS",
                        status="warning",
                        summary="Адрес не отвечает.",
                    ),
                ),
            )
            service = RouterSelfCheckService(
                FakeClient(successful_result()),
                router_id=5,
                path=path,
                transport_factory=FakeProbeTransport,
                monotonic=StepClock(),
            )
            self.assertTrue(service.save(previous))
            FakeProbeTransport.created = []

            result = service.run(
                local_ip="192.0.2.1",
                keendns="router.keenetic.pro",
                check_keys={"keendns_https"},
            )

            self.assertEqual(result.run_mode, "problems")
            self.assertEqual(result.executed_keys, ("keendns_https",))
            self.assertEqual(
                [item.connection_type for item in FakeProbeTransport.created],
                ["KeenDNS"],
            )
            self.assertEqual(
                next(
                    item.change
                    for item in result.checks
                    if item.key == "keendns_https"
                ),
                "resolved",
            )
            self.assertEqual(result.resolved_count, 1)
            self.assertEqual(result.overall_status, "passed")
            self.assertEqual(service.client.refresh_calls, [])

    def test_monitor_routes_self_check_without_mutating_action(self) -> None:
        monitor = RouterMonitor(
            {
                "id": 19,
                "local_ip": "192.0.2.1",
                "keendns": "router.keenetic.pro",
            }
        )
        emitted: list[tuple[Any, ...]] = []
        progress: list[tuple[Any, ...]] = []
        monitor.router_self_check_completed.connect(
            lambda *args: emitted.append(args)
        )
        monitor.router_self_check_progress.connect(
            lambda *args: progress.append(args)
        )
        expected = SimpleNamespace(
            overall_label="Проверка пройдена"
        )
        service = Mock()

        def run(**kwargs: Any) -> Any:
            kwargs["progress"](50, "Проверка")
            return expected

        service.run.side_effect = run
        with patch(
            "core.connection_worker.RouterSelfCheckService",
            return_value=service,
        ) as service_type:
            refresh_required = monitor.process_action(
                client=SimpleNamespace(),  # type: ignore[arg-type]
                action_type="router_self_check",
                payload=None,
            )

        self.assertFalse(refresh_required)
        self.assertIsNone(service.run.call_args.kwargs["check_keys"])
        service_type.assert_called_once()
        self.assertEqual(progress, [(19, 50, "Проверка")])
        self.assertEqual(
            emitted,
            [(19, True, "Проверка пройдена", expected)],
        )


if __name__ == "__main__":
    unittest.main()
