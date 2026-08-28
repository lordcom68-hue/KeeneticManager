from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.debug_log import write_priority_debug
from core.services.runtime_paths import LOGS_DIR
from ui.widgets.status_badge import StatusBadge
from ui.widgets.internet_connection_settings_dialog import (
    InternetConnectionSettingsDialog,
)



def _priority_trace(
    message: str,
    *,
    frequent: bool = False,
) -> None:
    write_priority_debug(
        LOGS_DIR / "priority_debug.log",
        message,
        frequent=frequent,
    )



class InternetConnectionCard(QFrame):
    priority_change_requested = Signal(
        str,
        int,
    )
    expansion_changed = Signal(
        str,
        bool,
    )

    def __init__(
        self,
        connection: dict[str, Any],
        *,
        priority_index: int,
        priority_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.connection: dict[str, Any] = {}
        self.interface_id = ""
        self.expanded = False
        self._details_signature: tuple[
            tuple[str, str],
            ...
        ] = ()
        self._detail_widgets: dict[
            str,
            tuple[QLabel, QLabel],
        ] = {}
        self._detail_captions: tuple[str, ...] = ()

        # Последние корректные значения подробностей.
        # Keenetic иногда пропускает отдельные поля в одном
        # цикле мониторинга. Без кэша строка исчезает, затем
        # появляется снова и меняет высоту карточки.
        self._detail_value_cache: dict[str, str] = {}

        self._updating_priority = False

        self.setObjectName(
            "internetManagementCard"
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            14,
            10,
            14,
            10,
        )
        root.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.white_dot = QLabel()
        self.white_dot.setObjectName(
            "internetWhiteDot"
        )
        self.white_dot.setFixedSize(6, 6)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.title_label = QLabel()
        self.title_label.setObjectName(
            "internetManagementTitle"
        )

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName(
            "internetManagementSubtitle"
        )

        title_box.addWidget(
            self.title_label
        )
        title_box.addWidget(
            self.subtitle_label
        )

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )

        self.ping_check_badge = StatusBadge(
            text="Проверка: нет данных",
            state="neutral",
        )
        self.ping_check_badge.setToolTip(
            "Текущее состояние Ping Check"
        )
        self.ping_check_badge.hide()

        self.priority_button = QPushButton()
        self.priority_button.setObjectName(
            "internetPriorityButton"
        )
        self.priority_button.setFixedWidth(150)
        self.priority_button.setMinimumHeight(30)
        self.priority_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.priority_button.setToolTip(
            "Выбрать порядок использования "
            "интернет-канала"
        )

        self.priority_menu = QMenu(
            self.priority_button
        )
        self.priority_button.setMenu(
            self.priority_menu
        )

        self._priority_count = 1
        self._priority_index = 0

        self.set_priority_options(
            priority_count
        )
        self.set_priority(
            priority_index
        )

        self.toggle_button = QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setArrowType(
            Qt.ArrowType.RightArrow
        )
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.toggle_button.setFixedSize(
            38,
            34,
        )
        self.toggle_button.clicked.connect(
            self._toggle_details
        )

        header.addWidget(
            self.white_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        header.addLayout(title_box, 1)
        header.addWidget(self.ping_check_badge)
        header.addWidget(self.status_badge)
        header.addWidget(self.priority_button)
        header.addWidget(self.toggle_button)

        root.addLayout(header)

        self.details_widget = QWidget(self)
        self.details_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.details_layout = QGridLayout(
            self.details_widget
        )
        self.details_layout.setContentsMargins(
            16,
            4,
            4,
            3,
        )
        self.details_layout.setHorizontalSpacing(
            24
        )
        self.details_layout.setVerticalSpacing(
            2
        )
        self.details_layout.setColumnMinimumWidth(
            0,
            128,
        )
        self.details_layout.setColumnMinimumWidth(
            2,
            128,
        )
        self.details_layout.setColumnStretch(
            1,
            1,
        )
        self.details_layout.setColumnStretch(
            3,
            1,
        )

        root.addWidget(self.details_widget)

        self.animation = QPropertyAnimation(
            self.details_widget,
            b"maximumHeight",
            self,
        )
        self.animation.setDuration(170)
        self.animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self.animation.finished.connect(
            self._animation_finished
        )

        self.details_widget.setMaximumHeight(0)
        self.details_widget.hide()

        self.update_connection(connection)

    def set_priority_options(
        self,
        count: int,
    ) -> None:
        self._priority_count = max(
            1,
            int(count),
        )
        self._priority_index = max(
            0,
            min(
                self._priority_index,
                self._priority_count - 1,
            ),
        )

        self.priority_menu.clear()

        for index in range(
            self._priority_count
        ):
            title = self._priority_title(
                index
            )
            action = self.priority_menu.addAction(
                title
            )
            action.setCheckable(True)
            action.setChecked(
                index == self._priority_index
            )
            action.triggered.connect(
                lambda _checked=False, value=index:
                self._priority_action_triggered(
                    value
                )
            )

        self._update_priority_button_text()

    def set_priority_enabled(
        self,
        enabled: bool,
    ) -> None:
        self.priority_button.setEnabled(enabled)

    def set_priority(
        self,
        index: int,
    ) -> None:
        self._updating_priority = True
        self._priority_index = max(
            0,
            min(
                int(index),
                self._priority_count - 1,
            ),
        )

        for action_index, action in enumerate(
            self.priority_menu.actions()
        ):
            action.setChecked(
                action_index == self._priority_index
            )

        self._update_priority_button_text()
        self._updating_priority = False

    def _priority_action_triggered(
        self,
        index: int,
    ) -> None:
        if self._updating_priority:
            return

        if index == self._priority_index:
            return

        self._priority_index = index

        for action_index, action in enumerate(
            self.priority_menu.actions()
        ):
            action.setChecked(
                action_index == index
            )

        self._update_priority_button_text()
        self._priority_changed(index)

    def _update_priority_button_text(
        self,
    ) -> None:
        self.priority_button.setText(
            self._priority_title(
                self._priority_index
            )
        )

    @staticmethod
    def _priority_title(
        index: int,
    ) -> str:
        if index <= 0:
            return "Основной канал"

        return f"Резервный {index}"

    def update_connection(
        self,
        connection: dict[str, Any],
    ) -> None:
        self.connection = dict(connection)
        self.interface_id = str(
            connection.get("interface_id")
            or connection.get("id")
            or connection.get("name")
            or ""
        ).strip()

        title = str(
            connection.get("name")
            or connection.get("description")
            or connection.get("interface_name")
            or self.interface_id
            or "Интернет-подключение"
        ).strip()

        interface_name = str(
            connection.get("interface_name")
            or connection.get("interface")
            or self.interface_id
            or ""
        ).strip()

        self._set_text(
            self.title_label,
            title,
        )
        self._set_text(
            self.subtitle_label,
            interface_name
            if interface_name != title
            else "",
        )
        self.subtitle_label.setVisible(
            bool(self.subtitle_label.text())
        )

        connected = self._is_connected(
            connection
        )
        enabled = self._is_enabled(
            connection
        )

        if not enabled:
            self.status_badge.set_status(
                text="Выключен",
                state="disconnected",
            )
        elif connected:
            self.status_badge.set_status(
                text="Подключено",
                state="connected",
            )
        else:
            self.status_badge.set_status(
                text="Отключено",
                state="disconnected",
            )

        self._update_ping_check_badge(
            connection
        )

        has_priority = (
            connection.get("global_order")
            is not None
        )
        self.priority_button.setVisible(
            has_priority
        )
        self.priority_button.setEnabled(
            has_priority
        )

        self._set_details(
            self._build_details(connection)
        )

    def _build_details(
        self,
        connection: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        return [
            (
                "Интерфейс",
                self.interface_id,
            ),
            (
                "Тип",
                connection.get("type")
                or connection.get("connection_type"),
            ),
            (
                "Протокол провайдера",
                connection.get("provider_protocol"),
            ),
            (
                "Диапазон Wireless ISP",
                connection.get("wireless_isp_band_title"),
            ),
            (
                "Wi-Fi-сеть",
                connection.get("wireless_isp_ssid")
                or connection.get("ssid"),
            ),
            (
                "BSSID",
                connection.get("wireless_isp_connected_bssid")
                or connection.get("wireless_isp_bssid"),
            ),
            (
                "Сигнал Wireless ISP",
                (
                    str(connection.get("wireless_isp_rssi"))
                    + " dBm"
                    if connection.get("wireless_isp_rssi")
                    not in (None, "")
                    else None
                ),
            ),
            (
                "Канал Wi-Fi",
                connection.get("wireless_isp_channel"),
            ),
            (
                "Транспорт",
                connection.get("transport_interface_id"),
            ),
            (
                "Порт провайдера",
                (
                    "LAN"
                    + str(connection.get("provider_port_label"))
                    if (
                        connection.get("provider_port_label")
                        and not connection.get(
                            "wireless_isp_settings_supported"
                        )
                    )
                    else None
                ),
            ),
            (
                "Пользователь провайдера",
                connection.get("provider_username"),
            ),
            (
                "Сервис PPPoE",
                connection.get("pppoe_service"),
            ),
            (
                "Сервер провайдера",
                connection.get("provider_server"),
            ),
            (
                "IPv4 транспорта",
                connection.get("transport_ipv4"),
            ),
            (
                "IPv4",
                connection.get("address")
                or connection.get("ip")
                or connection.get("ipv4"),
            ),
            (
                "IPv6",
                self._format_ipv6_address(
                    connection.get("ipv6_address")
                    or connection.get("ipv6")
                ),
            ),
            (
                "Шлюз",
                (
                    connection.get("gateway")
                    or connection.get("default_gateway")
                )
                if (
                    connection.get("gateway")
                    or connection.get("default_gateway")
                )
                != connection.get("transport_interface_id")
                else None,
            ),
            (
                "DNS",
                self._format_dns(
                    connection.get("dns")
                    or connection.get("dns_servers")
                ),
            ),
            (
                "Ping Check",
                self._format_ping_check_status(
                    connection
                ),
            ),
            (
                "Профиль проверки",
                self._format_ping_check_profile(
                    connection
                ),
            ),
            (
                "Успехов подряд",
                (
                    self._format_integer(
                        connection.get(
                            "ping_check_success_count"
                        )
                    )
                    if connection.get(
                        "ping_check_enabled"
                    )
                    else None
                ),
            ),
            (
                "Ошибок подряд",
                (
                    self._format_integer(
                        connection.get(
                            "ping_check_fail_count"
                        )
                    )
                    if connection.get(
                        "ping_check_enabled"
                    )
                    else None
                ),
            ),
            (
                "Адреса узла",
                (
                    self._format_dns(
                        connection.get(
                            "ping_check_addresses"
                        )
                    )
                    if connection.get(
                        "ping_check_enabled"
                    )
                    else None
                ),
            ),
            (
                "Последний успех",
                self._format_observed_timestamp(
                    connection.get(
                        "ping_check_last_success"
                    )
                ),
            ),
            (
                "Последняя ошибка",
                self._format_observed_timestamp(
                    connection.get(
                        "ping_check_last_failure"
                    )
                ),
            ),
            (
                "Причина состояния",
                connection.get(
                    "ping_check_state_reason"
                ),
            ),
            (
                "Последнее переключение",
                self._format_switch_event(
                    connection.get(
                        "ping_check_last_switch_reason"
                    ),
                    connection.get(
                        "ping_check_last_switch_at"
                    ),
                ),
            ),
            (
                "MTU",
                connection.get("mtu"),
            ),
            (
                "MAC",
                connection.get("mac")
                or connection.get("mac_address"),
            ),
            (
                "Время подключения",
                connection.get("uptime"),
            ),
            (
                "Скорость линии",
                self._format_rate(
                    connection.get("link_speed_bps")
                    or connection.get("link_speed")
                ),
            ),
            (
                "Приём",
                self._format_rate(
                    connection.get("rxspeed")
                    or connection.get("rx_rate")
                ),
            ),
            (
                "Передача",
                self._format_rate(
                    connection.get("txspeed")
                    or connection.get("tx_rate")
                ),
            ),
            (
                "Получено всего",
                self._format_bytes(
                    connection.get("rxbytes")
                ),
            ),
            (
                "Передано всего",
                self._format_bytes(
                    connection.get("txbytes")
                ),
            ),
            (
                "Пакетов принято",
                self._format_integer(
                    connection.get("rxpackets")
                ),
            ),
            (
                "Пакетов отправлено",
                self._format_integer(
                    connection.get("txpackets")
                ),
            ),
            (
                "Порядок канала",
                connection.get("global_order"),
            ),
        ]

    def _set_details(
        self,
        details: list[tuple[str, Any]],
    ) -> None:
        visible: list[tuple[str, str]] = []

        for caption, value in details:
            caption_text = str(caption)

            if not self._is_empty(value):
                value_text = str(value)
                self._detail_value_cache[
                    caption_text
                ] = value_text
                visible.append(
                    (
                        caption_text,
                        value_text,
                    )
                )
                continue

            # Если поле уже хотя бы один раз было получено,
            # временное отсутствие в новом ответе RCI не
            # удаляет строку из карточки.
            cached_value = self._detail_value_cache.get(
                caption_text
            )

            if cached_value is not None:
                visible.append(
                    (
                        caption_text,
                        cached_value,
                    )
                )

        captions = tuple(
            caption
            for caption, _value in visible
        )
        signature = tuple(visible)

        if signature == self._details_signature:
            return

        self._details_signature = signature

        # Если набор полей не изменился, обновляем только текст.
        # QLabel и layout не пересоздаются, поэтому карточка
        # не дёргается при обновлении скорости и аптайма.
        if captions == self._detail_captions:
            for caption, value in visible:
                widgets = self._detail_widgets.get(
                    caption
                )

                if widgets is None:
                    continue

                _caption_label, value_label = widgets

                if value_label.text() != value:
                    value_label.setText(value)

            return

        self._detail_captions = captions
        self._clear_details()

        midpoint = (
            len(visible) + 1
        ) // 2

        left = visible[:midpoint]
        right = visible[midpoint:]

        for row in range(
            max(
                len(left),
                len(right),
            )
        ):
            if row < len(left):
                self._add_detail(
                    row,
                    0,
                    left[row][0],
                    left[row][1],
                )

            if row < len(right):
                self._add_detail(
                    row,
                    2,
                    right[row][0],
                    right[row][1],
                )

        if self.expanded:
            QTimer.singleShot(
                0,
                self._restore_expanded_height,
            )

    def _restore_expanded_height(
        self,
    ) -> None:
        if not self.expanded:
            return

        self.details_widget.show()
        self.details_layout.invalidate()
        self.details_layout.activate()

        height = max(
            1,
            self.details_layout.sizeHint().height(),
            self.details_widget.sizeHint().height(),
        )

        if (
            self.details_widget.maximumHeight()
            != height
        ):
            self.details_widget.setMinimumHeight(
                height
            )
            self.details_widget.setMaximumHeight(
                height
            )
            self.updateGeometry()

    def _add_detail(
        self,
        row: int,
        column: int,
        caption_text: str,
        value_text: str,
    ) -> None:
        caption = QLabel(
            caption_text,
            self.details_widget,
        )
        caption.setObjectName(
            "internetDetailCaption"
        )

        # Ширина подписи рассчитывается по фактическому
        # шрифту и DPI. Раньше фиксированные 128 px
        # обрезали длинные строки вроде
        # «Пользователь провайдера».
        caption.ensurePolished()
        caption_width = max(
            128,
            caption.sizeHint().width() + 8,
        )
        caption.setFixedWidth(
            caption_width
        )

        value = QLabel(
            value_text,
            self.details_widget,
        )
        value.setObjectName(
            "internetDetailValue"
        )
        value.setWordWrap(True)
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.details_layout.addWidget(
            caption,
            row,
            column,
        )
        self.details_layout.addWidget(
            value,
            row,
            column + 1,
        )

        self._detail_widgets[
            caption_text
        ] = (
            caption,
            value,
        )

    def _priority_changed(
        self,
        index: int,
    ) -> None:
        if self._updating_priority:
            return

        self.priority_change_requested.emit(
            self.interface_id,
            index,
        )

    def _toggle_details(
        self,
        checked: bool,
    ) -> None:
        # Каждая карточка раскрывается независимо.
        # Изменение высоты выполняется мгновенно, потому что
        # QPropertyAnimation внутри QScrollArea вызывает рывки
        # всей страницы и полосы прокрутки.
        self.set_expanded(
            checked,
            animated=False,
        )
        self.expansion_changed.emit(
            self.interface_id,
            checked,
        )

    def set_expanded(
        self,
        expanded: bool,
        *,
        animated: bool = False,
    ) -> None:
        del animated

        expanded = bool(expanded)

        if (
            self.expanded == expanded
            and self.toggle_button.isChecked()
            == expanded
        ):
            return

        self.expanded = expanded
        self.animation.stop()

        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(
            expanded
        )
        self.toggle_button.blockSignals(False)

        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if expanded
            else Qt.ArrowType.RightArrow
        )

        if expanded:
            self.details_widget.show()
            self.details_layout.invalidate()
            self.details_layout.activate()
            self.details_widget.adjustSize()

            height = max(
                1,
                self.details_layout.sizeHint().height(),
                self.details_widget.sizeHint().height(),
            )

            self.details_widget.setMinimumHeight(
                height
            )
            self.details_widget.setMaximumHeight(
                height
            )
        else:
            self.details_widget.setMinimumHeight(0)
            self.details_widget.setMaximumHeight(0)
            self.details_widget.hide()

        self.updateGeometry()

    def _animation_finished(self) -> None:
        # Оставлено для совместимости с созданным объектом
        # QPropertyAnimation. Раскрытие выполняется без него.
        return

    def _details_height(self) -> int:
        self.details_layout.activate()
        return max(
            1,
            self.details_layout.sizeHint().height(),
        )

    def _clear_details(self) -> None:
        self._detail_widgets = {}

        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _is_connected(
        connection: dict[str, Any],
    ) -> bool:
        value = (
            connection.get("connected")
            or connection.get("link")
            or connection.get("status")
        )

        if isinstance(value, bool):
            return value

        return str(value or "").strip().lower() in {
            "yes",
            "true",
            "up",
            "online",
            "connected",
            "running",
        }

    @staticmethod
    def _is_enabled(
        connection: dict[str, Any],
    ) -> bool:
        value = connection.get("enabled")

        if value is None:
            state = str(
                connection.get("state")
                or ""
            ).strip().lower()
            return state not in {
                "disabled",
                "off",
            }

        if isinstance(value, bool):
            return value

        return str(value).strip().lower() not in {
            "no",
            "false",
            "0",
            "off",
            "disabled",
        }

    def _update_ping_check_badge(
        self,
        connection: dict[str, Any],
    ) -> None:
        enabled = bool(
            connection.get(
                "ping_check_enabled"
            )
        )

        if not enabled:
            self.ping_check_badge.hide()

            for caption in (
                "Профиль проверки",
                "Успехов подряд",
                "Ошибок подряд",
                "Адреса узла",
                "Последний успех",
                "Последняя ошибка",
            ):
                self._detail_value_cache.pop(
                    caption,
                    None,
                )

            return

        status = str(
            connection.get(
                "ping_check_runtime_status"
            )
            or "unknown"
        ).strip().lower()

        if status == "pass":
            text = "Проверка: OK"
            state = "connected"
        elif status == "fail":
            text = "Проверка: сбой"
            state = "error"
        elif status == "pending":
            text = "Проверка: ожидание"
            state = "waiting"
        else:
            text = "Проверка: нет данных"
            state = "neutral"

        self.ping_check_badge.set_status(
            text=text,
            state=state,
        )
        self.ping_check_badge.show()

    @staticmethod
    def _format_ping_check_status(
        connection: dict[str, Any],
    ) -> str:
        if not connection.get(
            "ping_check_enabled"
        ):
            return "Выключен"

        text = str(
            connection.get(
                "ping_check_runtime_text"
            )
            or "Нет данных"
        ).strip()

        if connection.get(
            "ping_check_ignore_fail"
        ):
            text += " (ошибки игнорируются)"

        return text

    @staticmethod
    def _format_ping_check_profile(
        connection: dict[str, Any],
    ) -> str | None:
        if not connection.get(
            "ping_check_enabled"
        ):
            return None

        mode = str(
            connection.get(
                "ping_check_mode"
            )
            or ""
        ).strip().lower()

        mode_text = {
            "automatic": "Автоматически",
            "icmp": "ICMP",
            "tls": "TCP/TLS",
            "connect": "TCP",
            "custom": "Существующий профиль",
        }.get(
            mode,
            mode or "Неизвестный режим",
        )

        profile = str(
            connection.get(
                "ping_check_profile"
            )
            or ""
        ).strip()

        if profile:
            return f"{mode_text} ({profile})"

        return mode_text

    @staticmethod
    def _format_observed_timestamp(
        value: Any,
    ) -> str | None:
        text = str(
            value or ""
        ).strip()

        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(
                text
            )
        except ValueError:
            return text

        return parsed.strftime(
            "%d.%m.%Y %H:%M:%S"
        )

    @classmethod
    def _format_switch_event(
        cls,
        reason: Any,
        timestamp: Any,
    ) -> str | None:
        reason_text = str(
            reason or ""
        ).strip()

        if not reason_text:
            return None

        time_text = cls._format_observed_timestamp(
            timestamp
        )

        if time_text:
            return (
                f"{time_text} — {reason_text}"
            )

        return reason_text

    @staticmethod
    def _format_ipv6_address(
        value: Any,
    ) -> str | None:
        """
        Возвращает только настоящий IPv6-адрес.

        Булевы флаги игнорируются. Вложенные структуры
        Keenetic вида ipv6 -> addresses -> address разбираются.
        """
        global_addresses: list[str] = []
        link_local_addresses: list[str] = []

        def collect(candidate: Any) -> None:
            if candidate is None or isinstance(candidate, bool):
                return

            if isinstance(candidate, dict):
                # Сначала проверяем поля, в которых Keenetic
                # обычно хранит сам адрес.
                for key in (
                    "address",
                    "ipv6-address",
                    "ipv6_address",
                    "global-address",
                    "global_address",
                ):
                    if key in candidate:
                        collect(candidate.get(key))

                for key, nested in candidate.items():
                    if key in {
                        "address",
                        "ipv6-address",
                        "ipv6_address",
                        "global-address",
                        "global_address",
                    }:
                        continue
                    collect(nested)
                return

            if isinstance(candidate, (list, tuple, set)):
                for nested in candidate:
                    collect(nested)
                return

            if not isinstance(candidate, str):
                return

            for part in candidate.replace(
                "\n",
                " ",
            ).replace(
                ",",
                " ",
            ).replace(
                ";",
                " ",
            ).split():
                address_text = (
                    part.strip("[]() ")
                    .split("/", 1)[0]
                    .split("%", 1)[0]
                    .strip()
                )

                try:
                    address = ipaddress.ip_address(
                        address_text
                    )
                except ValueError:
                    continue

                if not isinstance(
                    address,
                    ipaddress.IPv6Address,
                ):
                    continue

                normalized = str(address)

                if address.is_link_local:
                    if normalized not in link_local_addresses:
                        link_local_addresses.append(normalized)
                elif normalized not in global_addresses:
                    global_addresses.append(normalized)

        collect(value)

        if global_addresses:
            return global_addresses[0]

        if link_local_addresses:
            return link_local_addresses[0]

        return None

    @staticmethod
    def _format_dns(
        value: Any,
    ) -> str | None:
        if isinstance(value, (list, tuple, set)):
            items = [
                str(item).strip()
                for item in value
                if str(item).strip()
            ]
            return ", ".join(items) or None

        if isinstance(value, dict):
            items = [
                str(item).strip()
                for item in value.values()
                if str(item).strip()
            ]
            return ", ".join(items) or None

        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _format_bytes(
        value: Any,
    ) -> str | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if number < 0:
            return None

        units = (
            "Б",
            "КБ",
            "МБ",
            "ГБ",
            "ТБ",
        )
        index = 0

        while number >= 1024 and index < len(units) - 1:
            number /= 1024
            index += 1

        if index == 0:
            return f"{number:.0f} {units[index]}"

        return f"{number:.2f} {units[index]}"

    @staticmethod
    def _format_integer(
        value: Any,
    ) -> str | None:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None

        if number < 0:
            return None

        return f"{number:,}".replace(",", " ")

    @staticmethod
    def _format_rate(
        value: Any,
    ) -> str | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if number < 0:
            return None

        if number == 0:
            return "0 бит/с"

        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f} Мбит/с"

        if number >= 1000:
            return f"{number / 1000:.1f} Кбит/с"

        return f"{number:.0f} бит/с"

    @staticmethod
    def _is_empty(
        value: Any,
    ) -> bool:
        if value is None:
            return True

        return str(value).strip() in {
            "",
            "-",
            "—",
            "None",
            "null",
            "[]",
            "{}",
        }

    @staticmethod
    def _set_text(
        label: QLabel,
        value: str,
    ) -> None:
        if label.text() != value:
            label.setText(value)





