from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtWidgets import QMessageBox

from core.services.router_self_check_service import (
    RouterSelfCheckResult,
    RouterSelfCheckService,
)
from core.services.runtime_paths import (
    router_self_check_history_path,
    router_self_check_path,
)
from ui.dialogs.router_self_check_dialog import RouterSelfCheckDialog


class RouterSelfCheckController:
    """Coordinate persisted self-check state and its settings-page UI."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.in_progress: set[int] = set()
        self.status: dict[int, str] = {}
        self.results: dict[int, RouterSelfCheckResult] = {}
        self.history: dict[int, tuple[RouterSelfCheckResult, ...]] = {}

    @staticmethod
    def format_status(
        result: RouterSelfCheckResult,
        *,
        history_count: int = 1,
    ) -> str:
        timestamp = str(result.finished_at or "").strip()
        try:
            checked_at = datetime.fromisoformat(timestamp).strftime(
                "%d.%m.%Y %H:%M"
            )
        except ValueError:
            checked_at = timestamp or "время неизвестно"
        details = [
            f"Последняя проверка: {checked_at} — {result.overall_label}."
        ]
        if result.new_problem_count:
            details.append(f"Новых проблем: {result.new_problem_count}.")
        if result.resolved_count:
            details.append(f"Устранено: {result.resolved_count}.")
        details.append(f"Сохранено проверок: {max(1, history_count)}.")
        return " ".join(details)

    def load_results(self, router_ids: set[int]) -> None:
        active_ids = {int(router_id) for router_id in router_ids}
        self.results = {
            key: value
            for key, value in self.results.items()
            if key in active_ids
        }
        self.history = {
            key: value
            for key, value in self.history.items()
            if key in active_ids
        }
        self.status = {
            key: value
            for key, value in self.status.items()
            if key in active_ids
        }
        for router_id in active_ids:
            history = RouterSelfCheckService.load_history(
                router_self_check_history_path(router_id)
            )
            latest_payload = RouterSelfCheckService.load_summary(
                router_self_check_path(router_id)
            )
            latest: RouterSelfCheckResult | None = None
            if latest_payload:
                try:
                    latest = RouterSelfCheckResult.from_dict(latest_payload)
                except (TypeError, ValueError):
                    latest = None
            if latest is None and history:
                latest = history[-1]
            if latest is None:
                continue
            if not history or history[-1].finished_at != latest.finished_at:
                history.append(latest)
            history = history[-RouterSelfCheckService.HISTORY_LIMIT :]
            self.results[router_id] = latest
            self.history[router_id] = tuple(history)
            self.status[router_id] = self.format_status(
                latest,
                history_count=len(history),
            )

    def status_text(self, router_id: int | None) -> str:
        if router_id is None:
            return "Самопроверка ещё не запускалась."
        return self.status.get(
            int(router_id),
            "Самопроверка ещё не запускалась.",
        )

    def sync_label(self) -> None:
        label = getattr(self.host, "router_self_check_status_label", None)
        if label is not None:
            label.setText(
                self.status_text(self.host.get_selected_router_id())
            )

    def update_controls(self) -> None:
        button = getattr(self.host, "router_self_check_button", None)
        if button is None:
            return
        router_id = self.host.get_selected_router_id()
        connected = self.host.router_is_fully_connected(router_id)
        busy = bool(router_id is not None and router_id in self.in_progress)
        button.setEnabled(connected and not busy)
        button.setText(
            "Самопроверка выполняется..."
            if busy
            else "Запустить самопроверку"
        )
        latest = (
            self.results.get(int(router_id))
            if router_id is not None
            else None
        )
        problem_button = getattr(
            self.host,
            "router_self_check_problem_button",
            None,
        )
        if problem_button is not None:
            problem_button.setEnabled(
                connected
                and not busy
                and latest is not None
                and bool(latest.problem_keys)
            )
        history_button = getattr(
            self.host,
            "router_self_check_history_button",
            None,
        )
        if history_button is not None:
            history_button.setEnabled(not busy and latest is not None)

    def run_full(self) -> None:
        router_id = self.host.get_selected_router_id()
        if not self.host.router_is_fully_connected(router_id):
            QMessageBox.warning(
                self.host,
                "Самопроверка роутера",
                "Для самопроверки сначала подключитесь к роутеру.",
            )
            return
        monitor = self.host.router_monitors.get(int(router_id or 0))
        if monitor is None or router_id in self.in_progress:
            return
        self.in_progress.add(int(router_id))
        status = "Подготовка безопасной самопроверки..."
        self.status[int(router_id)] = status
        self.sync_label()
        self.update_controls()
        monitor.request_router_self_check()

    def run_problems(self) -> None:
        router_id = self.host.get_selected_router_id()
        if not self.host.router_is_fully_connected(router_id):
            QMessageBox.warning(
                self.host,
                "Самопроверка роутера",
                "Для повторной проверки сначала подключитесь к роутеру.",
            )
            return
        latest = self.results.get(int(router_id or 0))
        problem_keys = latest.problem_keys if latest is not None else ()
        if not problem_keys:
            QMessageBox.information(
                self.host,
                "Самопроверка роутера",
                "В последнем отчёте нет проблемных проверок.",
            )
            return
        monitor = self.host.router_monitors.get(int(router_id or 0))
        if monitor is None or router_id in self.in_progress:
            return
        self.in_progress.add(int(router_id))
        self.status[int(router_id)] = (
            "Повторяются только проблемные проверки..."
        )
        self.sync_label()
        self.update_controls()
        monitor.request_router_self_check(problem_keys)

    def open_history(self) -> None:
        router_id = self.host.get_selected_router_id()
        latest = (
            self.results.get(int(router_id))
            if router_id is not None
            else None
        )
        if latest is None:
            QMessageBox.information(
                self.host,
                "История самопроверок",
                "Для выбранного роутера ещё нет сохранённых проверок.",
            )
            return
        RouterSelfCheckDialog(
            latest,
            self.host,
            history=self.history.get(int(router_id), (latest,)),
        ).exec()

    def progress(
        self,
        router_id: int,
        percentage: int,
        message: str,
    ) -> None:
        router_id = int(router_id)
        status = f"{str(message)} {max(0, min(100, int(percentage)))}%"
        self.status[router_id] = status
        if router_id == self.host.get_selected_router_id():
            self.sync_label()

    def completed(
        self,
        router_id: int,
        success: bool,
        message: str,
        result: object,
    ) -> None:
        router_id = int(router_id)
        self.in_progress.discard(router_id)
        if success and isinstance(result, RouterSelfCheckResult):
            history = list(self.history.get(router_id, ()))
            if not history or history[-1].finished_at != result.finished_at:
                history.append(result)
            history = history[-RouterSelfCheckService.HISTORY_LIMIT :]
            self.results[router_id] = result
            self.history[router_id] = tuple(history)
            status = self.format_status(result, history_count=len(history))
        else:
            status = f"Самопроверка не завершена: {str(message)}"
        self.status[router_id] = status
        if router_id != self.host.get_selected_router_id():
            return
        self.sync_label()
        self.update_controls()
        if success and isinstance(result, RouterSelfCheckResult):
            RouterSelfCheckDialog(
                result,
                self.host,
                history=self.history.get(router_id, (result,)),
            ).exec()
        else:
            QMessageBox.warning(
                self.host,
                "Самопроверка роутера",
                str(message),
            )

    def forget_router(self, router_id: int) -> None:
        router_id = int(router_id)
        self.in_progress.discard(router_id)
        self.status.pop(router_id, None)
        self.results.pop(router_id, None)
        self.history.pop(router_id, None)

    def monitor_stopped(self, router_id: int) -> None:
        router_id = int(router_id)
        if router_id not in self.in_progress:
            return
        self.in_progress.discard(router_id)
        self.status[router_id] = (
            "Самопроверка остановлена вместе с мониторингом."
        )
        if self.host.get_selected_router_id() == router_id:
            self.sync_label()
            self.update_controls()
