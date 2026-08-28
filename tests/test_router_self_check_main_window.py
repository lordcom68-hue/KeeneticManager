from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.services.router_self_check_service import (
    RouterSelfCheckItem,
    RouterSelfCheckResult,
)
from ui.controllers.router_self_check_controller import (
    RouterSelfCheckController,
)


def result() -> RouterSelfCheckResult:
    return RouterSelfCheckResult(
        schema_version=1,
        router_id=7,
        started_at="2026-08-16T12:00:00+03:00",
        finished_at="2026-08-16T12:00:01+03:00",
        overall_status="passed",
        overall_label="Проверка пройдена",
        duration_ms=1_000,
        model="KN-2311",
        version="5.1.0",
        connection_type="Локальная сеть",
        checks=(
            RouterSelfCheckItem(
                key="active_session",
                category="Подключение",
                title="Активная сессия",
                status="passed",
                summary="Сессия активна.",
            ),
        ),
    )


class RouterSelfCheckControllerTests(unittest.TestCase):
    def test_start_marks_router_busy_and_queues_monitor_action(self) -> None:
        monitor = Mock()
        host = SimpleNamespace(
            get_selected_router_id=lambda: 7,
            router_is_fully_connected=lambda router_id: router_id == 7,
            router_monitors={7: monitor},
            router_self_check_status_label=Mock(),
        )
        controller = RouterSelfCheckController(host)
        controller.update_controls = Mock()

        controller.run_full()

        self.assertEqual(controller.in_progress, {7})
        monitor.request_router_self_check.assert_called_once_with()
        controller.update_controls.assert_called_once_with()

    def test_completed_check_unblocks_button_and_opens_report(self) -> None:
        check_result = result()
        host = SimpleNamespace(
            get_selected_router_id=lambda: 7,
            router_self_check_status_label=Mock(),
        )
        controller = RouterSelfCheckController(host)
        controller.in_progress.add(7)
        controller.update_controls = Mock()
        dialog = Mock()

        with patch(
            "ui.controllers.router_self_check_controller.RouterSelfCheckDialog",
            return_value=dialog,
        ):
            controller.completed(
                7,
                True,
                check_result.overall_label,
                check_result,
            )

        self.assertEqual(controller.in_progress, set())
        self.assertIn("Последняя проверка:", controller.status[7])
        controller.update_controls.assert_called_once_with()
        dialog.exec.assert_called_once_with()

    def test_repeat_problem_checks_passes_only_problem_keys(self) -> None:
        monitor = Mock()
        check_result = replace(
            result(),
            overall_status="warning",
            overall_label="Есть предупреждения",
            checks=(
                RouterSelfCheckItem(
                    key="local_http",
                    category="Подключение",
                    title="Локальный HTTP",
                    status="passed",
                    summary="Готово.",
                ),
                RouterSelfCheckItem(
                    key="keendns_https",
                    category="Подключение",
                    title="KeenDNS HTTPS",
                    status="warning",
                    summary="Не отвечает.",
                ),
            ),
        )
        host = SimpleNamespace(
            get_selected_router_id=lambda: 7,
            router_is_fully_connected=lambda router_id: router_id == 7,
            router_monitors={7: monitor},
            router_self_check_status_label=Mock(),
        )
        controller = RouterSelfCheckController(host)
        controller.results[7] = check_result
        controller.update_controls = Mock()

        controller.run_problems()

        monitor.request_router_self_check.assert_called_once_with(
            ("keendns_https",)
        )
        self.assertEqual(controller.in_progress, {7})

    def test_persisted_status_contains_time_and_comparison(self) -> None:
        check_result = replace(
            result(),
            finished_at="2026-08-16T14:05:00+03:00",
            checks=(
                RouterSelfCheckItem(
                    key="keendns_https",
                    category="Подключение",
                    title="KeenDNS HTTPS",
                    status="passed",
                    summary="Готово.",
                    change="resolved",
                    previous_status="warning",
                ),
            ),
        )

        text = RouterSelfCheckController.format_status(
            check_result,
            history_count=3,
        )

        self.assertIn("16.08.2026 14:05", text)
        self.assertIn("Устранено: 1", text)
        self.assertIn("Сохранено проверок: 3", text)

    def test_background_router_result_is_saved_without_popup(self) -> None:
        check_result = result()
        host = SimpleNamespace(
            get_selected_router_id=lambda: 9,
        )
        controller = RouterSelfCheckController(host)
        controller.in_progress.add(7)

        with patch(
            "ui.controllers.router_self_check_controller.RouterSelfCheckDialog"
        ) as dialog:
            controller.completed(
                7,
                True,
                check_result.overall_label,
                check_result,
            )

        self.assertEqual(controller.in_progress, set())
        self.assertIn(7, controller.status)
        dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