class InternetManagementItem(QFrame):
    """
    Элемент списка управления подключениями.
    """

    state_change_requested = Signal(
        str,
        str,
        bool,
    )
    reconnect_requested = Signal(
        str,
        str,
    )
    settings_change_requested = Signal(
        str,
        str,
        object,
    )
    protocol_change_requested = Signal(
        str,
        str,
        object,
    )
    delete_requested = Signal(
        str,
        str,
        object,
    )

    def __init__(
        self,
        connection: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.connection = dict(connection)
        self.interface_id = ""
        self.display_name = "Интернет-подключение"
        self.enabled = True
        self._action_pending = False

        self.setObjectName(
            "internetManagementItem"
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(
            12,
            9,
            12,
            9,
        )
        root.setSpacing(10)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)

        self.title_label = QLabel()
        self.title_label.setObjectName(
            "internetManagementItemTitle"
        )

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName(
            "internetManagementItemSubtitle"
        )

        text_box.addWidget(
            self.title_label
        )
        text_box.addWidget(
            self.subtitle_label
        )

        self.status_badge = StatusBadge(
            text="Отключено",
            state="disconnected",
        )

        self.settings_button = QPushButton(
            "Настроить"
        )
        self.reconnect_button = QPushButton(
            "Переподключить"
        )
        self.toggle_button = QPushButton(
            "Выключить"
        )
        self.delete_button = QPushButton(
            "Удалить"
        )
        self.delete_button.setObjectName("dangerButton")

        for button in (
            self.settings_button,
            self.reconnect_button,
            self.toggle_button,
            self.delete_button,
        ):
            button.setMinimumHeight(30)

        self.settings_button.setToolTip(
            "Открыть параметры подключения"
        )
        self.settings_button.clicked.connect(
            self._open_settings
        )
        self.reconnect_button.setToolTip(
            "Отключить и повторно включить подключение"
        )
        self.reconnect_button.clicked.connect(
            self._reconnect_requested
        )
        self.toggle_button.setToolTip(
            "Включить или выключить подключение"
        )
        self.toggle_button.clicked.connect(
            self._toggle_requested
        )
        self.delete_button.setToolTip(
            "Удалить поддерживаемое кабельное подключение и "
            "вернуть LAN-порт в домашнюю сеть"
        )
        self.delete_button.clicked.connect(
            self._delete_requested
        )

        root.addLayout(text_box, 1)
        root.addWidget(self.status_badge)
        root.addWidget(self.settings_button)
        root.addWidget(self.reconnect_button)
        root.addWidget(self.toggle_button)
        root.addWidget(self.delete_button)

        self.update_connection(connection)

    def update_connection(
        self,
        connection: dict[str, Any],
    ) -> None:
        self.connection = dict(connection)

        interface_id = str(
            connection.get("interface_id")
            or connection.get("id")
            or connection.get("name")
            or ""
        ).strip()
        self.interface_id = interface_id

        title = str(
            connection.get("name")
            or connection.get("description")
            or connection.get("interface_name")
            or interface_id
            or "Интернет-подключение"
        ).strip()

        connection_type = str(
            connection.get("type")
            or connection.get("connection_type")
            or "Подключение"
        ).strip()

        self.display_name = title
        self.title_label.setText(title)

        subtitle_parts = [
            item
            for item in (
                connection_type,
                interface_id
                if interface_id != title
                else "",
            )
            if item
        ]

        self.subtitle_label.setText(
            " · ".join(subtitle_parts)
        )
        self.subtitle_label.setVisible(
            bool(self.subtitle_label.text())
        )

        enabled = InternetConnectionCard._is_enabled(
            connection
        )
        self.enabled = enabled
        connected = (
            InternetConnectionCard._is_connected(
                connection
            )
        )

        if not enabled:
            self.status_badge.set_status(
                text="Выключен",
                state="disconnected",
            )
            self.toggle_button.setText(
                "Включить"
            )
        elif connected:
            self.status_badge.set_status(
                text="Подключено",
                state="connected",
            )
            self.toggle_button.setText(
                "Выключить"
            )
        else:
            self.status_badge.set_status(
                text="Отключено",
                state="disconnected",
            )
            self.toggle_button.setText(
                "Выключить"
            )

        self._action_pending = False
        has_interface = bool(self.interface_id)
        delete_supported = bool(
            connection.get("delete_supported")
        )
        self.delete_button.setVisible(
            delete_supported
            or not self._is_vpn_connection(connection)
        )
        protocol_change_supported = bool(
            connection.get("protocol_change_supported")
        )
        protocol_change_reason = str(
            connection.get("protocol_change_reason")
            or ""
        ).strip()
        delete_reason = str(
            connection.get("delete_reason")
            or ""
        ).strip()

        self.reconnect_button.setText(
            "Переподключить"
        )
        self.toggle_button.setText(
            "Включить"
            if not enabled
            else "Выключить"
        )
        self.delete_button.setText("Удалить")

        self.settings_button.setEnabled(
            has_interface
        )
        self.settings_button.setToolTip(
            (
                "Открыть параметры WAN. Внутри можно сменить "
                "IPoE, PPPoE, PPTP или L2TP без переназначения "
                "физического порта."
                if protocol_change_supported
                else (
                    "Открыть параметры подключения. "
                    + (
                        f"Смена типа недоступна: "
                        f"{protocol_change_reason}"
                        if protocol_change_reason
                        else ""
                    )
                ).strip()
            )
        )
        self.toggle_button.setEnabled(
            has_interface
        )
        self.reconnect_button.setEnabled(
            has_interface and enabled
        )
        self.delete_button.setEnabled(
            has_interface and delete_supported
        )
        if delete_supported:
            port_label = str(
                connection.get("delete_port_label")
                or "?"
            )
            link_known = connection.get(
                "delete_port_link_known"
            )
            link_active = bool(
                connection.get("delete_port_link_active")
            )

            delete_kind = str(
                connection.get("delete_kind") or ""
            ).strip().lower()

            if delete_kind == "ethernet.dedicated":
                protocol_title = str(
                    connection.get("provider_protocol")
                    or connection.get("type")
                    or "IPoE"
                ).strip()
                delete_tooltip = (
                    f"Удалить настройки {protocol_title} и перевести "
                    "физический WAN-порт в домашнюю сеть как LAN0"
                )
            elif delete_kind in {
                "ethernet.pppoe",
                "ethernet.pptp",
                "ethernet.l2tp",
            }:
                protocol_title = {
                    "ethernet.pppoe": "PPPoE",
                    "ethernet.pptp": "PPTP",
                    "ethernet.l2tp": "L2TP",
                }[delete_kind]
                delete_tooltip = (
                    f"Удалить {protocol_title} вместе с транспортом "
                    "и вернуть LAN-порт в домашнюю сеть"
                )
            elif delete_kind == "wireless.isp":
                band_title = str(
                    connection.get("wireless_isp_band_title")
                    or "выбранного диапазона"
                ).strip()
                ssid = str(
                    connection.get("wireless_isp_ssid")
                    or connection.get("ssid")
                    or ""
                ).strip()
                delete_tooltip = (
                    f"Удалить настройки Wireless ISP {band_title}"
                    + (f" для сети «{ssid}»" if ssid else "")
                    + " и освободить этот Wi-Fi-диапазон для нового подключения"
                )
            else:
                delete_tooltip = (
                    "Удалить Проводное IPoE и вернуть LAN-порт "
                    "в домашнюю сеть"
                )

            port_display_name = str(
                connection.get("delete_port_display_name")
                or (
                    "WAN (порт "
                    + str(connection.get("delete_port_number") or "0")
                    + ")"
                    if delete_kind == "ethernet.dedicated"
                    else "LAN" + port_label
                )
            )

            # Физический Ethernet-линк относится только к проводным
            # подключениям. Wireless ISP не связан с LAN/WAN-портом,
            # поэтому его подсказка не должна упоминать кабель.
            if delete_kind != "wireless.isp":
                if link_active:
                    delete_tooltip += (
                        f". На {port_display_name} подключён кабель; "
                        "операция потребует дополнительного подтверждения"
                    )
                elif link_known is False:
                    delete_tooltip += (
                        ". Состояние кабеля неизвестно; операция потребует "
                        "дополнительного подтверждения"
                    )
        else:
            delete_tooltip = (
                delete_reason
                or (
                    "Удаление этого типа подключения "
                    "пока не поддерживается"
                )
            )

        self.delete_button.setToolTip(
            delete_tooltip
        )

    @staticmethod
    def _is_vpn_connection(
        connection: dict[str, Any],
    ) -> bool:
        text = " ".join(
            str(
                connection.get(key)
                or ""
            )
            for key in (
                "interface_id",
                "id",
                "type",
                "connection_type",
                "name",
                "interface_name",
                "description",
            )
        ).lower()
        return any(
            marker in text
            for marker in (
                "openvpn",
                "wireguard",
                "zerotier",
                "ikev2",
                "openconnect",
                "l2tp",
                "pptp",
                "sstp",
                "ipsec",
                "gre",
                "vpn",
            )
        )

    def _open_settings(self) -> None:
        if self._action_pending or not self.interface_id:
            return

        dialog = InternetConnectionSettingsDialog(
            self.connection,
            self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        protocol_settings = (
            dialog.protocol_change_settings()
        )

        if protocol_settings is not None:
            self._action_pending = True
            self.settings_button.setText(
                "Выполняется..."
            )
            self.settings_button.setEnabled(False)
            self.reconnect_button.setEnabled(False)
            self.toggle_button.setEnabled(False)
            self.delete_button.setEnabled(False)

            request_details = dict(self.connection)
            request_details[
                "_requested_protocol_settings"
            ] = protocol_settings

            self.protocol_change_requested.emit(
                self.interface_id,
                self.display_name,
                request_details,
            )
            return

        changes = dialog.changes()

        if not changes:
            return

        self.settings_change_requested.emit(
            self.interface_id,
            self.display_name,
            changes,
        )

    def finish_protocol_change_action(self) -> None:
        self._action_pending = False
        self.settings_button.setText("Настроить")
        has_interface = bool(self.interface_id)
        delete_supported = bool(
            self.connection.get("delete_supported")
        )
        self.settings_button.setEnabled(has_interface)
        self.toggle_button.setEnabled(has_interface)
        self.reconnect_button.setEnabled(
            has_interface and self.enabled
        )
        self.delete_button.setEnabled(
            has_interface and delete_supported
        )

    def _reconnect_requested(self) -> None:
        if (
            self._action_pending
            or not self.interface_id
            or not self.enabled
        ):
            return

        self._action_pending = True
        self.settings_button.setEnabled(False)
        self.reconnect_button.setEnabled(False)
        self.toggle_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.reconnect_button.setText(
            "Выполняется..."
        )

        self.reconnect_requested.emit(
            self.interface_id,
            self.display_name,
        )

    def finish_reconnect_action(self) -> None:
        """
        Немедленно возвращает кнопки из состояния ожидания.

        Следующий refresh всё равно подтвердит фактическое
        состояние интерфейса, но UI больше не зависит от него.
        """
        self._action_pending = False
        self.reconnect_button.setText(
            "Переподключить"
        )
        self.toggle_button.setText(
            "Включить"
            if not self.enabled
            else "Выключить"
        )

        has_interface = bool(self.interface_id)
        delete_supported = bool(
            self.connection.get("delete_supported")
        )
        self.settings_button.setEnabled(
            has_interface
        )
        self.toggle_button.setEnabled(
            has_interface
        )
        self.reconnect_button.setEnabled(
            has_interface and self.enabled
        )
        self.delete_button.setEnabled(
            has_interface and delete_supported
        )

    def _toggle_requested(self) -> None:
        if self._action_pending or not self.interface_id:
            return

        target_enabled = not self.enabled
        self._action_pending = True
        self.toggle_button.setEnabled(False)
        self.toggle_button.setText(
            "Выполняется..."
        )

        self.state_change_requested.emit(
            self.interface_id,
            self.display_name,
            target_enabled,
        )

    def _delete_requested(self) -> None:
        if (
            self._action_pending
            or not self.interface_id
            or not self.connection.get(
                "delete_supported"
            )
        ):
            return

        self._action_pending = True
        self.settings_button.setEnabled(False)
        self.reconnect_button.setEnabled(False)
        self.toggle_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.delete_button.setText("Удаление...")

        self.delete_requested.emit(
            self.interface_id,
            self.display_name,
            dict(self.connection),
        )

    def finish_delete_action(self) -> None:
        self._action_pending = False
        self.delete_button.setText("Удалить")
        has_interface = bool(self.interface_id)
        delete_supported = bool(
            self.connection.get("delete_supported")
        )
        self.settings_button.setEnabled(
            has_interface
        )
        self.toggle_button.setEnabled(
            has_interface
        )
        self.reconnect_button.setEnabled(
            has_interface and self.enabled
        )
        self.delete_button.setEnabled(
            has_interface and delete_supported
        )


class InternetTab(QWidget):
    """
    Вкладка управления интернет-подключениями.

    Приоритеты уникальны внутри одного роутера. При выборе
    занятого приоритета два подключения автоматически меняются
    местами.
    """

    priorities_changed = Signal(dict)
    interface_state_change_requested = Signal(
        str,
        str,
        bool,
    )
    interface_reconnect_requested = Signal(
        str,
        str,
    )
    interface_settings_change_requested = Signal(
        str,
        str,
        object,
    )
    connection_delete_requested = Signal(
        str,
        str,
        object,
    )
    connection_protocol_change_requested = Signal(
        str,
        str,
        object,
    )
    connection_creation_requested = Signal(
        str,
        str,
    )

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.router_id: int | None = None
        self.cards: dict[
            str,
            InternetConnectionCard,
        ] = {}
        self.priorities: dict[
            str,
            int,
        ] = {}
        self.api_priorities: dict[
            str,
            int,
        ] = {}
        self._confirmed_priorities: dict[
            str,
            int,
        ] = {}
        self._change_pending = False
        self.expansion_states: dict[
            str,
            bool,
        ] = {}
        self.management_items: dict[
            str,
            InternetManagementItem,
        ] = {}
        self.management_expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        content = QWidget()
        self.content_layout = QVBoxLayout(
            content
        )
        self.content_layout.setContentsMargins(
            12,
            12,
            12,
            12,
        )
        self.content_layout.setSpacing(12)

        self.top_card = QFrame()
        self.top_card.setObjectName(
            "internetSummaryCard"
        )

        top_root = QVBoxLayout(
            self.top_card
        )
        top_root.setContentsMargins(
            16,
            12,
            16,
            12,
        )
        top_root.setSpacing(10)

        summary_header = QHBoxLayout()
        summary_header.setSpacing(10)

        white_dot = QLabel()
        white_dot.setObjectName(
            "internetWhiteDot"
        )
        white_dot.setFixedSize(7, 7)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        self.summary_title = QLabel(
            "Интернет"
        )
        self.summary_title.setObjectName(
            "internetSummaryTitle"
        )

        self.summary_text = QLabel(
            "Роутер не подключён"
        )
        self.summary_text.setObjectName(
            "internetSummaryText"
        )

        title_box.addWidget(
            self.summary_title
        )
        title_box.addWidget(
            self.summary_text
        )

        self.summary_toggle_button = QToolButton()
        self.summary_toggle_button.setCheckable(True)
        self.summary_toggle_button.setArrowType(
            Qt.ArrowType.RightArrow
        )
        self.summary_toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.summary_toggle_button.setFixedSize(
            38,
            34,
        )
        self.summary_toggle_button.setToolTip(
            "Показать управление подключениями"
        )
        self.summary_toggle_button.clicked.connect(
            self._toggle_management_panel
        )

        summary_header.addWidget(
            white_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        summary_header.addLayout(
            title_box,
            1,
        )
        summary_header.addWidget(
            self.summary_toggle_button
        )

        top_root.addLayout(
            summary_header
        )

        self.summary_metrics = QWidget(
            self.top_card
        )
        metrics_layout = QGridLayout(
            self.summary_metrics
        )
        metrics_layout.setContentsMargins(
            17,
            4,
            4,
            0,
        )
        metrics_layout.setHorizontalSpacing(18)
        metrics_layout.setVerticalSpacing(4)

        metric_names = (
            "Состояние",
            "Подключений",
            "Активных",
            "Основной",
            "Резервный",
            "Публичный IP",
        )

        self.summary_metric_values: dict[
            str,
            QLabel,
        ] = {}

        for row, metric_name in enumerate(
            metric_names
        ):
            name_label = QLabel(metric_name)
            name_label.setObjectName(
                "internetSummaryMetricName"
            )
            name_label.setFixedWidth(116)

            value_label = QLabel("—")
            value_label.setObjectName(
                "internetSummaryMetricValue"
            )
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

            metrics_layout.addWidget(
                name_label,
                row,
                0,
            )
            metrics_layout.addWidget(
                value_label,
                row,
                1,
            )

            self.summary_metric_values[
                metric_name
            ] = value_label

        metrics_layout.setColumnStretch(
            1,
            1,
        )

        top_root.addWidget(
            self.summary_metrics
        )

        self.management_panel = QWidget(
            self.top_card
        )
        self.management_panel.setObjectName(
            "internetManagementPanel"
        )
        management_layout = QVBoxLayout(
            self.management_panel
        )
        management_layout.setContentsMargins(
            17,
            8,
            4,
            2,
        )
        management_layout.setSpacing(8)

        management_header = QHBoxLayout()

        management_title = QLabel(
            "Управление подключениями"
        )
        management_title.setObjectName(
            "internetManagementPanelTitle"
        )

        self.create_connection_button = QPushButton(
            "Создать подключение"
        )
        self.create_connection_button.setObjectName(
            "primaryButton"
        )
        self.create_connection_button.setMinimumHeight(
            32
        )
        self.create_connection_button.setToolTip(
            "Выберите тип нового подключения"
        )

        self.create_connection_menu = QMenu(
            self.create_connection_button
        )
        self._populate_create_connection_menu()
        self.create_connection_button.setMenu(
            self.create_connection_menu
        )

        management_header.addWidget(
            management_title,
            1,
        )
        management_header.addWidget(
            self.create_connection_button
        )

        management_layout.addLayout(
            management_header
        )

        self.management_items_container = QWidget(
            self.management_panel
        )
        self.management_items_layout = QVBoxLayout(
            self.management_items_container
        )
        self.management_items_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        self.management_items_layout.setSpacing(
            7
        )

        management_layout.addWidget(
            self.management_items_container
        )

        self.management_empty_label = QLabel(
            "Нет подключений для управления"
        )
        self.management_empty_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.management_empty_label.setMinimumHeight(
            54
        )
        management_layout.addWidget(
            self.management_empty_label
        )

        self.management_panel.hide()
        top_root.addWidget(
            self.management_panel
        )

        self.connections_container = QWidget()
        self.connections_layout = QVBoxLayout(
            self.connections_container
        )
        self.connections_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        self.connections_layout.setSpacing(10)

        self.empty_label = QLabel()
        self.empty_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.empty_label.setMinimumHeight(150)

        self.content_layout.addWidget(
            self.top_card
        )
        self.content_layout.addWidget(
            self.connections_container
        )
        self.content_layout.addWidget(
            self.empty_label
        )
        self.content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        # Всегда резервируем место под вертикальную полосу.
        # Иначе при раскрытии нескольких карточек scrollbar
        # появляется внезапно, уменьшает ширину страницы и
        # вызывает заметный рывок всего интерфейса.
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setWidget(content)

        root.addWidget(scroll)

        self.setStyleSheet(
            """
            QFrame#internetSummaryCard,
            QFrame#internetManagementCard {
                background: #151e2d;
                border: 1px solid #283548;
                border-radius: 12px;
            }

            QLabel#internetWhiteDot {
                background: #60a5fa;
                border: none;
                border-radius: 3px;
            }

            QLabel#internetSummaryTitle {
                font-size: 20px;
                font-weight: 700;
            }

            QLabel#internetSummaryText {
                font-size: 13px;
            }

            QLabel#internetSummaryMetricName {
                font-size: 12px;
                color: #94a3b8;
            }

            QLabel#internetSummaryMetricValue {
                font-size: 12px;
                color: #e2e8f0;
                font-weight: 600;
            }

            QLabel#internetManagementPanelTitle {
                font-size: 14px;
                font-weight: 700;
            }

            QFrame#internetManagementItem {
                background: rgba(11, 18, 32, 46);
                border: 1px solid #283548;
                border-radius: 8px;
            }

            QLabel#internetManagementItemTitle {
                font-size: 14px;
                font-weight: 700;
            }

            QLabel#internetManagementItemSubtitle {
                font-size: 11px;
                color: #94a3b8;
            }

            QLabel#internetManagementTitle {
                font-size: 16px;
                font-weight: 700;
            }

            QLabel#internetManagementSubtitle {
                font-size: 12px;
            }

            QLabel#internetDetailCaption {
                color: #94a3b8;
                font-size: 11px;
            }

            QLabel#internetDetailValue {
                color: #e2e8f0;
                font-size: 11px;
            }
            """
        )

        self.clear()

    def _populate_create_connection_menu(
        self,
    ) -> None:
        self.create_connection_menu.clear()

        # Кабельные протоколы выбираются внутри единого мастера,
        # как в оригинальном веб-интерфейсе Keenetic.
        self._add_connection_menu_action(
            self.create_connection_menu,
            "Кабельный Интернет",
            "ethernet.cable",
        )

        # Wireless ISP открывает единый мастер. Диапазон
        # 2,4 или 5 ГГц выбирается уже внутри него.
        self._add_connection_menu_action(
            self.create_connection_menu,
            "Wireless ISP",
            "wireless.isp",
        )

        # Мобильное подключение также является прямым пунктом.
        # Конкретный тип модема будет выбираться внутри будущего
        # единого мастера мобильного Интернета.
        self._add_connection_menu_action(
            self.create_connection_menu,
            "Мобильное подключение",
            "mobile.connection",
        )

        # Отдельные VPN-мастера ещё не реализованы. Не показываем
        # подменю из неработающих вариантов: прямой пункт открывает
        # то же информационное сообщение, что мобильное подключение.
        self._add_connection_menu_action(
            self.create_connection_menu,
            "Другие подключения",
            "other.connections",
        )

    def _add_connection_menu_action(
        self,
        menu: QMenu,
        title: str,
        connection_type: str,
    ) -> None:
        action = menu.addAction(title)
        action.setData(connection_type)
        action.setToolTip(
            f"Создать подключение: {title}"
        )
        action.triggered.connect(
            lambda _checked=False,
            type_id=connection_type,
            display_title=title: (
                self.connection_creation_requested.emit(
                    type_id,
                    display_title,
                )
            )
        )

    def _toggle_management_panel(
        self,
        checked: bool,
    ) -> None:
        self.management_expanded = bool(
            checked
        )
        self.management_panel.setVisible(
            self.management_expanded
        )
        self.summary_toggle_button.setArrowType(
            Qt.ArrowType.DownArrow
            if self.management_expanded
            else Qt.ArrowType.RightArrow
        )
        self.summary_toggle_button.setToolTip(
            (
                "Скрыть управление подключениями"
                if self.management_expanded
                else "Показать управление подключениями"
            )
        )

    def _update_management_items(
        self,
        connections: list[dict[str, Any]],
        interface_ids: list[str],
    ) -> None:
        incoming = {
            interface_id: connection
            for interface_id, connection in zip(
                interface_ids,
                connections, strict=False,
            )
        }

        for interface_id in list(
            self.management_items
        ):
            if interface_id in incoming:
                continue

            item = self.management_items.pop(
                interface_id
            )
            self.management_items_layout.removeWidget(
                item
            )
            item.deleteLater()

        for position, interface_id in enumerate(
            interface_ids
        ):
            connection = incoming[
                interface_id
            ]
            item = self.management_items.get(
                interface_id
            )

            if item is None:
                item = InternetManagementItem(
                    connection,
                    self.management_items_container,
                )
                item.state_change_requested.connect(
                    self.interface_state_change_requested
                )
                item.reconnect_requested.connect(
                    self.interface_reconnect_requested
                )
                item.settings_change_requested.connect(
                    self.interface_settings_change_requested
                )
                item.protocol_change_requested.connect(
                    self.connection_protocol_change_requested
                )
                item.delete_requested.connect(
                    self.connection_delete_requested
                )
                self.management_items[
                    interface_id
                ] = item
            else:
                item.update_connection(
                    connection
                )

            self.management_items_layout.insertWidget(
                position,
                item,
            )

        self.management_empty_label.setVisible(
            not connections
        )
        self.management_items_container.setVisible(
            bool(connections)
        )

    def finish_interface_reconnect(
        self,
        interface_id: str,
    ) -> None:
        item = self.management_items.get(
            str(interface_id)
        )

        if item is not None:
            item.finish_reconnect_action()

    def finish_protocol_change(
        self,
        interface_id: str,
    ) -> None:
        item = self.management_items.get(
            str(interface_id)
        )

        if item is not None:
            item.finish_protocol_change_action()

    def finish_connection_delete(
        self,
        interface_id: str,
    ) -> None:
        item = self.management_items.get(
            str(interface_id)
        )

        if item is not None:
            item.finish_delete_action()

    def _clear_management_items(self) -> None:
        for item in self.management_items.values():
            self.management_items_layout.removeWidget(
                item
            )
            item.deleteLater()

        self.management_items = {}

    def set_router(
        self,
        router_id: int | None,
    ) -> None:
        if router_id == self.router_id:
            return

        self.router_id = router_id
        self.clear_cards()
        self._clear_management_items()
        self.priorities = {}
        self.expansion_states = {}
        self.management_expanded = False
        self.summary_toggle_button.blockSignals(True)
        self.summary_toggle_button.setChecked(False)
        self.summary_toggle_button.blockSignals(False)
        self.summary_toggle_button.setArrowType(
            Qt.ArrowType.RightArrow
        )
        self.management_panel.hide()

    def set_connections(
        self,
        connections: list[dict[str, Any]] | None,
    ) -> None:
        connections = list(
            connections or []
        )

        if self.router_id is None:
            self.clear()
            return

        interface_ids = [
            self._interface_id(
                connection,
                index,
            )
            for index, connection in enumerate(
                connections
            )
        ]

        priority_interface_ids = [
            interface_id
            for interface_id, connection in zip(
                interface_ids,
                connections, strict=False,
            )
            if self._connection_has_priority(
                connection
            )
        ]

        self.api_priorities = (
            self._build_api_priority_map(
                connections,
                interface_ids,
            )
        )

        _priority_trace(
            (
                "DATA. Internet connections: "
                f"ids={interface_ids}, "
                f"api_priorities={self.api_priorities}"
            ),
            frequent=True,
        )

        # Роутер — единственный источник истины.
        # Во время отправки изменения сохраняем оптимистическое
        # состояние, а после подтверждения следующий refresh
        # снова читает фактический ip.global.order.
        if not self._change_pending:
            self.priorities = {
                interface_id: self.api_priorities[
                    interface_id
                ]
                for interface_id in priority_interface_ids
                if interface_id in self.api_priorities
            }
            self._confirmed_priorities = dict(
                self.priorities
            )

        incoming = {
            interface_id: connection
            for interface_id, connection in zip(
                interface_ids,
                connections, strict=False,
            )
        }

        for interface_id in list(
            self.cards
        ):
            if interface_id in incoming:
                continue

            card = self.cards.pop(
                interface_id
            )
            self.connections_layout.removeWidget(
                card
            )
            card.deleteLater()
            # Приоритет не удаляем: интерфейс может временно
            # исчезнуть при переключении каналов и затем вернуться.

        priority_count = max(
            1,
            len(self.priorities),
        )

        ordered_ids = sorted(
            interface_ids,
            key=lambda interface_id: (
                interface_id not in self.priorities,
                self.priorities.get(
                    interface_id,
                    999,
                ),
            ),
        )

        for position, interface_id in enumerate(
            ordered_ids
        ):
            connection = incoming[
                interface_id
            ]
            card = self.cards.get(
                interface_id
            )

            if card is None:
                card = InternetConnectionCard(
                    connection,
                    priority_index=(
                        self.priorities.get(
                            interface_id,
                            0,
                        )
                    ),
                    priority_count=priority_count,
                    parent=self.connections_container,
                )
                card.priority_change_requested.connect(
                    self.change_priority
                )
                card.expansion_changed.connect(
                    self._remember_expansion_state
                )
                card.set_expanded(
                    self.expansion_states.get(
                        interface_id,
                        False,
                    ),
                    animated=False,
                )
                self.cards[
                    interface_id
                ] = card
            else:
                card.set_priority_options(
                    priority_count
                )
                card.set_priority(
                    self.priorities.get(
                        interface_id,
                        0,
                    )
                )
                card.update_connection(
                    connection
                )
                card.set_expanded(
                    self.expansion_states.get(
                        interface_id,
                        card.expanded,
                    ),
                    animated=False,
                )

            self.connections_layout.insertWidget(
                position,
                card,
            )

        self.empty_label.setVisible(
            not connections
        )
        self.empty_label.setText(
            "Нет интернет-подключений"
            if not connections
            else ""
        )
        self.connections_container.setVisible(
            bool(connections)
        )

        self._update_management_items(
            connections,
            interface_ids,
        )

        self._update_summary(
            connections,
            ordered_ids,
        )

    def _remember_expansion_state(
        self,
        interface_id: str,
        expanded: bool,
    ) -> None:
        interface_id = str(
            interface_id
            or ""
        ).strip()

        if not interface_id:
            return

        self.expansion_states[
            interface_id
        ] = bool(expanded)

    def change_priority(
        self,
        interface_id: str,
        new_priority: int,
    ) -> None:
        _priority_trace(
            (
                "0. change_priority called: "
                f"interface={interface_id}, "
                f"rank={new_priority}, "
                f"ranks={self.priorities}, "
                f"api={self.api_priorities}"
            )
        )

        if interface_id not in self.priorities:
            _priority_trace(
                "0A. interface is absent in priorities"
            )
            return

        old_priority = self.priorities[
            interface_id
        ]

        if old_priority == new_priority:
            return

        occupied_interface = next(
            (
                item_id
                for item_id, priority
                in self.priorities.items()
                if (
                    item_id != interface_id
                    and priority == new_priority
                )
            ),
            None,
        )

        self.priorities[
            interface_id
        ] = new_priority

        if occupied_interface is not None:
            # Старое место выбранного подключения стало свободным,
            # поэтому его получает подключение, которое было вытеснено.
            self.priorities[
                occupied_interface
            ] = old_priority

        self._normalize_priorities()
        self._apply_priorities_to_cards()

        # Официальный веб-интерфейс Keenetic использует
        # zero-based order:
        # 0 — основной, 1 — резервный 1, 2 — резервный 2.
        active_ids = set(
            self.cards
        )

        connection_orders = {
            item_id: rank
            for item_id, rank
            in self.priorities.items()
            if item_id in active_ids
        }

        if not connection_orders:
            self.rollback_priorities(
                "Нет интернет-каналов для изменения."
            )
            return

        self._change_pending = True
        self._set_all_priority_controls(False)

        self.priorities_changed.emit(
            connection_orders
        )

    def confirm_priorities(
        self,
        applied: dict[str, int],
    ) -> None:
        self.priorities = {
            str(interface_id): int(order)
            for interface_id, order in applied.items()
        }
        self._confirmed_priorities = dict(
            self.priorities
        )
        self._change_pending = False
        self._apply_priorities_to_cards()
        self._set_all_priority_controls(True)

    def rollback_priorities(
        self,
        _message: str = "",
    ) -> None:
        if self._confirmed_priorities:
            self.priorities = dict(
                self._confirmed_priorities
            )

        self._change_pending = False
        self._apply_priorities_to_cards()
        self._set_all_priority_controls(True)

    def _set_all_priority_controls(
        self,
        enabled: bool,
    ) -> None:
        for card in self.cards.values():
            card.set_priority_enabled(enabled)

    @staticmethod
    def _build_api_priority_map(
        connections: list[dict[str, Any]],
        interface_ids: list[str],
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        for interface_id, connection in zip(
            interface_ids,
            connections, strict=False,
        ):
            try:
                value = int(
                    connection.get("global_order")
                )
            except (TypeError, ValueError):
                continue

            if value >= 0:
                result[interface_id] = value

        return result

    @staticmethod
    def _connection_has_priority(
        connection: dict[str, Any],
    ) -> bool:
        try:
            value = int(
                connection.get("global_order")
            )
        except (TypeError, ValueError):
            return False

        return value >= 0

    def clear(self) -> None:
        self.clear_cards()
        self._clear_management_items()
        self.api_priorities = {}
        self._confirmed_priorities = {}
        self._change_pending = False
        self.summary_text.setText(
            "Роутер не подключён"
        )

        default_summary_values = {
            "Состояние": "Роутер не подключён",
            "Подключений": "0",
            "Активных": "0",
            "Основной": "—",
            "Резервный": "—",
            "Публичный IP": "Не определён",
        }

        for name, value in (
            default_summary_values.items()
        ):
            label = self.summary_metric_values.get(
                name
            )

            if label is not None:
                label.setText(value)
        self.empty_label.setText("")
        self.empty_label.hide()
        self.connections_container.hide()

    def clear_cards(self) -> None:
        for card in self.cards.values():
            self.connections_layout.removeWidget(
                card
            )
            card.deleteLater()

        self.cards = {}

    def _normalize_priorities(self) -> None:
        ordered = sorted(
            self.priorities,
            key=lambda interface_id:
            self.priorities[
                interface_id
            ],
        )

        for priority, interface_id in enumerate(
            ordered
        ):
            self.priorities[
                interface_id
            ] = priority

    def _apply_priorities_to_cards(
        self,
    ) -> None:
        ordered = sorted(
            self.cards,
            key=lambda interface_id:
            self.priorities.get(
                interface_id,
                999,
            ),
        )

        count = max(
            1,
            len(ordered),
        )

        for position, interface_id in enumerate(
            ordered
        ):
            card = self.cards[
                interface_id
            ]
            card.set_priority_options(
                count
            )
            card.set_priority(
                self.priorities[
                    interface_id
                ]
            )
            self.connections_layout.insertWidget(
                position,
                card,
            )

    def _update_summary(
        self,
        connections: list[dict[str, Any]],
        ordered_ids: list[str],
    ) -> None:
        """
        Использует тот же порядок, что и карточки мониторинга.

        ordered_ids строится по self.priorities:
            0 — основной;
            1 — резервный 1;
            далее — остальные резервные каналы.

        Фактическое переключение определяется только состоянием
        подключения, но не меняет назначенные пользователем роли.
        """
        connection_count = len(connections)

        by_id = {
            self._interface_id(
                connection,
                index,
            ): connection
            for index, connection in enumerate(
                connections
            )
        }

        active_count = sum(
            1
            for connection in connections
            if InternetConnectionCard._is_connected(
                connection
            )
        )

        configured_main_id = (
            ordered_ids[0]
            if ordered_ids
            else ""
        )
        configured_reserve_id = (
            ordered_ids[1]
            if len(ordered_ids) > 1
            else ""
        )

        main_connection = by_id.get(
            configured_main_id,
            {},
        )
        reserve_connection = by_id.get(
            configured_reserve_id,
            {},
        )

        main_connected = (
            InternetConnectionCard._is_connected(
                main_connection
            )
        )
        reserve_connected = (
            InternetConnectionCard._is_connected(
                reserve_connection
            )
        )

        if not connections or active_count <= 0:
            state_text = "Интернет недоступен"
        elif main_connected:
            state_text = "Интернет доступен"
        elif reserve_connected:
            state_text = (
                "Работа через резервный канал"
            )
        else:
            # Может быть активен резервный 2 или следующий.
            another_reserve_active = any(
                InternetConnectionCard._is_connected(
                    by_id.get(interface_id, {})
                )
                for interface_id in ordered_ids[2:]
            )

            state_text = (
                "Работа через резервный канал"
                if another_reserve_active
                else "Интернет недоступен"
            )

        self.summary_metric_values[
            "Состояние"
        ].setText(state_text)
        self.summary_metric_values[
            "Подключений"
        ].setText(str(connection_count))
        self.summary_metric_values[
            "Активных"
        ].setText(str(active_count))

        if not connections:
            self.summary_text.setText(
                "Нет интернет-подключений"
            )
            self.summary_metric_values[
                "Основной"
            ].setText("—")
            self.summary_metric_values[
                "Резервный"
            ].setText("—")
            self.summary_metric_values[
                "Публичный IP"
            ].setText("Не определён")
            return

        def display_name(
            interface_id: str,
        ) -> str:
            connection = by_id.get(
                interface_id,
                {},
            )

            return str(
                connection.get("name")
                or connection.get("description")
                or connection.get("interface_name")
                or interface_id
                or "Не определён"
            )

        main_name = (
            display_name(configured_main_id)
            if configured_main_id
            else "—"
        )
        reserve_name = (
            display_name(configured_reserve_id)
            if configured_reserve_id
            else "—"
        )

        self.summary_text.setText(
            (
                f"Основной канал: {main_name} · "
                f"{'подключён' if main_connected else 'отключён'}"
            )
        )

        self.summary_metric_values[
            "Основной"
        ].setText(main_name)
        self.summary_metric_values[
            "Резервный"
        ].setText(reserve_name)

        # Сначала ищем адрес на реально работающем канале:
        # основном, затем на резервных по их порядку.
        public_ip_order = [
            interface_id
            for interface_id in ordered_ids
            if InternetConnectionCard._is_connected(
                by_id.get(interface_id, {})
            )
        ]

        self.summary_metric_values[
            "Публичный IP"
        ].setText(
            self._public_ipv4_text(
                connections,
                public_ip_order,
            )
        )

    @staticmethod
    def _public_ipv4_text(
        connections: list[dict[str, Any]],
        ordered_ids: list[str],
    ) -> str:
        by_id = {
            InternetTab._interface_id(
                connection,
                index,
            ): connection
            for index, connection in enumerate(
                connections
            )
        }

        candidates = [
            by_id.get(interface_id, {})
            for interface_id in ordered_ids
        ]

        if not candidates:
            candidates = list(connections)

        for connection in candidates:
            for key in (
                "address",
                "ip",
                "ipv4",
            ):
                raw_value = connection.get(key)

                if raw_value is None:
                    continue

                text = str(raw_value).strip()

                if not text:
                    continue

                address_text = text.split(
                    "/",
                    maxsplit=1,
                )[0]

                try:
                    address = ipaddress.ip_address(
                        address_text
                    )
                except ValueError:
                    continue

                if (
                    isinstance(
                        address,
                        ipaddress.IPv4Address,
                    )
                    and address.is_global
                ):
                    return str(address)

        return "Не определён"

    @staticmethod
    def _interface_id(
        connection: dict[str, Any],
        index: int,
    ) -> str:
        return str(
            connection.get("interface_id")
            or connection.get("id")
            or connection.get("name")
            or f"connection-{index}"
        ).strip()
