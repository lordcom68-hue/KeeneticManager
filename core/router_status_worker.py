from __future__ import annotations

import http.client
import socket
import ssl
import threading
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from core.services.reachability import ReachabilityTracker
from core.services.crash_report_service import report_background_exception


class RouterStatusWorker(QThread):
    """
    Фоновая проверка реальной доступности роутеров.

    Локальный адрес проверяется TCP-соединением.
    KeenDNS проверяется HTTPS-запросом к службе авторизации, потому что
    облачный сервер KeenDNS может принимать TCP-соединение,
    даже когда сам роутер выключен.
    """

    status_changed = Signal(int, bool, str)
    cycle_completed = Signal()

    OFFLINE_STATUS_CODES = {
        502,
        503,
        504,
        521,
        522,
        523,
        524,
    }

    def __init__(
        self,
        interval_seconds: float = 3.0,
        timeout_seconds: float = 1.5,
        paused: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.interval_seconds = max(
            1.0,
            float(interval_seconds),
        )
        self.timeout_seconds = max(
            0.5,
            float(timeout_seconds),
        )

        self._routers: list[dict] = []
        self._routers_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._paused = bool(paused)
        self._refresh_requested = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._reachability = ReachabilityTracker(
            failure_threshold=3,
        )

    def set_routers(
        self,
        routers: list[dict],
    ) -> None:
        with self._routers_lock:
            self._routers = [
                dict(router)
                for router in routers
                if router.get("id") is not None
            ]
            router_ids = {
                int(router["id"])
                for router in self._routers
            }

        self._reachability.retain_only(router_ids)
        self._wake_event.set()

    def request_refresh(self) -> None:
        with self._state_lock:
            self._refresh_requested = True
        self._wake_event.set()

    def set_paused(self, paused: bool) -> None:
        with self._state_lock:
            self._paused = bool(paused)
        self._wake_event.set()

    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    def _consume_refresh_request(self) -> bool:
        with self._state_lock:
            requested = self._refresh_requested
            self._refresh_requested = False
        return requested

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            # Состояние паузы и одноразового запроса хранится
            # отдельно. Старый wake не должен запускать второй
            # цикл сразу после первого.
            self._wake_event.clear()
            force_refresh = (
                self._consume_refresh_request()
            )

            if self.is_paused() and not force_refresh:
                self._wake_event.wait(0.25)
                continue

            with self._routers_lock:
                routers = [
                    dict(router)
                    for router in self._routers
                ]

            for router in routers:
                if (
                    self._stop_event.is_set()
                    or (
                        self.is_paused()
                        and not force_refresh
                    )
                ):
                    break

                router_id = int(router["id"])
                try:
                    probe_online, probe_address = self._check_router(
                        router
                    )
                except Exception as error:
                    report_background_exception(
                        "router_status_worker.probe",
                        error,
                        details={"router_id": router_id},
                    )
                    probe_online, probe_address = False, ""
                result = self._reachability.update(
                    router_id=router_id,
                    probe_online=probe_online,
                    address=probe_address,
                )

                self.status_changed.emit(
                    router_id,
                    result.online,
                    result.address,
                )

            self.cycle_completed.emit()

            if self.is_paused():
                continue

            self._wake_event.wait(
                self.interval_seconds
            )

    def _check_router(
        self,
        router: dict,
    ) -> tuple[bool, str]:
        # Локальный IP имеет приоритет.
        local_value = str(
            router.get("local_ip")
            or ""
        ).strip()

        if local_value:
            local_target = self._parse_target(
                local_value,
                default_scheme="http",
            )

            if (
                local_target is not None
                and self._check_local_target(
                    *local_target
                )
            ):
                scheme, host, port = local_target
                return (
                    True,
                    f"{scheme}://{host}:{port}",
                )

        # KeenDNS нельзя проверять только открытием TCP-порта:
        # порт принадлежит облачному прокси и может быть доступен
        # при выключенном роутере.
        keendns_value = str(
            router.get("keendns")
            or ""
        ).strip()

        if keendns_value:
            targets = self._keendns_targets(
                keendns_value
            )

            for scheme, host, port in targets:
                if self._check_keendns_http(
                    scheme,
                    host,
                    port,
                ):
                    return (
                        True,
                        f"{scheme}://{host}:{port}",
                    )

        return False, ""

    def _check_local_target(
        self,
        scheme: str,
        host: str,
        port: int,
    ) -> bool:
        try:
            with socket.create_connection(
                (host, port),
                timeout=self.timeout_seconds,
            ):
                return True
        except (OSError, socket.timeout):
            # Если пользователь не указал порт, пробуем
            # альтернативный стандартный веб-порт.
            alternative = (
                443 if port == 80 else 80
            )

            try:
                with socket.create_connection(
                    (host, alternative),
                    timeout=self.timeout_seconds,
                ):
                    return True
            except (OSError, socket.timeout):
                return False

    def _check_keendns_http(
        self,
        scheme: str,
        host: str,
        port: int,
    ) -> bool:
        connection = None

        try:
            if scheme == "https":
                context = ssl.create_default_context()
                connection = (
                    http.client.HTTPSConnection(
                        host,
                        port=port,
                        timeout=self.timeout_seconds,
                        context=context,
                    )
                )
            else:
                connection = (
                    http.client.HTTPConnection(
                        host,
                        port=port,
                        timeout=self.timeout_seconds,
                    )
                )

            connection.request(
                "GET",
                "/auth",
                headers={
                    "User-Agent": (
                        "KeeneticManager/"
                        "RouterStatusCheck"
                    ),
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )

            response = connection.getresponse()
            status = int(response.status)
            response.read(65536)

            if status in self.OFFLINE_STATUS_CODES:
                return False

            # 200 означает уже открытую/активную сессию. Для анонимного
            # запроса настоящий Keenetic отвечает 401 и передаёт оба
            # заголовка challenge. Ответ облачного прокси, редирект или
            # обычный 401 без этих заголовков не подтверждает роутер.
            if status == 200:
                return True

            if status == 401:
                realm = str(
                    response.getheader("X-NDM-Realm") or ""
                ).strip()
                challenge = str(
                    response.getheader("X-NDM-Challenge") or ""
                ).strip()
                return bool(realm and challenge)

            return False

        except (
            OSError,
            socket.timeout,
            ssl.SSLError,
            http.client.HTTPException,
        ):
            return False

        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def _keendns_targets(
        self,
        value: str,
    ) -> list[tuple[str, str, int]]:
        parsed = self._parse_target(
            value,
            default_scheme="https",
        )

        if parsed is None:
            return []

        _scheme, host, port = parsed

        # KeenDNS всегда проверяется по HTTPS. Явный нестандартный порт
        # сохраняем, иначе используем стандартный 443.
        explicit_port = self._contains_explicit_port(
            value
        )
        return [("https", host, port if explicit_port else 443)]

    @staticmethod
    def _parse_target(
        value: str,
        default_scheme: str,
    ) -> tuple[str, str, int] | None:
        text = value.strip()

        if not text:
            return None

        if "://" not in text:
            text = f"{default_scheme}://{text}"

        parsed = urlparse(text)
        scheme = (
            parsed.scheme.lower()
            if parsed.scheme
            else default_scheme
        )
        host = str(
            parsed.hostname
            or ""
        ).strip()

        if not host:
            return None

        try:
            port = parsed.port
        except ValueError:
            port = None

        if port is None:
            port = 443 if scheme == "https" else 80

        return scheme, host, port

    @staticmethod
    def _contains_explicit_port(
        value: str,
    ) -> bool:
        text = value.strip()

        if "://" not in text:
            text = f"//{text}"

        parsed = urlparse(text)

        try:
            return parsed.port is not None
        except ValueError:
            return False
