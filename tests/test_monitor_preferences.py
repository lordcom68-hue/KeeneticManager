from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from ui.main_window import (
    MONITOR_INTERVALS,
    MainWindow,
    coerce_bool_setting,
    create_application_settings,
    load_monitor_preferences,
)
from ui.controllers.router_connection_controller import (  # noqa: E402
    RouterConnectionController,
)


class MonitorPreferenceTests(unittest.TestCase):
    def create_settings(self) -> QSettings:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "monitor.ini"
        return QSettings(
            str(path),
            QSettings.Format.IniFormat,
        )

    def test_boolean_settings_accept_qsettings_shapes(self) -> None:
        self.assertTrue(coerce_bool_setting(True, False))
        self.assertTrue(coerce_bool_setting("yes", False))
        self.assertTrue(coerce_bool_setting("да", False))
        self.assertFalse(coerce_bool_setting("false", True))
        self.assertFalse(coerce_bool_setting("нет", True))
        self.assertTrue(coerce_bool_setting("unknown", True))

    def test_portable_settings_use_local_ini_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "settings.ini"
            with patch.dict(
                os.environ,
                {"KEENETIC_MANAGER_SETTINGS_FILE": str(path)},
            ):
                settings = create_application_settings()
                settings.setValue("portable/check", True)
                settings.sync()

            self.assertEqual(
                Path(settings.fileName()).resolve(),
                path.resolve(),
            )
            self.assertTrue(path.is_file())

    def test_monitor_preferences_round_trip(self) -> None:
        settings = self.create_settings()
        settings.setValue("monitor/interval_ms", 10_000)
        settings.setValue("monitor/auto_refresh_enabled", True)
        settings.setValue("monitor/reachability_enabled", False)
        settings.sync()

        self.assertEqual(
            load_monitor_preferences(settings),
            (10_000, True, False),
        )

    def test_first_run_preserves_effective_auto_refresh_default(self) -> None:
        settings = self.create_settings()

        self.assertEqual(
            load_monitor_preferences(settings),
            (2_000, True, True),
        )

    def test_invalid_interval_uses_safe_default(self) -> None:
        settings = self.create_settings()
        settings.setValue("monitor/interval_ms", 1234)

        interval_ms, _, _ = load_monitor_preferences(settings)

        self.assertEqual(interval_ms, 2_000)
        self.assertIn(interval_ms, MONITOR_INTERVALS.values())

    def test_connect_does_not_force_auto_refresh_checkbox(self) -> None:
        source = inspect.getsource(
            RouterConnectionController.connect_selected
        )

        self.assertNotIn(
            "auto_refresh_checkbox.setChecked(True)",
            source,
        )
        self.assertIn("monitor.request_refresh()", source)

    def test_disabled_reachability_uses_unknown_status(self) -> None:
        self.assertEqual(
            MainWindow.router_status_state(
                None,
                checks_enabled=False,
            ),
            "unknown",
        )
        self.assertEqual(
            MainWindow.router_status_state(
                None,
                checks_enabled=True,
            ),
            "checking",
        )

    def test_background_success_does_not_spam_status_bar(self) -> None:
        source = inspect.getsource(
            MainWindow.connection_completed
        )

        self.assertNotIn(
            "Данные роутера обновлены",
            source,
        )


class ShutdownContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_shutdown_includes_active_and_stopping_monitors_once(self) -> None:
        status_worker = object()
        active = object()
        stopping = object()
        firmware_worker = object()
        fake_window = SimpleNamespace(
            router_status_worker=status_worker,
            router_monitors={1: active},
            stopping_router_monitors={1: active, 2: stopping},
            firmware_inspection_worker=firmware_worker,
        )

        candidates = MainWindow._shutdown_thread_candidates(
            fake_window
        )

        self.assertEqual(
            candidates,
            [status_worker, active, stopping, firmware_worker],
        )

    def test_deleted_qthread_is_treated_as_stopped(self) -> None:
        class DeletedThread:
            def isRunning(self):
                raise RuntimeError("deleted")

        self.assertFalse(
            MainWindow._thread_is_running(
                DeletedThread()
            )
        )

    def test_close_waits_non_blockingly_for_every_thread(self) -> None:
        class FakeThread:
            def __init__(self) -> None:
                self.running = True
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1
                self.running = False

            def isRunning(self) -> bool:
                return self.running

        class ShutdownWindow(MainWindow):
            def __init__(self) -> None:
                QMainWindow.__init__(self)
                self._shutdown_in_progress = False
                self._shutdown_ready = False
                self._shutdown_threads = []
                self._shutdown_poll_timer = QTimer(self)
                self._shutdown_poll_timer.setInterval(5)
                self._shutdown_poll_timer.timeout.connect(
                    self._poll_shutdown_threads
                )
                self.router_status_worker = FakeThread()
                self.active = FakeThread()
                self.stopping = FakeThread()
                self.firmware_inspection_worker = FakeThread()
                self.router_monitors = {1: self.active}
                self.stopping_router_monitors = {
                    2: self.stopping
                }

        window = ShutdownWindow()
        window.show()
        self.app.processEvents()

        window.close()
        QTest.qWait(30)
        self.app.processEvents()

        self.assertTrue(window._shutdown_ready)
        self.assertFalse(window.isVisible())
        self.assertEqual(
            window.router_status_worker.stop_calls,
            1,
        )
        self.assertEqual(window.active.stop_calls, 1)
        self.assertEqual(window.stopping.stop_calls, 1)
        self.assertEqual(
            window.firmware_inspection_worker.stop_calls,
            1,
        )

    def test_close_remains_responsive_while_request_is_finishing(self) -> None:
        class PendingThread:
            def __init__(self) -> None:
                self.running = True
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1

            def isRunning(self) -> bool:
                return self.running

        class ShutdownWindow(MainWindow):
            def __init__(self) -> None:
                QMainWindow.__init__(self)
                self._shutdown_in_progress = False
                self._shutdown_ready = False
                self._shutdown_threads = []
                self._shutdown_poll_timer = QTimer(self)
                self._shutdown_poll_timer.setInterval(5)
                self._shutdown_poll_timer.timeout.connect(
                    self._poll_shutdown_threads
                )
                self.pending = PendingThread()
                self.router_status_worker = self.pending
                self.router_monitors = {}
                self.stopping_router_monitors = {}
                self.firmware_inspection_worker = None

        window = ShutdownWindow()
        window.show()
        self.app.processEvents()

        started = monotonic()
        window.close()
        elapsed = monotonic() - started
        self.app.processEvents()

        self.assertLess(elapsed, 0.1)
        self.assertFalse(window.isVisible())
        self.assertFalse(window._shutdown_ready)
        self.assertEqual(window.pending.stop_calls, 1)

        window.pending.running = False
        QTest.qWait(30)
        self.app.processEvents()
        self.assertTrue(window._shutdown_ready)


if __name__ == "__main__":
    unittest.main()
