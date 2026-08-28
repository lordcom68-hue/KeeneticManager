from __future__ import annotations

import faulthandler
import json
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from core.app_version import APP_NAME, APP_VERSION
from core.rci.diagnostics import sanitize_text, sanitize_value
from core.services.runtime_paths import CRASH_LOG_PATH, FATAL_LOG_PATH


NotificationCallback = Callable[[str], None]


class CrashReportService:
    """Persist unexpected Python and background-thread failures safely."""

    MAX_LOG_BYTES = 1_000_000
    MAX_TRACEBACK_CHARS = 64_000
    NOTIFICATION_COOLDOWN_SECONDS = 15.0

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        fatal_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else CRASH_LOG_PATH
        self.fatal_path = (
            Path(fatal_path) if fatal_path is not None else FATAL_LOG_PATH
        )
        self._lock = threading.RLock()
        self._notify: NotificationCallback | None = None
        self._last_notification_at = 0.0
        self._installed = False
        self._previous_sys_hook: Any = None
        self._previous_thread_hook: Any = None
        self._previous_unraisable_hook: Any = None
        self._sys_hook: Any = None
        self._thread_hook: Any = None
        self._unraisable_hook: Any = None
        self._fatal_stream: Any = None
        self._faulthandler_enabled_here = False

    def record_exception(
        self,
        error: BaseException,
        *,
        context: str,
        exception_type: type[BaseException] | None = None,
        exception_traceback: Any = None,
        details: dict[str, Any] | None = None,
        notify: bool = False,
    ) -> Path | None:
        """Append one sanitized record without ever raising to the caller."""
        error_type = exception_type or type(error)
        trace = exception_traceback
        if trace is None:
            trace = error.__traceback__
        try:
            rendered_traceback = "".join(
                traceback.format_exception(error_type, error, trace)
            )
        except Exception:
            rendered_traceback = f"{error_type.__name__}: {error}"

        payload = sanitize_value(
            {
                "timestamp": datetime.now().astimezone().isoformat(),
                "application": APP_NAME,
                "application_version": APP_VERSION,
                "context": str(context or "unknown"),
                "thread": threading.current_thread().name,
                "exception_type": error_type.__name__,
                "exception": str(error),
                "traceback": rendered_traceback[
                    -self.MAX_TRACEBACK_CHARS :
                ],
                "details": dict(details or {}),
            }
        )

        result: Path | None = None
        try:
            line = json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
            ) + "\n"
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(line.encode("utf-8")))
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
                result = self.path
        except OSError:
            result = None

        if notify:
            self._notify_user()
        return result

    def install(
        self,
        *,
        notify: NotificationCallback | None = None,
        enable_faulthandler: bool = True,
    ) -> None:
        if self._installed:
            self._notify = notify
            return

        self._notify = notify
        self._previous_sys_hook = sys.excepthook
        self._previous_thread_hook = getattr(threading, "excepthook", None)
        self._previous_unraisable_hook = getattr(sys, "unraisablehook", None)

        self._sys_hook = self._handle_sys_exception
        self._thread_hook = self._handle_thread_exception
        self._unraisable_hook = self._handle_unraisable_exception
        sys.excepthook = self._sys_hook
        if self._previous_thread_hook is not None:
            threading.excepthook = self._thread_hook
        if self._previous_unraisable_hook is not None:
            sys.unraisablehook = self._unraisable_hook

        if enable_faulthandler:
            self._enable_faulthandler()
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        if sys.excepthook is self._sys_hook:
            sys.excepthook = self._previous_sys_hook
        if (
            self._previous_thread_hook is not None
            and threading.excepthook is self._thread_hook
        ):
            threading.excepthook = self._previous_thread_hook
        if (
            self._previous_unraisable_hook is not None
            and sys.unraisablehook is self._unraisable_hook
        ):
            sys.unraisablehook = self._previous_unraisable_hook

        if self._faulthandler_enabled_here:
            try:
                faulthandler.disable()
            except (RuntimeError, OSError):
                pass
        if self._fatal_stream is not None:
            try:
                self._fatal_stream.close()
            except OSError:
                pass
        self._fatal_stream = None
        self._faulthandler_enabled_here = False
        self._installed = False

    def _handle_sys_exception(
        self,
        exception_type: type[BaseException],
        error: BaseException,
        exception_traceback: Any,
    ) -> None:
        self.record_exception(
            error,
            context="main_thread",
            exception_type=exception_type,
            exception_traceback=exception_traceback,
            notify=True,
        )
        if callable(self._previous_sys_hook):
            self._previous_sys_hook(
                exception_type,
                error,
                exception_traceback,
            )

    def _handle_thread_exception(self, arguments: Any) -> None:
        error = arguments.exc_value
        self.record_exception(
            error,
            context="background_thread",
            exception_type=arguments.exc_type,
            exception_traceback=arguments.exc_traceback,
            details={"thread_name": getattr(arguments.thread, "name", "")},
            notify=True,
        )
        if callable(self._previous_thread_hook):
            self._previous_thread_hook(arguments)

    def _handle_unraisable_exception(self, arguments: Any) -> None:
        error = arguments.exc_value or RuntimeError(
            str(arguments.err_msg or "Unraisable exception")
        )
        self.record_exception(
            error,
            context="unraisable",
            exception_type=arguments.exc_type or type(error),
            exception_traceback=arguments.exc_traceback,
            details={"message": str(arguments.err_msg or "")},
            notify=False,
        )
        if callable(self._previous_unraisable_hook):
            self._previous_unraisable_hook(arguments)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_size = self.path.stat().st_size
        except OSError:
            current_size = 0
        if current_size + max(0, incoming_bytes) <= self.MAX_LOG_BYTES:
            return
        backup = self.path.with_suffix(self.path.suffix + ".1")
        backup.unlink(missing_ok=True)
        self.path.replace(backup)

    def _notify_user(self) -> None:
        callback = self._notify
        if callback is None:
            return
        now = monotonic()
        with self._lock:
            if (
                now - self._last_notification_at
                < self.NOTIFICATION_COOLDOWN_SECONDS
            ):
                return
            self._last_notification_at = now
        message = (
            "Произошла непредвиденная ошибка. Программа сохранила "
            "обезличенные сведения для диагностики:\n"
            f"{self.path}"
        )
        try:
            callback(sanitize_text(message))
        except Exception:
            return

    def _enable_faulthandler(self) -> None:
        try:
            if faulthandler.is_enabled():
                return
            self.fatal_path.parent.mkdir(parents=True, exist_ok=True)
            self._fatal_stream = self.fatal_path.open(
                "a",
                encoding="utf-8",
            )
            self._fatal_stream.write(
                "\n--- session "
                f"{datetime.now().astimezone().isoformat()} ---\n"
            )
            self._fatal_stream.flush()
            faulthandler.enable(file=self._fatal_stream, all_threads=True)
            self._faulthandler_enabled_here = True
        except (OSError, RuntimeError):
            if self._fatal_stream is not None:
                try:
                    self._fatal_stream.close()
                except OSError:
                    pass
            self._fatal_stream = None


_default_service: CrashReportService | None = None
_default_service_lock = threading.Lock()


def get_crash_report_service() -> CrashReportService:
    global _default_service
    with _default_service_lock:
        if _default_service is None:
            _default_service = CrashReportService()
        return _default_service


def report_background_exception(
    context: str,
    error: BaseException,
    *,
    details: dict[str, Any] | None = None,
) -> Path | None:
    return get_crash_report_service().record_exception(
        error,
        context=context,
        details=details,
        notify=False,
    )
