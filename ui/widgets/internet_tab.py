from __future__ import annotations

import ipaddress
import re
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
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.debug_log import write_priority_debug
from core.services.runtime_paths import LOGS_DIR
from ui.widgets.status_badge import StatusBadge



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



class InternetConnectionSettingsDialog(QDialog):
    """
    Просмотр и безопасное редактирование параметров WAN.

    Для поддерживаемых проводных подключений этот же диалог
    позволяет сменить IPoE, PPPoE, PPTP или L2TP. Отдельная
    кнопка смены типа в списке подключений не требуется.
    """

    PROTOCOLS = (
        ("Не использовать (IPoE)", "none"),
        ("PPPoE", "pppoe"),
        ("PPTP", "pptp"),
        ("L2TP", "l2tp"),
    )

    def __init__(
        self,
        connection: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.connection = dict(connection)
        self._protocol_change_settings: dict[str, Any] | None = None
        self.interface_id = str(
            connection.get("interface_id")
            or connection.get("id")
            or ""
        ).strip()

        self.original_description = self._text(
            connection.get("description")
            or connection.get("interface_name")
            or connection.get("name")
        )

        self.wireless_isp_settings_supported = bool(
            connection.get("wireless_isp_settings_supported")
        )
        self.original_wireless_band = str(
            connection.get("wireless_isp_band") or ""
        ).strip()
        self.original_wireless_band_title = str(
            connection.get("wireless_isp_band_title")
            or (
                "2,4 ГГц"
                if self.original_wireless_band == "2.4"
                else "5 ГГц"
                if self.original_wireless_band == "5"
                else "не определён"
            )
        ).strip()
        self.original_wireless_ssid = str(
            connection.get("wireless_isp_ssid")
            or connection.get("ssid")
            or ""
        ).strip()
        self.original_wireless_bssid = str(
            connection.get("wireless_isp_bssid")
            or ""
        ).strip().upper()
        self.original_wireless_open_network = bool(
            connection.get("wireless_isp_open_network")
        )
        self.original_wireless_password_configured = bool(
            connection.get("wireless_isp_password_configured")
        )

        self.protocol_change_supported = bool(
            connection.get("protocol_change_supported")
        )
        self.protocol_change_reason = str(
            connection.get("protocol_change_reason")
            or ""
        ).strip()
        current_protocol = str(
            connection.get("protocol_change_current")
            or connection.get("provider_protocol")
            or "none"
        ).strip().lower()
        self.current_protocol = {
            "ipoe": "none",
            "ethernet": "none",
        }.get(current_protocol, current_protocol)

        if self.current_protocol not in {
            "none",
            "pppoe",
            "pptp",
            "l2tp",
        }:
            self.current_protocol = "none"

        self.original_mtu = self._parse_mtu(
            connection.get("mtu")
        )
        self.original_use_provider_dns = bool(
            connection.get(
                "use_provider_dns",
                True,
            )
        )
        configured_dns = connection.get(
            "configured_dns"
        )

        if isinstance(configured_dns, str):
            configured_dns = [
                configured_dns
            ]

        if not isinstance(configured_dns, list):
            configured_dns = []

        self.original_manual_dns = [
            str(value).strip()
            for value in configured_dns
            if str(value).strip()
        ]

        self.ipv4_settings_supported = bool(
            connection.get(
                "ipv4_settings_supported",
                False,
            )
        )
        self.original_ipv4_dhcp = bool(
            connection.get(
                "ipv4_dhcp",
                True,
            )
        )
        self.original_ipv4_address = str(
            connection.get(
                "configured_ipv4_address"
            )
            or ""
        ).strip()

        try:
            self.original_ipv4_prefix = int(
                connection.get(
                    "configured_ipv4_prefix"
                )
            )
        except (TypeError, ValueError):
            self.original_ipv4_prefix = None

        self.original_ipv4_gateway = str(
            connection.get(
                "configured_ipv4_gateway"
            )
            or ""
        ).strip()

        self.dhcp_client_supported = bool(
            connection.get(
                "dhcp_client_supported",
                self.ipv4_settings_supported,
            )
        )
        self.original_dhcp_hostname = str(
            connection.get(
                "dhcp_client_hostname"
            )
            or "Keenetic"
        ).strip()
        self.original_dhcp_routes = bool(
            connection.get(
                "dhcp_client_routes",
                True,
            )
        )
        self.original_dhcp_dns_routes = bool(
            connection.get(
                "dhcp_client_dns_routes",
                True,
            )
        )

        self.mac_settings_supported = bool(
            connection.get(
                "mac_settings_supported",
                False,
            )
        )
        self.original_mac_mode = str(
            connection.get("mac_mode")
            or "factory"
        ).strip().lower()

        if self.original_mac_mode not in {
            "factory",
            "manual",
        }:
            self.original_mac_mode = "factory"

        self.original_manual_mac = str(
            connection.get(
                "configured_mac"
            )
            or ""
        ).strip().upper()

        self.ping_check_supported = bool(
            connection.get(
                "ping_check_supported",
                True,
            )
        )
        self.original_ping_check_enabled = bool(
            connection.get(
                "ping_check_enabled",
                False,
            )
        )
        self.original_ping_check_mode = str(
            connection.get(
                "ping_check_mode"
            )
            or (
                "icmp"
                if self.original_ping_check_enabled
                else "disabled"
            )
        ).strip().lower()

        if self.original_ping_check_mode not in {
            "disabled",
            "automatic",
            "icmp",
            "tls",
            "connect",
            "custom",
        }:
            self.original_ping_check_mode = (
                "icmp"
                if self.original_ping_check_enabled
                else "disabled"
            )

        self.original_ping_check_profile = str(
            connection.get(
                "ping_check_profile"
            )
            or ""
        ).strip()

        self.original_ping_check_host = str(
            connection.get(
                "ping_check_host"
            )
            or (
                "8.8.8.8"
                if self.original_ping_check_mode == "icmp"
                else ""
            )
        ).strip()

        try:
            self.original_ping_check_interval = int(
                connection.get(
                    "ping_check_interval",
                    10,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_interval = 10

        self.original_ping_check_interval = max(
            3,
            min(
                3600,
                self.original_ping_check_interval,
            ),
        )

        try:
            self.original_ping_check_max_fails = int(
                connection.get(
                    "ping_check_max_fails",
                    5,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_max_fails = 5

        self.original_ping_check_max_fails = max(
            1,
            min(
                10,
                self.original_ping_check_max_fails,
            ),
        )

        default_ping_port = (
            53
            if self.original_ping_check_mode == "connect"
            else 443
        )

        try:
            self.original_ping_check_port = int(
                connection.get(
                    "ping_check_port",
                    default_ping_port,
                )
            )
        except (TypeError, ValueError):
            self.original_ping_check_port = (
                default_ping_port
            )

        self.original_ping_check_port = max(
            1,
            min(
                65535,
                self.original_ping_check_port,
            ),
        )

        display_name = (
            self.original_description
            or self.interface_id
            or "Интернет-подключение"
        )

        self.setWindowTitle(
            f"Настройка — {display_name}"
        )
        self.setModal(True)
        self.setMinimumSize(460, 360)

        host_window = (
            parent.window()
            if parent is not None
            else None
        )
        host_width = (
            host_window.width()
            if host_window is not None
            else 900
        )
        host_height = (
            host_window.height()
            if host_window is not None
            else 700
        )

        dialog_width = max(
            460,
            min(
                760,
                host_width - 70,
            ),
        )
        dialog_height = max(
            360,
            min(
                720,
                host_height - 70,
            ),
        )

        self.resize(
            dialog_width,
            dialog_height,
        )
        self.setMaximumSize(
            max(460, host_width - 30),
            max(360, host_height - 30),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(
            QFrame.Shape.NoFrame
        )
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        scroll_content = QWidget(scroll_area)
        scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        content_layout = QVBoxLayout(
            scroll_content
        )
        content_layout.setContentsMargins(
            4,
            2,
            8,
            6,
        )
        content_layout.setSpacing(14)

        title = QLabel(display_name)
        title.setObjectName(
            "internetSettingsTitle"
        )
        title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content_layout.addWidget(title)

        if self.wireless_isp_settings_supported:
            notice_text = (
                "Можно изменить Wi-Fi-сеть Wireless ISP, пароль, "
                "BSSID, пользовательское имя, MTU, IPv4, DHCP, "
                "DNS и проверку доступности. Диапазон 2,4/5 ГГц "
                "закреплён за текущим интерфейсом."
            )
        elif self.protocol_change_supported:
            notice_text = (
                "Можно изменить тип проводного WAN, пользовательское "
                "имя, MTU, WAN MAC, IPv4, DHCP, DNS и проверку "
                "доступности. Недоступные настройки не отображаются."
            )
        else:
            notice_text = (
                "Можно изменить пользовательское имя, MTU, "
                "WAN MAC, IPv4, DHCP, DNS и проверку доступности. "
                "Недоступные настройки не отображаются."
            )
        notice = QLabel(notice_text)
        notice.setWordWrap(True)
        notice.setObjectName(
            "internetSettingsNotice"
        )
        content_layout.addWidget(notice)

        edit_form = QFormLayout()
        edit_form.setHorizontalSpacing(18)
        edit_form.setVerticalSpacing(9)
        edit_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        edit_form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

        self.description_edit = QLineEdit(
            self.original_description
        )
        self.description_edit.setMaxLength(64)
        self.description_edit.setPlaceholderText(
            "Например: Основной провайдер"
        )
        edit_form.addRow(
            "Пользовательское имя:",
            self.description_edit,
        )

        self.wireless_ssid_edit: QLineEdit | None = None
        self.wireless_open_network_check: QCheckBox | None = None
        self.wireless_password_edit: QLineEdit | None = None
        self.wireless_bssid_edit: QLineEdit | None = None

        if self.wireless_isp_settings_supported:
            wireless_section = QLabel(
                "Подключение к Wi-Fi провайдера"
            )
            wireless_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", wireless_section)

            band_value = QLabel(
                self.original_wireless_band_title
            )
            band_value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            band_value.setToolTip(
                "Для смены диапазона удалите это подключение и "
                "создайте Wireless ISP в другом диапазоне."
            )
            edit_form.addRow(
                "Диапазон:",
                band_value,
            )

            self.wireless_ssid_edit = QLineEdit(
                self.original_wireless_ssid
            )
            self.wireless_ssid_edit.setMaxLength(32)
            self.wireless_ssid_edit.setPlaceholderText(
                "Имя Wi-Fi-сети провайдера"
            )
            edit_form.addRow(
                "Wi-Fi-сеть (SSID):",
                self.wireless_ssid_edit,
            )

            self.wireless_open_network_check = QCheckBox(
                "Сеть не защищена паролем"
            )
            self.wireless_open_network_check.setChecked(
                self.original_wireless_open_network
            )
            edit_form.addRow(
                "Защита:",
                self.wireless_open_network_check,
            )

            self.wireless_password_edit = QLineEdit()
            self.wireless_password_edit.setEchoMode(
                QLineEdit.EchoMode.Password
            )
            self.wireless_password_edit.setMaxLength(64)
            self.wireless_password_edit.setPlaceholderText(
                (
                    "Оставьте пустым, чтобы сохранить текущий пароль"
                    if self.original_wireless_password_configured
                    else "Пароль WPA/WPA2 — 8–63 символа"
                )
            )
            edit_form.addRow(
                "Пароль Wi-Fi:",
                self.wireless_password_edit,
            )

            self.wireless_bssid_edit = QLineEdit(
                self.original_wireless_bssid
            )
            self.wireless_bssid_edit.setMaxLength(17)
            self.wireless_bssid_edit.setPlaceholderText(
                "Необязательно, например AA:BB:CC:DD:EE:FF"
            )
            self.wireless_bssid_edit.setToolTip(
                "Пустое поле разрешает подключаться к любой точке "
                "с выбранным SSID."
            )
            edit_form.addRow(
                "BSSID:",
                self.wireless_bssid_edit,
            )

            self.wireless_open_network_check.toggled.connect(
                self._update_wireless_security_fields
            )
            self._update_wireless_security_fields()

        self.protocol_combo: QComboBox | None = None
        self.protocol_auth_widget: QWidget | None = None
        self.protocol_username_edit: QLineEdit | None = None
        self.protocol_password_edit: QLineEdit | None = None
        self.protocol_service_edit: QLineEdit | None = None
        self.protocol_server_edit: QLineEdit | None = None
        self.protocol_note_label: QLabel | None = None
        self.save_button: QPushButton | None = None
        self._protocol_previous_target = self.current_protocol
        self._protocol_mtu_values: dict[str, str] = {}

        if self.protocol_change_supported:
            protocol_section = QLabel(
                "Авторизация у провайдера"
            )
            protocol_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", protocol_section)

            self.protocol_combo = QComboBox()
            for protocol_title, protocol_id in self.PROTOCOLS:
                self.protocol_combo.addItem(
                    protocol_title,
                    protocol_id,
                )

            protocol_index = self.protocol_combo.findData(
                self.current_protocol
            )
            self.protocol_combo.setCurrentIndex(
                protocol_index if protocol_index >= 0 else 0
            )
            self.protocol_combo.setToolTip(
                "Выберите IPoE, PPPoE, PPTP или L2TP. "
                "Физический порт и транспорт сохранятся."
            )
            edit_form.addRow(
                "Тип (протокол):",
                self.protocol_combo,
            )

            self.protocol_auth_widget = QWidget(self)
            protocol_auth_form = QFormLayout(
                self.protocol_auth_widget
            )
            protocol_auth_form.setContentsMargins(
                0,
                0,
                0,
                0,
            )
            protocol_auth_form.setHorizontalSpacing(18)
            protocol_auth_form.setVerticalSpacing(8)
            protocol_auth_form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )

            self.protocol_username_edit = QLineEdit()
            self.protocol_username_edit.setMaxLength(128)
            self.protocol_username_edit.setText(
                str(
                    connection.get("provider_username")
                    or ""
                ).strip()
            )
            self.protocol_username_edit.setPlaceholderText(
                "Логин, выданный провайдером"
            )
            protocol_auth_form.addRow(
                "Имя пользователя:",
                self.protocol_username_edit,
            )

            self.protocol_password_edit = QLineEdit()
            self.protocol_password_edit.setMaxLength(256)
            self.protocol_password_edit.setEchoMode(
                QLineEdit.EchoMode.Password
            )
            self.protocol_password_edit.setPlaceholderText(
                "Пароль, выданный провайдером"
            )
            protocol_auth_form.addRow(
                "Пароль:",
                self.protocol_password_edit,
            )

            self.protocol_service_edit = QLineEdit()
            self.protocol_service_edit.setMaxLength(64)
            self.protocol_service_edit.setText(
                str(
                    connection.get("pppoe_service")
                    or ""
                ).strip()
            )
            self.protocol_service_edit.setPlaceholderText(
                "Необязательно"
            )
            protocol_auth_form.addRow(
                "Имя сервиса PPPoE:",
                self.protocol_service_edit,
            )

            self.protocol_server_edit = QLineEdit()
            self.protocol_server_edit.setMaxLength(253)
            self.protocol_server_edit.setText(
                str(
                    connection.get("provider_server")
                    or ""
                ).strip()
            )
            self.protocol_server_edit.setPlaceholderText(
                "Адрес PPTP/L2TP-сервера"
            )
            protocol_auth_form.addRow(
                "Сервер провайдера:",
                self.protocol_server_edit,
            )

            edit_form.addRow(
                "",
                self.protocol_auth_widget,
            )

            self.protocol_note_label = QLabel()
            self.protocol_note_label.setWordWrap(True)
            self.protocol_note_label.setObjectName(
                "internetSettingsNotice"
            )
            edit_form.addRow(
                "",
                self.protocol_note_label,
            )

            self.protocol_combo.currentIndexChanged.connect(
                self._update_protocol_fields
            )
        self.mtu_edit = QLineEdit(
            (
                str(self.original_mtu)
                if self.original_mtu is not None
                else ""
            )
        )
        self.mtu_edit.setPlaceholderText(
            "576–9000"
        )
        self.mtu_edit.setMaxLength(4)
        edit_form.addRow(
            "MTU:",
            self.mtu_edit,
        )

        self.mac_mode_combo: QComboBox | None = None
        self.mac_address_edit: QLineEdit | None = None

        if self.mac_settings_supported:
            self.mac_mode_combo = QComboBox()
            self.mac_mode_combo.addItem(
                "Заводской WAN MAC",
                "factory",
            )
            self.mac_mode_combo.addItem(
                "Указать вручную",
                "manual",
            )

            mac_mode_index = (
                self.mac_mode_combo.findData(
                    self.original_mac_mode
                )
            )

            if mac_mode_index >= 0:
                self.mac_mode_combo.setCurrentIndex(
                    mac_mode_index
                )

            edit_form.addRow(
                "MAC-адрес:",
                self.mac_mode_combo,
            )

            self.mac_address_edit = QLineEdit(
                (
                    self.original_manual_mac
                    if self.original_mac_mode
                    == "manual"
                    else ""
                )
            )
            self.mac_address_edit.setPlaceholderText(
                "Например: 02:11:22:33:44:55"
            )
            self.mac_address_edit.setMaxLength(17)
            self.mac_address_edit.setToolTip(
                "Ручной WAN MAC в формате "
                "AA:BB:CC:DD:EE:FF"
            )
            edit_form.addRow(
                "Ручной MAC:",
                self.mac_address_edit,
            )

            self.mac_mode_combo.currentIndexChanged.connect(
                self._update_mac_fields
            )
            self._update_mac_fields()

            mac_warning = QLabel(
                "Изменение MAC активного WAN может "
                "временно разорвать соединение и обновить "
                "выданный провайдером IP-адрес."
            )
            mac_warning.setWordWrap(True)
            mac_warning.setObjectName(
                "internetSettingsWarning"
            )
            edit_form.addRow("", mac_warning)

        self.ipv4_dhcp_check: QCheckBox | None = None
        self.ipv4_address_edit: QLineEdit | None = None
        self.ipv4_prefix_edit: QLineEdit | None = None
        self.ipv4_gateway_edit: QLineEdit | None = None
        self.dhcp_hostname_edit: QLineEdit | None = None
        self.dhcp_routes_check: QCheckBox | None = None
        self.dhcp_dns_routes_check: QCheckBox | None = None

        if self.ipv4_settings_supported:
            self.ipv4_dhcp_check = QCheckBox(
                "Получать IPv4-адрес автоматически (DHCP)"
            )
            self.ipv4_dhcp_check.setChecked(
                self.original_ipv4_dhcp
            )
            self.ipv4_dhcp_check.setToolTip(
                "DHCP автоматически получает адрес, маску, "
                "DNS и шлюз от провайдера"
            )
            edit_form.addRow(
                "Настройка IPv4:",
                self.ipv4_dhcp_check,
            )

            self.ipv4_address_edit = QLineEdit(
                self.original_ipv4_address
            )
            self.ipv4_address_edit.setPlaceholderText(
                "Например: 192.0.2.10"
            )
            edit_form.addRow(
                "IPv4-адрес:",
                self.ipv4_address_edit,
            )

            prefix_text = (
                str(self.original_ipv4_prefix)
                if self.original_ipv4_prefix is not None
                else ""
            )
            self.ipv4_prefix_edit = QLineEdit(
                prefix_text
            )
            self.ipv4_prefix_edit.setPlaceholderText(
                "1–32, например 24"
            )
            self.ipv4_prefix_edit.setMaxLength(2)
            edit_form.addRow(
                "Длина префикса:",
                self.ipv4_prefix_edit,
            )

            self.ipv4_gateway_edit = QLineEdit(
                self.original_ipv4_gateway
            )
            self.ipv4_gateway_edit.setPlaceholderText(
                "Например: 192.0.2.1"
            )
            edit_form.addRow(
                "Шлюз:",
                self.ipv4_gateway_edit,
            )

            if self.dhcp_client_supported:
                self.dhcp_hostname_edit = QLineEdit(
                    self.original_dhcp_hostname
                )
                self.dhcp_hostname_edit.setMaxLength(63)
                self.dhcp_hostname_edit.setPlaceholderText(
                    "Например: Keenetic"
                )
                self.dhcp_hostname_edit.setToolTip(
                    "Имя, которое DHCP-клиент передаёт "
                    "серверу провайдера"
                )
                edit_form.addRow(
                    "Имя DHCP-клиента:",
                    self.dhcp_hostname_edit,
                )

                self.dhcp_routes_check = QCheckBox(
                    "Принимать маршруты от DHCP-сервера"
                )
                self.dhcp_routes_check.setChecked(
                    self.original_dhcp_routes
                )
                self.dhcp_routes_check.setToolTip(
                    "Разрешить провайдеру передавать "
                    "дополнительные маршруты по DHCP"
                )
                edit_form.addRow(
                    "Маршруты DHCP:",
                    self.dhcp_routes_check,
                )

                self.dhcp_dns_routes_check = QCheckBox(
                    "Создавать маршруты к DNS провайдера"
                )
                self.dhcp_dns_routes_check.setChecked(
                    self.original_dhcp_dns_routes
                )
                self.dhcp_dns_routes_check.setToolTip(
                    "Создавать маршруты к DNS-серверам, "
                    "полученным через этот DHCP-клиент"
                )
                edit_form.addRow(
                    "Маршруты DNS:",
                    self.dhcp_dns_routes_check,
                )

            self.ipv4_dhcp_check.toggled.connect(
                self._update_ipv4_fields
            )
            self._update_ipv4_fields(
                self.original_ipv4_dhcp
            )

            ipv4_warning = QLabel(
                "Изменение IPv4 активного интернет-канала "
                "может временно разорвать соединение с роутером."
            )
            ipv4_warning.setWordWrap(True)
            ipv4_warning.setObjectName(
                "internetSettingsWarning"
            )
            edit_form.addRow("", ipv4_warning)

        self.ping_check_mode_combo: QComboBox | None = None
        self.ping_check_host_edit: QLineEdit | None = None
        self.ping_check_port_spin: QSpinBox | None = None
        self.ping_check_interval_spin: QSpinBox | None = None
        self.ping_check_max_fails_spin: QSpinBox | None = None
        self.ping_check_note_label: QLabel | None = None
        self._last_ping_check_mode = (
            self.original_ping_check_mode
        )

        if self.ping_check_supported:
            ping_section = QLabel(
                "Проверка доступности интернет-канала"
            )
            ping_section.setObjectName(
                "internetSettingsSectionTitle"
            )
            edit_form.addRow("", ping_section)

            self.ping_check_mode_combo = QComboBox()
            self.ping_check_mode_combo.addItem(
                "Выключено",
                "disabled",
            )
            self.ping_check_mode_combo.addItem(
                "Автоматически (Keenetic)",
                "automatic",
            )
            self.ping_check_mode_combo.addItem(
                "ICMP echo (Ping)",
                "icmp",
            )
            self.ping_check_mode_combo.addItem(
                "TCP/TLS — проверка порта",
                "tls",
            )
            self.ping_check_mode_combo.addItem(
                "TCP — проверка порта",
                "connect",
            )

            if self.original_ping_check_mode == "custom":
                self.ping_check_mode_combo.addItem(
                    (
                        "Существующий профиль "
                        "(не изменять)"
                    ),
                    "custom",
                )

            ping_mode_index = (
                self.ping_check_mode_combo.findData(
                    self.original_ping_check_mode
                )
            )

            if ping_mode_index < 0:
                ping_mode_index = 0

            self.ping_check_mode_combo.setCurrentIndex(
                ping_mode_index
            )
            self.ping_check_mode_combo.setToolTip(
                "Автоматический режим соответствует "
                "стандартной проверке веб-интерфейса Keenetic"
            )
            edit_form.addRow(
                "Режим Ping Check:",
                self.ping_check_mode_combo,
            )

            self.ping_check_host_edit = QLineEdit(
                self.original_ping_check_host
            )
            self.ping_check_host_edit.setMaxLength(253)
            self.ping_check_host_edit.setPlaceholderText(
                "Например: 8.8.8.8 или example.com"
            )
            edit_form.addRow(
                "Узел проверки:",
                self.ping_check_host_edit,
            )

            self.ping_check_port_spin = QSpinBox()
            self.ping_check_port_spin.setRange(
                1,
                65535,
            )
            self.ping_check_port_spin.setValue(
                self.original_ping_check_port
            )
            self.ping_check_port_spin.setToolTip(
                "TCP-порт удалённого узла. "
                "Для TLS обычно используется 443, "
                "для DNS по TCP — 53"
            )
            edit_form.addRow(
                "TCP-порт:",
                self.ping_check_port_spin,
            )

            self.ping_check_interval_spin = QSpinBox()
            self.ping_check_interval_spin.setRange(
                3,
                3600,
            )
            self.ping_check_interval_spin.setValue(
                self.original_ping_check_interval
            )
            self.ping_check_interval_spin.setSuffix(
                " с"
            )
            self.ping_check_interval_spin.setToolTip(
                "Период между проверками"
            )
            edit_form.addRow(
                "Интервал:",
                self.ping_check_interval_spin,
            )

            self.ping_check_max_fails_spin = QSpinBox()
            self.ping_check_max_fails_spin.setRange(
                1,
                10,
            )
            self.ping_check_max_fails_spin.setValue(
                self.original_ping_check_max_fails
            )
            self.ping_check_max_fails_spin.setToolTip(
                "Количество последовательных ошибок, "
                "после которых канал считается недоступным"
            )
            edit_form.addRow(
                "Неудачных попыток:",
                self.ping_check_max_fails_spin,
            )

            self.ping_check_note_label = QLabel()
            self.ping_check_note_label.setWordWrap(True)
            self.ping_check_note_label.setObjectName(
                "internetSettingsNotice"
            )
            edit_form.addRow(
                "",
                self.ping_check_note_label,
            )

            self.ping_check_mode_combo.currentIndexChanged.connect(
                self._update_ping_check_fields
            )
            self._update_ping_check_fields()

        self.provider_dns_check = QCheckBox(
            "Использовать DNS-серверы, полученные "
            "от провайдера"
        )
        self.provider_dns_check.setChecked(
            self.original_use_provider_dns
        )
        self.provider_dns_check.setToolTip(
            "Соответствует параметру ip.name-servers "
            "выбранного интерфейса"
        )
        edit_form.addRow(
            "DNS провайдера:",
            self.provider_dns_check,
        )

        primary_dns = (
            self.original_manual_dns[0]
            if len(self.original_manual_dns) >= 1
            else ""
        )
        secondary_dns = (
            self.original_manual_dns[1]
            if len(self.original_manual_dns) >= 2
            else ""
        )

        self.primary_dns_edit = QLineEdit(
            primary_dns
        )
        self.primary_dns_edit.setPlaceholderText(
            "Например: 1.1.1.1 или 2606:4700:4700::1111"
        )
        edit_form.addRow(
            "Основной DNS:",
            self.primary_dns_edit,
        )

        self.secondary_dns_edit = QLineEdit(
            secondary_dns
        )
        self.secondary_dns_edit.setPlaceholderText(
            "Например: 8.8.8.8 или 2001:4860:4860::8888"
        )
        edit_form.addRow(
            "Резервный DNS:",
            self.secondary_dns_edit,
        )

        dns_note_text = (
            "Оставьте оба поля пустыми, чтобы удалить "
            "ручные DNS этого подключения."
        )

        if len(self.original_manual_dns) > 2:
            dns_note_text += (
                "\nНа роутере найдено больше двух записей. "
                "При сохранении лишние DNS будут удалены."
            )

        dns_note = QLabel(
            dns_note_text
        )
        dns_note.setWordWrap(True)
        dns_note.setObjectName(
            "internetSettingsNotice"
        )
        edit_form.addRow("", dns_note)

        mtu_note = QLabel(
            "Изменяйте MTU только при необходимости. "
            "Неправильное значение может нарушить "
            "доступ к отдельным сайтам и сервисам."
        )
        mtu_note.setWordWrap(True)
        mtu_note.setObjectName(
            "internetSettingsWarning"
        )
        edit_form.addRow("", mtu_note)

        content_layout.addLayout(edit_form)

        details_title = QLabel(
            "Текущие параметры"
        )
        details_title.setObjectName(
            "internetSettingsSectionTitle"
        )
        content_layout.addWidget(details_title)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows
        )

        fields = (
            (
                "Интерфейс",
                self.interface_id,
            ),
            (
                "Тип подключения",
                connection.get("type")
                or connection.get("connection_type"),
            ),
            (
                "Состояние",
                self._status_text(connection),
            ),
            (
                "IPv4-адрес",
                connection.get("address")
                or connection.get("ip"),
            ),
            (
                "IPv6-адрес",
                connection.get("ipv6_address"),
            ),
            (
                "Шлюз",
                connection.get("gateway"),
            ),
            (
                "DNS-серверы",
                connection.get("dns"),
            ),
            (
                "MAC-адрес",
                connection.get("mac"),
            ),
            (
                "Скорость линии",
                self._format_speed(
                    connection.get("link_speed_bps")
                ),
            ),
            (
                "Время подключения",
                connection.get("uptime"),
            ),
            (
                "Источник статистики",
                connection.get("traffic_source"),
            ),
            (
                "Приоритет",
                self._priority_text(connection),
            ),
        )

        for caption, value in fields:
            text = self._text(value)

            if not text:
                continue

            value_label = QLabel(text)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(
                f"{caption}:",
                value_label,
            )

        content_layout.addLayout(form)

        content_layout.addStretch(1)
        scroll_area.setWidget(
            scroll_content
        )
        root.addWidget(
            scroll_area,
            1,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        save_button = buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.save_button = save_button
        cancel_button = buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )

        if save_button is not None:
            save_button.setText("Сохранить")

        if cancel_button is not None:
            cancel_button.setText("Назад")

        buttons.accepted.connect(
            self._validate_and_accept
        )
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if self.protocol_combo is not None:
            self._update_protocol_fields()

    def _selected_protocol(self) -> str:
        if self.protocol_combo is None:
            return self.current_protocol

        target = str(
            self.protocol_combo.currentData()
            or self.current_protocol
        ).strip().lower()

        return (
            target
            if target in {"none", "pppoe", "pptp", "l2tp"}
            else self.current_protocol
        )

    def _protocol_is_changed(self) -> bool:
        return (
            self.protocol_change_supported
            and self._selected_protocol()
            != self.current_protocol
        )

    def _set_standard_settings_enabled(
        self,
        enabled: bool,
    ) -> None:
        enabled = bool(enabled)

        if self.mac_mode_combo is not None:
            self.mac_mode_combo.setEnabled(enabled)

        if self.mac_address_edit is not None:
            self.mac_address_edit.setEnabled(enabled)

        if self.ipv4_dhcp_check is not None:
            self.ipv4_dhcp_check.setEnabled(enabled)

        for widget in (
            self.ipv4_address_edit,
            self.ipv4_prefix_edit,
            self.ipv4_gateway_edit,
            self.dhcp_hostname_edit,
            self.dhcp_routes_check,
            self.dhcp_dns_routes_check,
            self.ping_check_mode_combo,
            self.ping_check_host_edit,
            self.ping_check_port_spin,
            self.ping_check_interval_spin,
            self.ping_check_max_fails_spin,
        ):
            if widget is not None:
                widget.setEnabled(enabled)

        self.provider_dns_check.setEnabled(enabled)
        self.primary_dns_edit.setEnabled(enabled)
        self.secondary_dns_edit.setEnabled(enabled)

        if enabled:
            self._update_mac_fields()

            if self.ipv4_dhcp_check is not None:
                self._update_ipv4_fields(
                    self.ipv4_dhcp_check.isChecked()
                )

            self._update_ping_check_fields()

    def _update_protocol_fields(
        self,
        _index: int | None = None,
    ) -> None:
        if self.protocol_combo is None:
            return

        previous = self._protocol_previous_target

        if hasattr(self, "mtu_edit"):
            self._protocol_mtu_values[previous] = (
                self.mtu_edit.text().strip()
            )

        target = self._selected_protocol()
        self._protocol_previous_target = target
        changed = target != self.current_protocol
        authenticated = target != "none" and changed

        if self.protocol_auth_widget is not None:
            auth_layout = self.protocol_auth_widget.layout()

            for field in (
                self.protocol_username_edit,
                self.protocol_password_edit,
            ):
                if field is None:
                    continue
                field.setVisible(authenticated)
                label = auth_layout.labelForField(field)
                if label is not None:
                    label.setVisible(authenticated)

            if self.protocol_service_edit is not None:
                visible = target == "pppoe" and changed
                self.protocol_service_edit.setVisible(visible)
                label = auth_layout.labelForField(
                    self.protocol_service_edit
                )
                if label is not None:
                    label.setVisible(visible)

            if self.protocol_server_edit is not None:
                visible = (
                    target in {"pptp", "l2tp"}
                    and changed
                )
                self.protocol_server_edit.setVisible(visible)
                label = auth_layout.labelForField(
                    self.protocol_server_edit
                )
                if label is not None:
                    label.setVisible(visible)

            self.protocol_auth_widget.setVisible(changed)

        default_mtu = {
            "none": 1500,
            "pppoe": 1492,
            "pptp": 1400,
            "l2tp": 1500,
        }[target]

        if hasattr(self, "mtu_edit"):
            target_mtu = self._protocol_mtu_values.get(
                target
            )

            if target_mtu is None:
                target_mtu = (
                    str(self.original_mtu)
                    if (
                        target == self.current_protocol
                        and self.original_mtu is not None
                    )
                    else str(default_mtu)
                )

            if self.mtu_edit.text().strip() != target_mtu:
                self.mtu_edit.setText(target_mtu)

            max_mtu = (
                1492
                if target == "pppoe"
                else 1500
            )
            self.mtu_edit.setPlaceholderText(
                f"576–{max_mtu}"
            )

        self._set_standard_settings_enabled(
            not changed
        )

        if self.protocol_note_label is not None:
            current_title = {
                "none": "IPoE",
                "pppoe": "PPPoE",
                "pptp": "PPTP",
                "l2tp": "L2TP",
            }[self.current_protocol]
            target_title = {
                "none": "IPoE",
                "pppoe": "PPPoE",
                "pptp": "PPTP",
                "l2tp": "L2TP",
            }[target]

            if changed:
                self.protocol_note_label.setText(
                    f"Будет выполнена смена {current_title} → "
                    f"{target_title}. Физический порт, транспортный "
                    "IPv4, WAN MAC, DNS, приоритет и Ping Check "
                    "будут сохранены автоматически. Остальные "
                    "поля временно заблокированы до завершения "
                    "смены протокола."
                )
            else:
                self.protocol_note_label.setText(
                    f"Текущий протокол: {current_title}. "
                    "Выберите другой вариант, чтобы сменить тип "
                    "подключения в этом же окне."
                )

        if self.save_button is not None:
            self.save_button.setText(
                "Сменить тип"
                if changed
                else "Сохранить"
            )
            self.save_button.setToolTip(
                (
                    "Создать новый протокол на существующем "
                    "транспорте и удалить прежний после проверки"
                )
                if changed
                else "Сохранить параметры подключения"
            )

    @classmethod
    def _normalize_provider_host(
        cls,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if (
            not text
            or len(text) > 253
            or any(
                character.isspace()
                for character in text
            )
            or "://" in text
            or "/" in text
            or any(
                character in "\r\n\x00"
                for character in text
            )
        ):
            return ""

        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            pass

        try:
            ascii_name = (
                text.rstrip(".")
                .encode("idna")
                .decode("ascii")
            )
        except UnicodeError:
            return ""

        labels = ascii_name.split(".")

        if not labels:
            return ""

        for label in labels:
            if (
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(
                    character
                    not in (
                        "abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789-"
                    )
                    for character in label
                )
            ):
                return ""

        return ascii_name.lower()

    def _validated_protocol_change(
        self,
        *,
        description: str,
    ) -> dict[str, Any] | None:
        if not self._protocol_is_changed():
            return None

        target = self._selected_protocol()
        mtu_text = self.mtu_edit.text().strip()

        try:
            mtu = int(mtu_text)
        except ValueError:
            self._show_error(
                "MTU должен быть целым числом."
            )
            return None

        max_mtu = 1492 if target == "pppoe" else 1500

        if not 576 <= mtu <= max_mtu:
            self._show_error(
                f"Для выбранного протокола MTU должен "
                f"быть от 576 до {max_mtu}."
            )
            return None

        result: dict[str, Any] = {
            "target_protocol": target,
            "description": description,
            "mtu": mtu,
        }

        if target == "none":
            return result

        if (
            self.protocol_username_edit is None
            or self.protocol_password_edit is None
        ):
            self._show_error(
                "Поля авторизации провайдера не созданы."
            )
            return None

        username = (
            self.protocol_username_edit.text()
            .strip()
        )
        password = self.protocol_password_edit.text()
        protocol_title = target.upper()

        if not username:
            self._show_error(
                f"Укажите имя пользователя {protocol_title}."
            )
            return None

        if not password:
            self._show_error(
                f"Укажите пароль {protocol_title}."
            )
            return None

        if any(
            character in "\r\n\x00"
            for character in username + password
        ):
            self._show_error(
                "Учётные данные содержат недопустимые символы."
            )
            return None

        result.update(
            {
                "username": username,
                "password": password,
            }
        )

        if target == "pppoe":
            service = (
                self.protocol_service_edit.text().strip()
                if self.protocol_service_edit is not None
                else ""
            )

            if any(
                character in "\r\n\x00"
                for character in service
            ):
                self._show_error(
                    "Имя сервиса содержит недопустимые символы."
                )
                return None

            result["service"] = service
        else:
            server = self._normalize_provider_host(
                (
                    self.protocol_server_edit.text()
                    if self.protocol_server_edit is not None
                    else ""
                )
            )

            if not server:
                self._show_error(
                    f"Укажите корректный адрес "
                    f"{protocol_title}-сервера."
                )
                return None

            result["server"] = server

        return result

    def protocol_change_settings(
        self,
    ) -> dict[str, Any] | None:
        if self._protocol_change_settings is None:
            return None

        return dict(self._protocol_change_settings)

    @staticmethod
    def _valid_wireless_ssid(value: str) -> bool:
        text = str(value or "")

        if (
            not text
            or any(character in "\r\n\x00" for character in text)
        ):
            return False

        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            return False

        return 1 <= size <= 32

    @staticmethod
    def _valid_wireless_password(value: str) -> bool:
        text = str(value or "")

        if any(character in "\r\n\x00" for character in text):
            return False

        if re.fullmatch(r"[0-9A-Fa-f]{64}", text):
            return True

        try:
            return 8 <= len(text.encode("utf-8")) <= 63
        except UnicodeError:
            return False

    def _update_wireless_security_fields(
        self,
        _checked: bool | None = None,
    ) -> None:
        if self.wireless_password_edit is None:
            return

        open_network = bool(
            self.wireless_open_network_check is not None
            and self.wireless_open_network_check.isChecked()
        )
        self.wireless_password_edit.setEnabled(
            not open_network
        )

    def _validated_wireless_isp_changes(
        self,
    ) -> dict[str, Any] | None:
        if not self.wireless_isp_settings_supported:
            return {}

        if (
            self.wireless_ssid_edit is None
            or self.wireless_open_network_check is None
            or self.wireless_password_edit is None
            or self.wireless_bssid_edit is None
        ):
            return {}

        result: dict[str, Any] = {}
        ssid = self.wireless_ssid_edit.text()

        if not self._valid_wireless_ssid(ssid):
            self._show_error(
                "SSID Wireless ISP должен занимать от 1 до 32 байт."
            )
            return None

        if ssid != self.original_wireless_ssid:
            result["ssid"] = ssid

        open_network = (
            self.wireless_open_network_check.isChecked()
        )
        password = self.wireless_password_edit.text()

        if open_network:
            if not self.original_wireless_open_network:
                result["open_network"] = True
        else:
            protection_changed = (
                open_network
                != self.original_wireless_open_network
            )

            if password:
                if not self._valid_wireless_password(password):
                    self._show_error(
                        "Пароль WPA/WPA2 должен содержать 8–63 "
                        "символа или 64 шестнадцатеричные цифры."
                    )
                    return None
                result["password"] = password
                if protection_changed:
                    result["open_network"] = False
            elif protection_changed:
                self._show_error(
                    "Для перехода с открытой сети на защищённую "
                    "укажите новый пароль Wi-Fi."
                )
                return None

        bssid = (
            self.wireless_bssid_edit.text()
            .strip()
            .upper()
            .replace("-", ":")
        )

        if bssid and not re.fullmatch(
            r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}",
            bssid,
        ):
            self._show_error(
                "BSSID должен иметь вид AA:BB:CC:DD:EE:FF."
            )
            return None

        if bssid != self.original_wireless_bssid:
            result["bssid"] = bssid

        return result

    def _validate_and_accept(self) -> None:
        if not self.interface_id:
            self._show_error(
                "Не удалось определить интерфейс."
            )
            return

        description = (
            self.description_edit.text()
            .strip()
        )

        if not description:
            self._show_error(
                "Пользовательское имя не может быть пустым."
            )
            return

        if self._protocol_is_changed():
            protocol_settings = (
                self._validated_protocol_change(
                    description=description,
                )
            )

            if protocol_settings is None:
                return

            self._protocol_change_settings = (
                protocol_settings
            )
            self.accept()
            return

        mtu_text = self.mtu_edit.text().strip()
        mtu: int | None = None

        if mtu_text:
            try:
                mtu = int(mtu_text)
            except ValueError:
                self._show_error(
                    "MTU должен быть целым числом."
                )
                return

            if not 576 <= mtu <= 9000:
                self._show_error(
                    "MTU должен находиться в диапазоне "
                    "от 576 до 9000."
                )
                return

        use_provider_dns = (
            self.provider_dns_check.isChecked()
        )

        manual_dns = self._validated_manual_dns()

        if manual_dns is None:
            return

        ipv4_settings = (
            self._validated_ipv4_settings()
        )

        if ipv4_settings is None:
            return

        dhcp_client_settings = (
            self._validated_dhcp_client_settings()
        )

        if dhcp_client_settings is None:
            return

        mac_settings = (
            self._validated_mac_settings()
        )

        if mac_settings is None:
            return

        ping_check_settings = (
            self._validated_ping_check_settings()
        )

        if ping_check_settings is None:
            return

        wireless_isp_changes = (
            self._validated_wireless_isp_changes()
        )

        if wireless_isp_changes is None:
            return

        if (
            description == self.original_description
            and mtu == self.original_mtu
            and use_provider_dns
            == self.original_use_provider_dns
            and manual_dns
            == self.original_manual_dns
            and ipv4_settings
            == self._original_ipv4_settings()
            and dhcp_client_settings
            == self._original_dhcp_client_settings()
            and mac_settings
            == self._original_mac_settings()
            and ping_check_settings
            == self._original_ping_check_settings()
            and not wireless_isp_changes
        ):
            self.reject()
            return

        self.accept()

    def changes(self) -> dict[str, Any]:
        if self._protocol_change_settings is not None:
            return {}

        result: dict[str, Any] = {}

        description = (
            self.description_edit.text()
            .strip()
        )

        if description != self.original_description:
            result["description"] = description

        mtu_text = self.mtu_edit.text().strip()
        mtu = (
            int(mtu_text)
            if mtu_text
            else None
        )

        if mtu != self.original_mtu:
            result["mtu"] = mtu

        use_provider_dns = (
            self.provider_dns_check.isChecked()
        )

        if (
            use_provider_dns
            != self.original_use_provider_dns
        ):
            result[
                "use_provider_dns"
            ] = use_provider_dns

        manual_dns = self._validated_manual_dns()

        if manual_dns is None:
            return result

        if manual_dns != self.original_manual_dns:
            result["manual_dns"] = manual_dns

        ipv4_settings = (
            self._validated_ipv4_settings()
        )

        if (
            ipv4_settings is not None
            and ipv4_settings
            != self._original_ipv4_settings()
        ):
            result["ipv4"] = ipv4_settings

        dhcp_client_settings = (
            self._validated_dhcp_client_settings()
        )

        if (
            dhcp_client_settings is not None
            and dhcp_client_settings
            != self._original_dhcp_client_settings()
        ):
            result[
                "dhcp_client"
            ] = dhcp_client_settings

        mac_settings = (
            self._validated_mac_settings()
        )

        if (
            mac_settings is not None
            and mac_settings
            != self._original_mac_settings()
        ):
            result["mac"] = mac_settings

        ping_check_settings = (
            self._validated_ping_check_settings()
        )

        if (
            ping_check_settings is not None
            and ping_check_settings
            != self._original_ping_check_settings()
        ):
            result[
                "ping_check"
            ] = ping_check_settings

        wireless_isp_changes = (
            self._validated_wireless_isp_changes()
        )

        if wireless_isp_changes:
            result["wireless_isp"] = (
                wireless_isp_changes
            )

        return result

    def _update_ping_check_fields(
        self,
        _index: int | None = None,
    ) -> None:
        mode = (
            str(
                self.ping_check_mode_combo.currentData()
                or "disabled"
            )
            if self.ping_check_mode_combo is not None
            else "disabled"
        )
        manual = mode in {
            "icmp",
            "tls",
            "connect",
        }
        port_enabled = mode in {
            "tls",
            "connect",
        }

        if (
            self.ping_check_port_spin is not None
            and mode != self._last_ping_check_mode
        ):
            if mode == "tls":
                self.ping_check_port_spin.setValue(
                    443
                )
            elif mode == "connect":
                self.ping_check_port_spin.setValue(
                    53
                )

        self._last_ping_check_mode = mode

        for widget in (
            self.ping_check_host_edit,
            self.ping_check_interval_spin,
            self.ping_check_max_fails_spin,
        ):
            if widget is not None:
                widget.setEnabled(manual)

        if self.ping_check_port_spin is not None:
            self.ping_check_port_spin.setEnabled(
                port_enabled
            )

        if self.ping_check_note_label is None:
            return

        if mode == "automatic":
            self.ping_check_note_label.setText(
                "Автоматический режим использует встроенный "
                "профиль Keenetic и проверяет несколько "
                "общедоступных узлов через TCP/443."
            )
        elif mode == "icmp":
            self.ping_check_note_label.setText(
                "ICMP-режим отправляет echo-запросы "
                "указанному IP-адресу или доменному имени."
            )
        elif mode == "tls":
            self.ping_check_note_label.setText(
                "TCP/TLS устанавливает TCP-соединение и "
                "выполняет TLS-проверку. Этот режим лучше "
                "обнаруживает перенаправление на портал "
                "провайдера."
            )
        elif mode == "connect":
            self.ping_check_note_label.setText(
                "TCP-режим проверяет возможность установить "
                "соединение с указанным портом без TLS."
            )
        elif mode == "custom":
            self.ping_check_note_label.setText(
                "На интерфейсе назначен сторонний или "
                "расширенный профиль. Он останется без "
                "изменений, пока не выбран другой режим."
            )
        else:
            self.ping_check_note_label.setText(
                "Проверка доступности для этого подключения "
                "будет отключена."
            )

    def _original_ping_check_settings(
        self,
    ) -> dict[str, Any]:
        if not self.ping_check_supported:
            return {}

        if self.original_ping_check_mode == "disabled":
            return {
                "mode": "disabled",
            }

        if self.original_ping_check_mode == "automatic":
            return {
                "mode": "automatic",
            }

        if self.original_ping_check_mode == "custom":
            return {
                "mode": "custom",
                "profile": (
                    self.original_ping_check_profile
                ),
            }

        result = {
            "mode": self.original_ping_check_mode,
            "host": self.original_ping_check_host,
            "interval": (
                self.original_ping_check_interval
            ),
            "max_fails": (
                self.original_ping_check_max_fails
            ),
        }

        if self.original_ping_check_mode in {
            "tls",
            "connect",
        }:
            result["port"] = (
                self.original_ping_check_port
            )

        return result

    @staticmethod
    def _normalize_ping_check_host(
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if (
            not text
            or len(text) > 253
            or any(
                character.isspace()
                for character in text
            )
            or "://" in text
            or "/" in text
        ):
            return ""

        try:
            return str(
                ipaddress.ip_address(text)
            )
        except ValueError:
            pass

        try:
            ascii_name = (
                text.rstrip(".")
                .encode("idna")
                .decode("ascii")
            )
        except UnicodeError:
            return ""

        labels = ascii_name.split(".")

        if not labels:
            return ""

        for label in labels:
            if (
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or any(
                    character
                    not in (
                        "abcdefghijklmnopqrstuvwxyz"
                        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789-"
                    )
                    for character in label
                )
            ):
                return ""

        return ascii_name.lower()

    def _validated_ping_check_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.ping_check_supported:
            return {}

        if self.ping_check_mode_combo is None:
            return {}

        mode = str(
            self.ping_check_mode_combo.currentData()
            or "disabled"
        )

        if mode == "disabled":
            return {
                "mode": "disabled",
            }

        if mode == "automatic":
            return {
                "mode": "automatic",
            }

        if mode == "custom":
            return self._original_ping_check_settings()

        if mode not in {
            "icmp",
            "tls",
            "connect",
        }:
            self._show_error(
                "Выбран неизвестный режим Ping Check."
            )
            return None

        if (
            self.ping_check_host_edit is None
            or self.ping_check_interval_spin is None
            or self.ping_check_max_fails_spin is None
        ):
            self._show_error(
                "Поля Ping Check не созданы."
            )
            return None

        host = self._normalize_ping_check_host(
            self.ping_check_host_edit.text()
        )

        if not host:
            self._show_error(
                "Укажите корректный IP-адрес или имя "
                "узла для Ping Check."
            )
            return None

        result = {
            "mode": mode,
            "host": host,
            "interval": (
                self.ping_check_interval_spin.value()
            ),
            "max_fails": (
                self.ping_check_max_fails_spin.value()
            ),
        }

        if mode in {
            "tls",
            "connect",
        }:
            if self.ping_check_port_spin is None:
                self._show_error(
                    "Поле TCP-порта не создано."
                )
                return None

            result["port"] = (
                self.ping_check_port_spin.value()
            )

        return result

    def _update_mac_fields(self) -> None:
        if (
            self.mac_mode_combo is None
            or self.mac_address_edit is None
        ):
            return

        self.mac_address_edit.setEnabled(
            self.mac_mode_combo.currentData()
            == "manual"
        )

    def _original_mac_settings(
        self,
    ) -> dict[str, Any]:
        if not self.mac_settings_supported:
            return {}

        if self.original_mac_mode == "manual":
            return {
                "mode": "manual",
                "address": self.original_manual_mac,
            }

        return {
            "mode": "factory",
        }

    @staticmethod
    def _normalize_mac_address(
        value: str,
    ) -> str:
        compact = "".join(
            character
            for character in str(value or "")
            if character.isalnum()
        ).upper()

        if (
            len(compact) != 12
            or any(
                character not in "0123456789ABCDEF"
                for character in compact
            )
        ):
            return ""

        return ":".join(
            compact[index:index + 2]
            for index in range(0, 12, 2)
        )

    def _validated_mac_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.mac_settings_supported:
            return {}

        if (
            self.mac_mode_combo is None
            or self.mac_address_edit is None
        ):
            self._show_error(
                "Поля настройки MAC не созданы."
            )
            return None

        mode = str(
            self.mac_mode_combo.currentData()
            or "factory"
        )

        if mode == "factory":
            return {
                "mode": "factory",
            }

        normalized = self._normalize_mac_address(
            self.mac_address_edit.text()
        )

        if not normalized:
            self._show_error(
                "Укажите MAC-адрес в формате "
                "AA:BB:CC:DD:EE:FF."
            )
            return None

        octets = [
            int(part, 16)
            for part in normalized.split(":")
        ]

        if normalized in {
            "00:00:00:00:00:00",
            "FF:FF:FF:FF:FF:FF",
        }:
            self._show_error(
                "Нельзя использовать нулевой или "
                "широковещательный MAC-адрес."
            )
            return None

        if octets[0] & 1:
            self._show_error(
                "Нельзя использовать групповой "
                "(multicast) MAC-адрес."
            )
            return None

        return {
            "mode": "manual",
            "address": normalized,
        }

    def _update_ipv4_fields(
        self,
        automatic: bool,
    ) -> None:
        manual_enabled = not bool(automatic)

        for widget in (
            self.ipv4_address_edit,
            self.ipv4_prefix_edit,
            self.ipv4_gateway_edit,
        ):
            if widget is not None:
                widget.setEnabled(
                    manual_enabled
                )

        for widget in (
            self.dhcp_hostname_edit,
            self.dhcp_routes_check,
            self.dhcp_dns_routes_check,
        ):
            if widget is not None:
                widget.setEnabled(
                    bool(automatic)
                )

    def _original_dhcp_client_settings(
        self,
    ) -> dict[str, Any]:
        if not self.dhcp_client_supported:
            return {}

        return {
            "hostname": self.original_dhcp_hostname,
            "routes": self.original_dhcp_routes,
            "dns_routes": (
                self.original_dhcp_dns_routes
            ),
        }

    def _validated_dhcp_client_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.dhcp_client_supported:
            return {}

        if (
            self.ipv4_dhcp_check is None
            or not self.ipv4_dhcp_check.isChecked()
        ):
            # При ручном IPv4 параметры DHCP-клиента
            # не применяются и не считаются изменёнными.
            return self._original_dhcp_client_settings()

        if (
            self.dhcp_hostname_edit is None
            or self.dhcp_routes_check is None
            or self.dhcp_dns_routes_check is None
        ):
            self._show_error(
                "Поля DHCP-клиента не созданы."
            )
            return None

        hostname = (
            self.dhcp_hostname_edit.text()
            .strip()
        )

        if not hostname:
            self._show_error(
                "Имя DHCP-клиента не может быть пустым."
            )
            return None

        if len(hostname) > 63:
            self._show_error(
                "Имя DHCP-клиента не должно превышать "
                "63 символа."
            )
            return None

        allowed = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789-."
        )

        if (
            any(
                character not in allowed
                for character in hostname
            )
            or hostname.startswith(("-", "."))
            or hostname.endswith(("-", "."))
        ):
            self._show_error(
                "Имя DHCP-клиента может содержать только "
                "латинские буквы, цифры, точки и дефисы."
            )
            return None

        return {
            "hostname": hostname,
            "routes": (
                self.dhcp_routes_check.isChecked()
            ),
            "dns_routes": (
                self.dhcp_dns_routes_check.isChecked()
            ),
        }

    def _original_ipv4_settings(
        self,
    ) -> dict[str, Any]:
        if not self.ipv4_settings_supported:
            return {}

        if self.original_ipv4_dhcp:
            return {
                "dhcp": True,
            }

        return {
            "dhcp": False,
            "address": self.original_ipv4_address,
            "prefix": self.original_ipv4_prefix,
            "gateway": self.original_ipv4_gateway,
        }

    def _validated_ipv4_settings(
        self,
    ) -> dict[str, Any] | None:
        if not self.ipv4_settings_supported:
            return {}

        if self.ipv4_dhcp_check is None:
            return {}

        if self.ipv4_dhcp_check.isChecked():
            return {
                "dhcp": True,
            }

        if (
            self.ipv4_address_edit is None
            or self.ipv4_prefix_edit is None
            or self.ipv4_gateway_edit is None
        ):
            self._show_error(
                "Поля IPv4 не созданы."
            )
            return None

        address_text = (
            self.ipv4_address_edit.text()
            .strip()
        )
        prefix_text = (
            self.ipv4_prefix_edit.text()
            .strip()
        )
        gateway_text = (
            self.ipv4_gateway_edit.text()
            .strip()
        )

        if not address_text:
            self._show_error(
                "Укажите ручной IPv4-адрес."
            )
            return None

        if not prefix_text:
            self._show_error(
                "Укажите длину префикса IPv4."
            )
            return None

        if not gateway_text:
            self._show_error(
                "Укажите IPv4-адрес шлюза."
            )
            return None

        try:
            address = ipaddress.ip_address(
                address_text
            )
        except ValueError:
            self._show_error(
                "Некорректный IPv4-адрес."
            )
            return None

        if not isinstance(
            address,
            ipaddress.IPv4Address,
        ):
            self._show_error(
                "В поле IPv4-адреса указан IPv6."
            )
            return None

        try:
            prefix = int(prefix_text)
        except ValueError:
            self._show_error(
                "Длина префикса должна быть целым числом."
            )
            return None

        if not 1 <= prefix <= 32:
            self._show_error(
                "Длина префикса должна быть от 1 до 32."
            )
            return None

        try:
            gateway = ipaddress.ip_address(
                gateway_text
            )
        except ValueError:
            self._show_error(
                "Некорректный IPv4-адрес шлюза."
            )
            return None

        if not isinstance(
            gateway,
            ipaddress.IPv4Address,
        ):
            self._show_error(
                "В поле шлюза указан IPv6."
            )
            return None

        return {
            "dhcp": False,
            "address": str(address),
            "prefix": prefix,
            "gateway": str(gateway),
        }

    def _manual_dns_values(self) -> list[str]:
        values = [
            self.primary_dns_edit.text().strip(),
            self.secondary_dns_edit.text().strip(),
        ]

        return [
            value
            for value in values
            if value
        ]

    def _validated_manual_dns(
        self,
    ) -> list[str] | None:
        values = self._manual_dns_values()
        normalized: list[str] = []

        for value in values:
            try:
                address = ipaddress.ip_address(
                    value
                )
            except ValueError:
                self._show_error(
                    (
                        f"Некорректный DNS-адрес: {value}. "
                        "Введите IPv4 или IPv6 адрес."
                    )
                )
                return None

            text = str(address)

            if text in normalized:
                self._show_error(
                    "Основной и резервный DNS не должны "
                    "совпадать."
                )
                return None

            normalized.append(text)

        return normalized

    def _show_error(
        self,
        message: str,
    ) -> None:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle(
            "Проверьте параметры"
        )
        box.setText(message)
        box.setIcon(
            QMessageBox.Icon.Warning
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok
        )

        ok_button = box.button(
            QMessageBox.StandardButton.Ok
        )

        if ok_button is not None:
            ok_button.setText("Хорошо")

        box.exec()

    @staticmethod
    def _parse_mtu(value: Any) -> int | None:
        try:
            mtu = int(float(value))
        except (TypeError, ValueError):
            return None

        return mtu if mtu > 0 else None

    @staticmethod
    def _text(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return ""

        if isinstance(value, (list, tuple, set)):
            items = [
                str(item).strip()
                for item in value
                if str(item).strip()
            ]
            return ", ".join(items)

        if isinstance(value, dict):
            items = [
                str(item).strip()
                for item in value.values()
                if str(item).strip()
            ]
            return ", ".join(items)

        text = str(value).strip()

        if text in {
            "",
            "-",
            "—",
            "None",
            "null",
            "False",
        }:
            return ""

        return text

    @classmethod
    def _status_text(
        cls,
        connection: dict[str, Any],
    ) -> str:
        enabled = InternetConnectionCard._is_enabled(
            connection
        )
        connected = (
            InternetConnectionCard._is_connected(
                connection
            )
        )

        if not enabled:
            return "Выключено"

        if connected:
            return "Подключено"

        return "Нет соединения"

    @staticmethod
    def _format_speed(value: Any) -> str:
        try:
            speed = float(value)
        except (TypeError, ValueError):
            return ""

        if speed <= 0:
            return ""

        if speed >= 1_000_000_000:
            return f"{speed / 1_000_000_000:.1f} Гбит/с"

        if speed >= 1_000_000:
            return f"{speed / 1_000_000:.0f} Мбит/с"

        if speed >= 1_000:
            return f"{speed / 1_000:.0f} Кбит/с"

        return f"{speed:.0f} бит/с"

    @staticmethod
    def _priority_text(
        connection: dict[str, Any],
    ) -> str:
        value = connection.get("global_order")

        try:
            order = int(value)
        except (TypeError, ValueError):
            return ""

        return str(order + 1)


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
