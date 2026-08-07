import math
import os
import sqlite3
from collections import deque
from pathlib import Path
from time import monotonic
from typing import TypedDict

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.connection_worker import RouterMonitor
from core.app_profile import (
    SETTINGS_APPLICATION,
    SETTINGS_ORGANIZATION,
)
from core.credential_store import CredentialStoreError
from core.debug_log import write_priority_debug
from core.database import Database
from core.app_version import APP_NAME, APP_VERSION
from core.services.diagnostics_service import DiagnosticsService
from core.services.runtime_paths import (
    DIAGNOSTICS_EXPORT_DIR,
    LOGS_DIR,
)
from core.services.rescue_mode_service import (
    RESCUED_MODE,
    RESCUER_MODE,
    RescueModeService,
)
from core.services.traffic_service import TrafficService
from core.router_status_worker import RouterStatusWorker
from ui.dialogs.add_router_dialog import AddRouterDialog
from ui.dialogs.smb_file_manager import SmbFileManagerDialog
from ui.dialogs.wired_ipoe_dialog import (
    CableInternetCreationDialog,
    CableProtocolChangeDialog,
)
from ui.dialogs.wireless_isp_dialog import (
    WirelessIspCreationDialog,
)
from ui.dialogs.wifi_network_dialog import (
    WiFiNetworkDialog,
)
from ui.widgets.connection_card import ConnectionCard
from ui.widgets.clients_tab import ClientsTab
from ui.widgets.journal_tab import JournalTab
from ui.widgets.wifi_network_card import WifiNetworkCard
from ui.widgets.wifi_settings_tab import WiFiSettingsTab
from ui.widgets.port_card import PortCard
from ui.widgets.port_overview import PortOverview
from ui.widgets.internet_tab import InternetTab
from ui.widgets.router_list import (
    ROUTER_STATUS_ROLE as ROUTER_LIST_STATUS_ROLE,
    RouterListWidget,
)
from ui.widgets.router_action_panel import (
    RouterMonitorActionPanel,
)
from ui.widgets.router_address_links import RouterAddressLinks
from ui.widgets.system_metric_chip import SystemMetricChip
from ui.widgets.rescue_mode_switch import RescueModeSwitch
from ui.widgets.refresh_button import AddToolButton, RefreshToolButton
from ui.message_boxes import ask_yes_no


MONITOR_INTERVALS = {
    "1 секунда": 1_000,
    "2 секунды": 2_000,
    "5 секунд": 5_000,
    "10 секунд": 10_000,
    "30 секунд": 30_000,
    "60 секунд": 60_000,
}


class TrafficHistory(TypedDict):
    rx: deque[float]
    tx: deque[float]
    time: deque[float]
    last_sample_time: float | None


def coerce_bool_setting(
    value: object,
    default: bool,
) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value or "").strip().lower()

    if normalized in {"1", "true", "yes", "on", "да"}:
        return True

    if normalized in {"0", "false", "no", "off", "нет"}:
        return False

    return bool(default)


def load_monitor_preferences(
    settings: QSettings,
) -> tuple[int, bool, bool]:
    allowed_intervals = set(MONITOR_INTERVALS.values())

    try:
        interval_ms = int(
            settings.value(
                "monitor/interval_ms",
                2_000,
            )
        )
    except (TypeError, ValueError):
        interval_ms = 2_000

    if interval_ms not in allowed_intervals:
        interval_ms = 2_000

    auto_refresh_enabled = coerce_bool_setting(
        settings.value(
            "monitor/auto_refresh_enabled",
            True,
        ),
        True,
    )
    reachability_enabled = coerce_bool_setting(
        settings.value(
            "monitor/reachability_enabled",
            True,
        ),
        True,
    )
    return (
        interval_ms,
        auto_refresh_enabled,
        reachability_enabled,
    )


def create_application_settings() -> QSettings:
    portable_path = os.environ.get(
        "KEENETIC_MANAGER_SETTINGS_FILE",
        "",
    ).strip()
    if portable_path:
        settings_path = Path(portable_path).expanduser().resolve()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        return QSettings(
            str(settings_path),
            QSettings.Format.IniFormat,
        )
    return QSettings(
        SETTINGS_ORGANIZATION,
        SETTINGS_APPLICATION,
    )



def _priority_trace(
    router_id: int | None,
    message: str,
) -> None:
    router_suffix = (
        str(router_id)
        if router_id is not None
        else "unknown"
    )
    write_priority_debug(
        LOGS_DIR / f"priority_debug_router_{router_suffix}.log",
        f"router_id={router_suffix} | {message}",
    )



class MainWindow(QMainWindow):
    ROUTER_ID_ROLE = Qt.ItemDataRole.UserRole
    ROUTER_STATUS_ROLE = ROUTER_LIST_STATUS_ROLE
    TAB_TITLES = (
        "Мониторинг",
        "Интернет",
        "Wi-Fi",
        "Клиенты",
        "Настройки",
        "Журнал",
    )

    def __init__(self) -> None:
        super().__init__()

        self.database = Database()
        self.settings = create_application_settings()
        (
            self.monitor_interval_ms,
            self.auto_refresh_enabled,
            self.reachability_checks_enabled,
        ) = load_monitor_preferences(
            self.settings
        )
        self.monitor_intervals = dict(
            MONITOR_INTERVALS
        )
        self.routers: list[dict] = []

        self._shutdown_in_progress = False
        self._shutdown_ready = False
        self._shutdown_threads: list[object] = []
        self._shutdown_poll_timer = QTimer(self)
        self._shutdown_poll_timer.setInterval(100)
        self._shutdown_poll_timer.timeout.connect(
            self._poll_shutdown_threads
        )

        # Состояние, уже сохранённое в SQLite. Оно позволяет
        # не выполнять UPDATE и повторный SELECT каждые две
        # секунды на каждом успешном цикле мониторинга.
        self.persisted_router_status: dict[int, bool] = {}
        self.last_seen_persisted_at: dict[int, float] = {}
        self.last_seen_write_interval_seconds = 60.0

        self.router_monitors: dict[
            int,
            RouterMonitor,
        ] = {}

        # Потоки, которым уже отправлена команда stop(), но
        # которые ещё завершают текущий сетевой запрос.
        # Сильная ссылка предотвращает уничтожение QThread
        # до фактического сигнала finished.
        self.stopping_router_monitors: dict[
            int,
            RouterMonitor,
        ] = {}

        self.connection_results: dict[int, object] = {}
        # Последний результат сохраняется и после подтверждённого
        # отключения, чтобы офлайн-архив содержал причину сбоя.
        self.last_monitor_results: dict[int, object] = {}
        self.pending_rescue_modes: dict[int, str] = {}

        # Открытый мастер кабельного Интернета получает свежий
        # список LAN-портов из каждого нового снимка роутера.
        self.active_cable_dialogs: dict[
            int,
            CableInternetCreationDialog,
        ] = {}

        # Открытый мастер Wireless ISP получает свежую
        # доступность диапазонов и результаты сканирования.
        self.active_wireless_isp_dialogs: dict[
            int,
            WirelessIspCreationDialog,
        ] = {}

        # Режим разработчика хранится отдельно для каждого
        # роутера и доступен даже без активного подключения.
        self.developer_mode_by_router: dict[int, bool] = {}

        # Открытые SMB-окна храним по ID роутера, чтобы окно
        # не уничтожалось сборщиком мусора.
        self.smb_file_managers: dict[
            int,
            SmbFileManagerDialog,
        ] = {}

        # Независимая доступность всех добавленных роутеров.
        # Она не означает полноценное подключение к мониторингу.
        self.router_reachability: dict[
            int,
            bool | None,
        ] = {}
        self.router_reachable_address: dict[
            int,
            str,
        ] = {}

        self.router_status_worker = RouterStatusWorker(
            interval_seconds=3.0,
            timeout_seconds=1.2,
            paused=(
                not self.reachability_checks_enabled
            ),
            parent=self,
        )
        self.router_status_worker.status_changed.connect(
            self.router_reachability_changed
        )

        # Результаты групповых операций над объединённой
        # Wi-Fi сетью 2.4/5 ГГц. Окно показывается один раз
        # после завершения команд для всех интерфейсов.
        self.pending_interface_batches: dict[
            int,
            dict[str, object],
        ] = {}

        self.traffic_history: dict[
            tuple[int, str],
            TrafficHistory,
        ] = {}
        # Каждый подключённый роутер обновляется
        # собственным RouterMonitor.

        self.setWindowTitle(
            f"{APP_NAME} {APP_VERSION}"
        )
        self.resize(1400, 850)
        self.setMinimumSize(900, 600)

        self.create_interface()
        self.connect_signals()
        self.load_routers()

        self.router_status_worker.set_routers(
            self.routers
        )
        self.router_status_worker.start()

        # До первого успешного подключения все динамические
        # поля и разделы остаются пустыми.
        current_router_id = self.get_selected_router_id()

        if (
            current_router_id is None
            or current_router_id
            not in self.connection_results
        ):
            self.clear_router_state()

        # При запуске программы ни один роутер
        # автоматически не подключается.

    def create_interface(self) -> None:
        splitter = QSplitter(
            Qt.Orientation.Horizontal
        )

        # Левая панель
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.left_panel_layout = left_layout

        left_header = QWidget(left_panel)
        left_header_layout = QHBoxLayout(left_header)
        left_header_layout.setContentsMargins(0, 0, 0, 0)
        left_header_layout.setSpacing(6)

        left_title = QLabel("Роутеры", left_header)
        left_title.setStyleSheet(
            "font-size: 20px; font-weight: bold;"
        )

        self.refresh_button = RefreshToolButton(
            left_header,
            glyph_size=20,
        )
        self.refresh_button.setObjectName(
            "routerHeaderAction"
        )
        self.refresh_button.setFixedSize(34, 34)
        self.refresh_button.setToolTip(
            "Обновить все подключённые роутеры"
        )
        self.refresh_button.setAccessibleName(
            "Обновить все подключённые роутеры"
        )

        self.add_button = AddToolButton(
            left_header,
            glyph_size=18,
        )
        self.add_button.setObjectName(
            "routerHeaderAction"
        )
        self.add_button.setFixedSize(34, 34)
        self.add_button.setToolTip("Добавить роутер")
        self.add_button.setAccessibleName("Добавить роутер")

        left_header_layout.addWidget(left_title)
        left_header_layout.addStretch()
        left_header_layout.addWidget(self.refresh_button)
        left_header_layout.addWidget(self.add_button)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Поиск по названию, IP или KeenDNS..."
        )

        self.monitor_controls = QWidget()
        monitor_layout = QGridLayout(self.monitor_controls)
        monitor_layout.setContentsMargins(0, 0, 0, 0)
        monitor_layout.setHorizontalSpacing(8)
        monitor_layout.setVerticalSpacing(4)

        self.auto_refresh_checkbox = QCheckBox(
            "Автообновление данных"
        )
        self.auto_refresh_checkbox.setChecked(
            self.auto_refresh_enabled
        )
        self.auto_refresh_checkbox.setToolTip(
            "Периодически обновляет полные данные только у "
            "подключённых роутеров. Проверка доступности "
            "настраивается отдельно."
        )

        self.refresh_interval_combo = QComboBox()
        self.refresh_interval_combo.addItems(
            self.monitor_intervals.keys()
        )
        interval_name = next(
            (
                name
                for name, value
                in self.monitor_intervals.items()
                if value == self.monitor_interval_ms
            ),
            "2 секунды",
        )
        self.refresh_interval_combo.setCurrentText(
            interval_name
        )
        self.refresh_interval_combo.setToolTip(
            "Минимальная пауза после завершения предыдущего "
            "обновления. Запросы одного роутера не перекрываются."
        )

        self.reachability_checkbox = QCheckBox(
            "Проверять доступность"
        )
        self.reachability_checkbox.setChecked(
            self.reachability_checks_enabled
        )
        self.reachability_checkbox.setToolTip(
            "Отдельно проверяет доступность всех сохранённых "
            "роутеров и обновляет цветные индикаторы списка."
        )

        monitor_layout.addWidget(
            self.auto_refresh_checkbox,
            0,
            0,
        )
        monitor_layout.addWidget(
            self.refresh_interval_combo,
            0,
            1,
        )
        monitor_layout.addWidget(
            self.reachability_checkbox,
            1,
            0,
            1,
            2,
        )

        self.router_list = RouterListWidget()
        self.router_list.setMinimumWidth(300)

        self.router_action_panel = RouterMonitorActionPanel(
            left_panel
        )
        self.connect_button = (
            self.router_action_panel.connect_button
        )
        self.disconnect_button = (
            self.router_action_panel.disconnect_button
        )

        management_actions = QWidget(left_panel)
        management_layout = QHBoxLayout(management_actions)
        management_layout.setContentsMargins(0, 0, 0, 0)
        management_layout.setSpacing(8)

        self.edit_button = QPushButton(
            "Изменить",
            management_actions,
        )
        self.edit_button.setObjectName("routerEditButton")
        self.delete_button = QPushButton(
            "Удалить",
            management_actions,
        )
        self.delete_button.setObjectName("routerDeleteButton")

        management_layout.addWidget(self.edit_button, 1)
        management_layout.addWidget(self.delete_button, 1)

        self.refresh_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.delete_button.setEnabled(False)

        left_layout.addWidget(left_header)
        left_layout.addWidget(self.search_input)
        left_layout.addWidget(self.monitor_controls)
        left_layout.addWidget(self.router_list, 1)
        left_layout.addWidget(self.router_action_panel)
        left_layout.addWidget(management_actions)

        # Правая панель
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(14)

        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        # Высота остаётся неизменной до и после подключения.
        header_card.setFixedHeight(148)

        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(
            14,
            8,
            14,
            8,
        )
        header_layout.setSpacing(12)
        header_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetDefaultConstraint
        )

        header_text = QWidget()
        header_text.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        header_text_layout = QVBoxLayout(
            header_text
        )
        header_text_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        header_text_layout.setSpacing(2)
        header_text_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinimumSize
        )

        self.router_title = QLabel(
            "Выберите роутер"
        )
        self.router_title.setObjectName("routerTitle")
        # A long user-defined router name must not enlarge the header's
        # minimum width and squeeze the rescue-mode controls out of reach.
        # QLabel keeps the complete text; the layout may clip it and the
        # tooltip below exposes the full value.
        self.router_title.setMinimumWidth(0)
        self.router_title.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        self.status_label = QLabel(
            "● Не подключён"
        )
        self.status_label.setObjectName("statusLabel")

        self.router_address_links = RouterAddressLinks(
            header_text
        )

        self.connected_address_label = QLabel(
            "Фактический адрес: —\n"
            "Подключение: —"
        )
        self.connected_address_label.setWordWrap(False)

        # Постоянно резервируем место ровно под две строки.
        # Благодаря этому способ подключения не обрезается,
        # а высота панели не меняется после соединения.
        address_metrics = (
            self.connected_address_label.fontMetrics()
        )
        self.connected_address_label.setFixedHeight(
            address_metrics.lineSpacing() * 2 + 4
        )
        self.connected_address_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
        )

        header_text_layout.addWidget(
            self.router_title
        )
        header_text_layout.addWidget(
            self.status_label
        )
        header_text_layout.addWidget(
            self.router_address_links
        )
        header_text_layout.addWidget(
            self.connected_address_label
        )

        self.port_overview = PortOverview(
            header_card
        )

        self.rescue_mode_switch = RescueModeSwitch(
            header_card
        )
        self.rescue_mode_switch.mode_requested.connect(
            self.change_rescue_mode
        )

        header_layout.addWidget(
            header_text,
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )
        header_layout.addWidget(
            self.rescue_mode_switch,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        header_layout.addWidget(
            self.port_overview,
            0,
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter,
        )

        dashboard = QWidget()
        dashboard_layout = QGridLayout(dashboard)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setHorizontalSpacing(14)
        dashboard_layout.setVerticalSpacing(14)

        device_card, device_layout = (
            self.create_dashboard_card(
                "Устройство"
            )
        )
        self.device_card = device_card
        self.model_label = QLabel("—")
        self.model_label.setObjectName("metricValue")
        self.version_label = QLabel(
            "KeeneticOS: —"
        )
        self.uptime_label = QLabel(
            "Время работы: —"
        )

        device_content = QWidget()
        device_content_layout = QHBoxLayout(
            device_content
        )
        device_content_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        device_content_layout.setSpacing(18)

        device_information = QWidget()
        device_information_layout = QVBoxLayout(
            device_information
        )
        device_information_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        device_information_layout.setSpacing(7)
        device_information_layout.addWidget(
            self.model_label
        )
        device_information_layout.addWidget(
            self.version_label
        )
        device_information_layout.addWidget(
            self.uptime_label
        )

        self.cpu_metric = SystemMetricChip(
            "CPU",
            "cpu",
            device_content,
        )
        self.ram_metric = SystemMetricChip(
            "Память",
            "memory",
            device_content,
        )

        # Совместимые ссылки сохраняют прежний контракт
        # обновления динамических показателей.
        self.cpu_label = self.cpu_metric.value_label
        self.cpu_progress = self.cpu_metric.progress
        self.ram_label = self.ram_metric.value_label
        self.ram_progress = self.ram_metric.progress

        system_metrics = QWidget()
        system_metrics_layout = QHBoxLayout(
            system_metrics
        )
        system_metrics_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        system_metrics_layout.setSpacing(10)
        system_metrics_layout.addWidget(
            self.cpu_metric
        )
        system_metrics_layout.addWidget(
            self.ram_metric
        )

        device_content_layout.addWidget(
            device_information,
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )
        device_content_layout.addWidget(
            system_metrics,
            0,
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter,
        )

        device_layout.addWidget(device_content)

        internet_card, internet_layout = (
            self.create_dashboard_card(
                "Активные подключения к Интернету"
            )
        )

        self.internet_connections_container = QWidget()
        self.internet_connections_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.internet_connections_layout = QVBoxLayout(
            self.internet_connections_container
        )
        self.internet_connections_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )
        self.internet_connections_layout.setSpacing(12)
        self.internet_connection_cards: dict[
            str,
            ConnectionCard,
        ] = {}
        self.internet_connection_empty_label: QLabel | None = None

        internet_layout.addWidget(
            self.internet_connections_container
        )

        wifi_card, wifi_layout = (
            self.create_dashboard_card(
                "Мои сети и Wi-Fi"
            )
        )

        self.wifi_networks_container = QWidget()
        self.wifi_networks_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.wifi_networks_layout = QVBoxLayout(
            self.wifi_networks_container
        )
        self.wifi_networks_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )
        self.wifi_networks_layout.setSpacing(12)
        wifi_layout.addWidget(
            self.wifi_networks_container
        )

        ports_card, ports_layout = (
            self.create_dashboard_card(
                "Порты"
            )
        )

        self.ports_container = QWidget()
        self.ports_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.ports_layout = QVBoxLayout(
            self.ports_container
        )
        self.ports_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )
        self.ports_layout.setSpacing(8)
        self.port_cards: dict[str, PortCard] = {}

        ports_layout.addWidget(
            self.ports_container
        )

        dashboard_layout.addWidget(
            device_card,
            0,
            0,
            1,
            2,
        )
        dashboard_layout.addWidget(
            internet_card,
            1,
            0,
            1,
            2,
        )
        dashboard_layout.addWidget(
            wifi_card,
            2,
            0,
            1,
            2,
        )
        dashboard_layout.addWidget(
            ports_card,
            3,
            0,
            1,
            2,
        )

        dashboard_scroll = QScrollArea()
        dashboard_scroll.setWidgetResizable(True)
        # Полоса прокрутки всегда зарезервирована.
        # Иначе после появления карточек портов вся область
        # мониторинга сдвигается на ширину scrollbar.
        dashboard_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        dashboard_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        dashboard_scroll.setWidget(dashboard)

        dashboard.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.tabs = QTabWidget()

        self.internet_tab = InternetTab()
        self.internet_tab.priorities_changed.connect(
            self.internet_priorities_changed
        )
        self.internet_tab.interface_state_change_requested.connect(
            self.change_connection_interface_state
        )
        self.internet_tab.interface_reconnect_requested.connect(
            self.reconnect_connection_interface
        )
        self.internet_tab.interface_settings_change_requested.connect(
            self.change_connection_interface_settings
        )
        self.internet_tab.connection_delete_requested.connect(
            self.delete_internet_connection
        )
        self.internet_tab.connection_protocol_change_requested.connect(
            self.change_cable_connection_protocol
        )
        self.internet_tab.connection_creation_requested.connect(
            self.open_connection_creation
        )

        self.clients_tab = ClientsTab()
        self.clients_tab.refresh_requested.connect(
            self.refresh_selected_router
        )
        self.clients_tab.interface_state_change_requested.connect(
            self.change_network_interfaces_state
        )
        self.clients_tab.wifi_access_change_requested.connect(
            self.change_wifi_client_access
        )
        self.clients_tab.message_requested.connect(
            lambda message: self.statusBar().showMessage(
                str(message),
                3000,
            )
        )
        self.wifi_tab = WiFiSettingsTab()
        self.wifi_tab.create_requested.connect(
            self.open_wifi_network_creation
        )
        self.wifi_tab.edit_requested.connect(
            self.open_wifi_network_edit
        )
        self.wifi_tab.state_change_requested.connect(
            self.change_network_interfaces_state
        )
        self.wifi_tab.delete_requested.connect(
            self.delete_wifi_network
        )
        self.journal_tab = JournalTab()
        self.journal_tab.refresh_requested.connect(
            self.refresh_system_log
        )
        self.journal_tab.message_requested.connect(
            lambda message: self.statusBar().showMessage(
                str(message),
                5000,
            )
        )
        self.settings_tab = self.create_settings_tab()

        self.tab_titles = list(self.TAB_TITLES)
        self.connected_tab_widgets = [
            dashboard_scroll,
            self.internet_tab,
            self.wifi_tab,
            self.clients_tab,
            self.settings_tab,
            self.journal_tab,
        ]
        self.disconnected_tab_widgets = [
            (
                self.settings_tab
                if title == "Настройки"
                else self.create_router_disconnected_page()
            )
            for title in self.tab_titles
        ]

        # Страницы вкладок никогда не извлекаются из QTabWidget. Для каждой
        # вкладки создаётся постоянный контейнер, а при смене роутера внутри
        # него переключается только рабочее или офлайн-содержимое. Это не
        # позволяет Windows на мгновение превратить снятую страницу в
        # отдельное системное окно с кнопками □ и ×.
        self.tab_page_containers: list[QWidget] = []
        self.tab_page_stacks: list[QStackedLayout] = []
        for title, connected_page, disconnected_page in zip(
            self.tab_titles,
            self.connected_tab_widgets,
            self.disconnected_tab_widgets, strict=False,
        ):
            container = QWidget()
            stack = QStackedLayout(container)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.addWidget(connected_page)
            if disconnected_page is not connected_page:
                stack.addWidget(disconnected_page)
            stack.setCurrentWidget(disconnected_page)
            self.tab_page_containers.append(container)
            self.tab_page_stacks.append(stack)
            self.tabs.addTab(container, title)

        self.configure_journal_corner_tab()
        self.tabs.currentChanged.connect(
            self.current_tab_changed
        )

        self.router_content_is_connected = False

        right_layout.addWidget(header_card)
        right_layout.addWidget(self.tabs)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)

        splitter.setSizes([330, 1170])
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)
        self.set_router_content_connected(False)

        self.setStyleSheet(
            """
            QToolButton#routerHeaderAction {
                color: #cbd5e1;
                background: transparent;
                border: 1px solid palette(mid);
                border-radius: 8px;
                font-size: 19px;
                font-weight: 600;
            }

            QToolButton#routerHeaderAction:hover:enabled {
                color: #ffffff;
                background: rgba(96, 165, 250, 31);
                border-color: #60a5fa;
            }

            QToolButton#routerHeaderAction:disabled {
                color: #64748b;
                background: transparent;
                border-color: rgba(100, 116, 139, 89);
            }

            QPushButton#routerEditButton,
            QPushButton#routerDeleteButton {
                background: transparent;
                border: 1px solid palette(mid);
                border-radius: 8px;
                font-weight: 600;
            }

            QPushButton#routerEditButton:hover:enabled {
                background: rgba(96, 165, 250, 26);
                border-color: #60a5fa;
            }

            QPushButton#routerDeleteButton:hover:enabled {
                color: #fecdd3;
                background: rgba(251, 113, 133, 26);
                border-color: #fb7185;
            }

            QLabel#routerTitle {
                font-size: 26px;
                font-weight: 700;
            }

            QLabel#statusLabel {
                font-size: 16px;
                font-weight: 600;
            }

            QLabel#cardTitle {
                font-size: 13px;
                font-weight: 600;
            }

            QLabel#metricValue {
                font-size: 20px;
                font-weight: 700;
            }

            QFrame#systemMetricChip {
                background: rgba(30, 41, 59, 66);
                border: 1px solid rgba(100, 116, 139, 89);
                border-radius: 10px;
            }

            QLabel#systemMetricTitle {
                color: #94a3b8;
                font-size: 12px;
                font-weight: 600;
                border: none;
                background: transparent;
            }

            QLabel#systemMetricValue {
                color: palette(text);
                font-size: 19px;
                font-weight: 700;
                border: none;
                background: transparent;
            }

            QProgressBar#cpuMetricProgress,
            QProgressBar#memoryMetricProgress {
                min-height: 5px;
                max-height: 5px;
                border: none;
                border-radius: 2px;
                background: rgba(100, 116, 139, 64);
            }

            QProgressBar {
                min-height: 18px;
                text-align: center;
            }

            QPushButton {
                min-height: 30px;
            }

            QLineEdit,
            QComboBox {
                min-height: 28px;
            }

            QFrame#internetConnectionCard {
                border: 1px solid palette(mid);
                border-radius: 10px;
            }

            QLabel#internetCardTitle {
                font-size: 17px;
                font-weight: 700;
            }

            QLabel#internetStatusOnline {
                font-size: 14px;
                font-weight: 600;
            }

            QLabel#emptyInternetState {
                font-size: 16px;
                font-weight: 600;
                min-height: 150px;
            }

            QFrame#detailedConnectionCard {
                border: 1px solid palette(mid);
                border-radius: 12px;
            }

            QLabel#connectionRoleTitle {
                font-size: 19px;
                font-weight: 700;
            }

            QLabel#connectionInterfaceTitle {
                font-size: 14px;
            }

            QLabel#connectionStatusOnline {
                font-weight: 700;
            }

            QLabel#connectionStatusOffline {
                font-weight: 700;
            }

            QLabel#connectionFieldName {
                font-weight: 600;
            }

            QFrame#wifiNetworkCard,
            QFrame#wifiSettingsNetworkCard {
                border: 1px solid palette(mid);
                border-radius: 10px;
            }

            QFrame#wifiClientCard {
                border: 1px solid palette(mid);
                border-radius: 8px;
            }

            QLabel#wifiGroupTitle {
                font-size: 17px;
                font-weight: 700;
            }

            QLabel#wifiSsidTitle {
                font-size: 15px;
            }

            QLabel#wifiClientsTitle,
            QLabel#wifiClientName {
                font-size: 14px;
                font-weight: 700;
            }

            QLabel#wifiFieldName {
                font-weight: 600;
            }

            QLabel#settingsDisconnectedLabel,
            QLabel#routerDisconnectedLabel {
                font-size: 14px;
                font-weight: 400;
            }
            """
        )

        # Цвета и оформление задаются глобальной темой
        # приложения в main.py. Окно не фиксирует белый фон.
        self.statusBar().showMessage("Готово")

    def create_router_disconnected_page(
        self,
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            18,
            18,
            18,
            18,
        )
        layout.setSpacing(0)

        label = QLabel(
            "Роутер не подключён"
        )
        label.setObjectName(
            "routerDisconnectedLabel"
        )
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )

        layout.addWidget(
            label,
            0,
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop,
        )
        layout.addStretch()

        return page

    def set_router_content_connected(
        self,
        connected: bool,
    ) -> None:
        """
        Физически заменяет страницы всех вкладок.

        При отключении рабочие виджеты отсутствуют в QTabWidget,
        поэтому они не могут остаться видимыми.
        """
        if not hasattr(self, "tab_page_stacks"):
            return

        connected = bool(connected)
        if hasattr(self, "update_settings_connection_state"):
            self.update_settings_connection_state(connected)

        desired_widgets = (
            self.connected_tab_widgets
            if connected
            else self.disconnected_tab_widgets
        )
        if (
            getattr(self, "router_content_is_connected", None)
            is connected
            and all(
                stack.currentWidget() is widget
                for stack, widget in zip(
                    self.tab_page_stacks,
                    desired_widgets, strict=False,
                )
            )
        ):
            return

        current_index = self.tabs.currentIndex()
        tabs_blocker = QSignalBlocker(self.tabs)
        self.tabs.setUpdatesEnabled(False)
        try:
            for stack, widget in zip(
                self.tab_page_stacks,
                desired_widgets, strict=False,
            ):
                if stack.currentWidget() is not widget:
                    stack.setCurrentWidget(widget)

            if 0 <= current_index < self.tabs.count():
                self.tabs.setCurrentIndex(
                    current_index
                )
        finally:
            del tabs_blocker
            self.tabs.setUpdatesEnabled(True)

        self.router_content_is_connected = connected
        self.tabs.updateGeometry()
        self.tabs.update()

    def configure_journal_corner_tab(self) -> None:
        self.journal_tab_index = self.tab_titles.index("Журнал")
        self.tabs.setTabVisible(self.journal_tab_index, False)

        self.journal_tab_button = QPushButton(
            "Журнал",
            self.tabs,
        )
        self.journal_tab_button.setObjectName(
            "journalCornerTab"
        )
        self.journal_tab_button.setCheckable(True)
        self.journal_tab_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.journal_tab_button.setToolTip(
            "Открыть журнал роутера"
        )
        self.journal_tab_button.clicked.connect(
            self.open_journal_tab
        )

        corner = QWidget(self.tabs)
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(0)
        corner_layout.addWidget(self.journal_tab_button)
        self.tabs.setCornerWidget(
            corner,
            Qt.Corner.TopRightCorner,
        )

    def open_journal_tab(self) -> None:
        self.journal_tab_button.setChecked(True)
        self.tabs.setCurrentIndex(self.journal_tab_index)

    def current_tab_changed(self, index: int) -> None:
        if not 0 <= int(index) < len(self.tab_titles):
            return

        journal_selected = (
            self.tab_titles[int(index)] == "Журнал"
        )
        self.journal_tab_button.setChecked(journal_selected)
        if not journal_selected:
            return
        self.load_current_journal_if_needed()

    def load_current_journal_if_needed(self) -> None:
        router_id = self.get_selected_router_id()
        if (
            router_id is None
            or not self.router_is_fully_connected(router_id)
        ):
            return
        self.journal_tab.set_router(router_id)
        if self.journal_tab.needs_initial_load():
            self.journal_tab.request_refresh()

    def refresh_system_log(self, max_lines: int = 500) -> None:
        router_id = self.get_selected_router_id()
        monitor = (
            self.router_monitors.get(router_id)
            if router_id is not None
            else None
        )
        if (
            router_id is None
            or monitor is None
            or not self.router_is_fully_connected(router_id)
        ):
            self.journal_tab.set_error(
                "Сначала подключитесь к выбранному роутеру."
            )
            return
        self.journal_tab.set_loading(True)
        monitor.request_system_log(int(max_lines))

    def system_log_completed(
        self,
        router_id: int,
        success: bool,
        message: str,
        entries: object,
    ) -> None:
        if int(router_id) != self.get_selected_router_id():
            return
        if success:
            normalized = (
                list(entries)
                if isinstance(entries, list)
                else []
            )
            self.journal_tab.set_entries(
                normalized,
                message=message,
            )
            self.statusBar().showMessage(message, 3000)
            return
        self.journal_tab.set_error(message)
        self.statusBar().showMessage(message, 5000)

    def create_dashboard_card(
        self,
        title: str,
    ) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("dashboardCard")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            18,
            16,
            18,
            16,
        )
        layout.setSpacing(9)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)

        return card, layout

    def create_settings_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(
            18,
            18,
            18,
            18,
        )
        page_layout.setSpacing(14)

        self.settings_online_content = QWidget()
        online_layout = QVBoxLayout(
            self.settings_online_content
        )
        online_layout.setContentsMargins(0, 0, 0, 0)
        online_layout.setSpacing(14)

        monitoring_card, monitoring_layout = (
            self.create_dashboard_card(
                "Обновление и доступность"
            )
        )
        monitoring_description = QLabel(
            "Управляет фоновым обновлением подключённых роутеров "
            "и независимой проверкой их доступности."
        )
        monitoring_description.setWordWrap(True)
        monitoring_layout.addWidget(monitoring_description)
        if hasattr(self, "left_panel_layout"):
            self.left_panel_layout.removeWidget(
                self.monitor_controls
            )
        monitoring_layout.addWidget(self.monitor_controls)
        online_layout.addWidget(monitoring_card)

        compatibility_card, compatibility_layout = (
            self.create_dashboard_card(
                "Совместимость роутера"
            )
        )
        self.settings_compatibility_label = QLabel(
            "Профиль возможностей будет определён после подключения."
        )
        self.settings_compatibility_label.setObjectName(
            "settingsCompatibilityLabel"
        )
        self.settings_compatibility_label.setWordWrap(True)
        compatibility_layout.addWidget(
            self.settings_compatibility_label
        )
        online_layout.addWidget(compatibility_card)

        # Совместимое имя сохраняет прежний контракт интерфейса.
        self.settings_connected_content = self.settings_online_content

        self.developer_card, developer_layout = (
            self.create_dashboard_card(
                "Режим разработчика"
            )
        )

        description = QLabel(
            "Собирает безопасные RCI-снимки и журнал ошибок. "
            "Пароли, токены, ключи и другие секреты "
            "удаляются перед сохранением."
        )
        description.setWordWrap(True)

        self.developer_mode_checkbox = QCheckBox(
            "Включить режим разработчика"
        )
        self.developer_mode_checkbox.setChecked(False)

        self.create_diagnostic_button = QPushButton(
            "Создать диагностический архив"
        )
        self.clear_diagnostic_button = QPushButton(
            "Очистить диагностические данные"
        )

        self.diagnostic_status_label = QLabel(
            "Режим разработчика для выбранного "
            "роутера выключен."
        )
        self.diagnostic_status_label.setWordWrap(True)

        developer_layout.addWidget(description)
        developer_layout.addWidget(
            self.developer_mode_checkbox
        )
        developer_layout.addWidget(
            self.create_diagnostic_button
        )
        developer_layout.addWidget(
            self.clear_diagnostic_button
        )
        developer_layout.addWidget(
            self.diagnostic_status_label
        )

        page_layout.addWidget(
            self.settings_online_content
        )
        page_layout.addWidget(
            self.developer_card
        )
        page_layout.addStretch()

        self.create_diagnostic_button.clicked.connect(
            self.create_diagnostic_archive
        )
        self.clear_diagnostic_button.clicked.connect(
            self.clear_diagnostic_data
        )
        self.developer_mode_checkbox.toggled.connect(
            self.developer_mode_changed
        )

        self.sync_developer_mode_for_selected_router()
        return page

    def update_settings_connection_state(
        self,
        connected: bool,
    ) -> None:
        """Показывает требующие подключения настройки только онлайн."""
        connected = bool(connected)
        if hasattr(self, "settings_online_content"):
            self.settings_online_content.setVisible(connected)
        if hasattr(self, "clear_diagnostic_button"):
            self.clear_diagnostic_button.setVisible(connected)

    def update_settings_router_profile(self, result: object) -> None:
        if not hasattr(self, "settings_compatibility_label"):
            return
        capabilities = getattr(
            result,
            "router_capabilities",
            None,
        )
        if not isinstance(capabilities, dict):
            capabilities = {}
        mode = str(
            capabilities.get("wifi_management_mode") or "monitoring"
        ).strip().lower()
        if mode == "mws":
            text = (
                "Полная поддержка: мониторинг и управление Wi‑Fi "
                "через MWS."
            )
            support_mode = "supported"
        elif mode == "legacy":
            text = (
                "Совместимый legacy‑режим KeeneticOS 4: управление "
                "физическими точками доступа."
            )
            support_mode = "legacy"
        else:
            text = (
                "Ограниченная поддержка: мониторинг доступен, "
                "изменение Wi‑Fi для этой прошивки отключено."
            )
            support_mode = "monitoring"
        self.settings_compatibility_label.setText(text)
        self.settings_compatibility_label.setProperty(
            "supportMode",
            support_mode,
        )
        self.settings_compatibility_label.style().unpolish(
            self.settings_compatibility_label
        )
        self.settings_compatibility_label.style().polish(
            self.settings_compatibility_label
        )

    def developer_mode_changed(
        self,
        enabled: bool,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            blocker = QSignalBlocker(
                self.developer_mode_checkbox
            )
            self.developer_mode_checkbox.setChecked(False)
            del blocker
            self.update_diagnostic_controls()
            return

        self.developer_mode_by_router[router_id] = bool(enabled)
        self.update_diagnostic_controls()

        connected = self.router_is_fully_connected(router_id)
        if enabled:
            self.diagnostic_status_label.setText(
                (
                    "Режим разработчика включён. Можно создать "
                    "диагностический архив текущего подключения."
                    if connected
                    else (
                        "Режим разработчика включён. Роутер сейчас "
                        "не подключён, поэтому будет создан "
                        "офлайн-архив с локальными журналами и "
                        "историей попыток подключения."
                    )
                )
            )
        else:
            self.diagnostic_status_label.setText(
                "Режим разработчика для выбранного роутера выключен."
            )

        self.statusBar().showMessage(
            (
                f"Режим разработчика включён для роутера {router_id}"
                if enabled
                else (
                    f"Режим разработчика выключен для роутера {router_id}"
                )
            ),
            3000,
        )

    def router_is_fully_connected(
        self,
        router_id: int | None,
    ) -> bool:
        result = (
            self.connection_results.get(router_id)
            if router_id is not None
            else None
        )
        return bool(
            router_id is not None
            and router_id in self.router_monitors
            and result is not None
            and getattr(result, "success", False)
        )

    def sync_developer_mode_for_selected_router(
        self,
    ) -> None:
        router_id = self.get_selected_router_id()
        selected = router_id is not None
        connected = self.router_is_fully_connected(router_id)
        enabled = bool(
            selected
            and self.developer_mode_by_router.get(
                router_id,
                False,
            )
        )

        blocker = QSignalBlocker(
            self.developer_mode_checkbox
        )
        self.developer_mode_checkbox.setChecked(enabled)
        del blocker

        self.update_settings_connection_state(connected)

        if not selected:
            self.diagnostic_status_label.setText(
                "Выберите роутер для создания диагностического архива."
            )
        elif enabled and connected:
            self.diagnostic_status_label.setText(
                "Режим разработчика включён для выбранного роутера."
            )
        elif enabled:
            self.diagnostic_status_label.setText(
                "Режим разработчика включён. Архив можно создать "
                "даже без активного подключения к роутеру."
            )
        else:
            self.diagnostic_status_label.setText(
                "Режим разработчика для выбранного роутера выключен."
            )

        self.update_diagnostic_controls()

    def disable_developer_mode_for_router(
        self,
        router_id: int,
    ) -> None:
        # Отключение HTTP-сессии не выключает режим разработчика.
        # Это необходимо для создания офлайн-диагностики.
        if router_id == self.get_selected_router_id():
            self.sync_developer_mode_for_selected_router()

    def update_diagnostic_controls(self) -> None:
        router_id = self.get_selected_router_id()
        selected = router_id is not None
        enabled = bool(
            selected
            and self.developer_mode_by_router.get(
                router_id,
                False,
            )
        )

        self.developer_mode_checkbox.setEnabled(selected)
        self.create_diagnostic_button.setEnabled(enabled)
        self.clear_diagnostic_button.setEnabled(enabled)

    def diagnostic_application_settings(
        self,
    ) -> dict[str, object]:
        return {
            "application_version": APP_VERSION,
            "monitor_interval_ms": self.monitor_interval_ms,
            "auto_refresh_enabled": bool(
                self.auto_refresh_checkbox.isChecked()
            ),
            "reachability_checks_enabled": bool(
                self.reachability_checkbox.isChecked()
            ),
            "selected_router_id": self.get_selected_router_id(),
            "developer_mode_enabled": bool(
                self.developer_mode_checkbox.isChecked()
            ),
            "last_diagnostic_directory": str(
                self.settings.value(
                    "developer/last_directory",
                    "",
                )
                or ""
            ),
        }

    def create_diagnostic_archive(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            QMessageBox.warning(
                self,
                "Роутер не выбран",
                "Выберите роутер.",
            )
            return

        initial_directory = str(
            self.settings.value(
                "developer/last_directory",
                str(DIAGNOSTICS_EXPORT_DIR),
            )
        )
        if not Path(initial_directory).is_dir():
            try:
                DIAGNOSTICS_EXPORT_DIR.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                initial_directory = str(DIAGNOSTICS_EXPORT_DIR)
            except OSError:
                initial_directory = str(Path.home())

        directory = QFileDialog.getExistingDirectory(
            self,
            "Папка для диагностического архива",
            initial_directory,
        )

        if not directory:
            return

        self.settings.setValue(
            "developer/last_directory",
            directory,
        )

        self.create_diagnostic_button.setEnabled(False)
        self.clear_diagnostic_button.setEnabled(False)
        self.diagnostic_status_label.setText(
            "Создание диагностического архива..."
        )

        monitor = self.router_monitors.get(router_id)
        application_settings = (
            self.diagnostic_application_settings()
        )

        if (
            monitor is not None
            and self.router_is_fully_connected(router_id)
        ):
            monitor.request_diagnostic_archive(
                directory,
                application_settings=application_settings,
            )
            return

        try:
            archive_path = DiagnosticsService(
                database=self.database
            ).create_offline_archive(
                destination=directory,
                router_id=router_id,
                application_settings=application_settings,
                last_result=self.last_monitor_results.get(router_id),
            )
        except Exception as error:
            self.diagnostic_action_completed(
                router_id,
                "create",
                False,
                str(error),
            )
            return

        self.diagnostic_action_completed(
            router_id,
            "create",
            True,
            str(archive_path),
        )

    def clear_diagnostic_data(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        answer = ask_yes_no(
            self,
            "Очистить диагностику",
            (
                "Удалить накопленные RCI-снимки, журнал "
                "диагностических событий и локальную историю "
                "попыток подключения?"
            ),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.create_diagnostic_button.setEnabled(False)
        self.clear_diagnostic_button.setEnabled(False)
        self.diagnostic_status_label.setText(
            "Очистка диагностических данных..."
        )

        monitor = self.router_monitors.get(router_id)
        if (
            monitor is not None
            and self.router_is_fully_connected(router_id)
        ):
            monitor.request_clear_diagnostics()
        else:
            DiagnosticsService(
                database=self.database
            ).clear_local_data(router_id)
            self.diagnostic_action_completed(
                router_id,
                "clear",
                True,
                "Локальная история диагностики очищена.",
            )

    def diagnostic_action_completed(
        self,
        router_id: int,
        action: str,
        success: bool,
        message: str,
    ) -> None:
        if router_id != self.get_selected_router_id():
            return

        self.update_diagnostic_controls()

        if action == "create":
            if success:
                self.diagnostic_status_label.setText(
                    f"Архив создан: {message}"
                )
                QMessageBox.information(
                    self,
                    "Диагностический архив создан",
                    message,
                )
            else:
                self.diagnostic_status_label.setText(
                    f"Ошибка создания архива: {message}"
                )
                QMessageBox.warning(
                    self,
                    "Архив не создан",
                    message,
                )
            return

        if action == "clear":
            if success:
                self.diagnostic_status_label.setText(
                    "Диагностические данные очищены."
                )
                QMessageBox.information(
                    self,
                    "Диагностика",
                    message,
                )
            else:
                self.diagnostic_status_label.setText(
                    f"Ошибка очистки: {message}"
                )
                QMessageBox.warning(
                    self,
                    "Диагностика",
                    message,
                )

    def connect_signals(self) -> None:
        self.connect_button.clicked.connect(
            self.connect_selected_router
        )
        self.disconnect_button.clicked.connect(
            self.disconnect_selected_router
        )
        self.refresh_button.clicked.connect(
            self.refresh_all_routers
        )
        self.auto_refresh_checkbox.toggled.connect(
            self.toggle_auto_refresh
        )
        self.reachability_checkbox.toggled.connect(
            self.toggle_reachability_checks
        )
        self.refresh_interval_combo.currentTextChanged.connect(
            self.change_refresh_interval
        )
        self.add_button.clicked.connect(
            self.open_add_router_dialog
        )
        self.edit_button.clicked.connect(
            self.open_edit_router_dialog
        )
        self.delete_button.clicked.connect(
            self.delete_selected_router
        )
        self.router_list.currentItemChanged.connect(
            self.router_selected
        )
        self.router_list.router_order_changed.connect(
            self.save_router_order
        )
        self.search_input.textChanged.connect(
            self.filter_routers
        )

    def load_routers(self) -> None:
        selected_router_id = (
            self.get_selected_router_id()
        )

        try:
            self.routers = (
                self.database.get_routers()
            )

        except sqlite3.Error as error:
            QMessageBox.critical(
                self,
                "Ошибка базы данных",
                str(error),
            )
            self.routers = []

        self.persisted_router_status = {
            int(router["id"]): bool(
                router.get("status")
            )
            for router in self.routers
        }
        active_ids = set(
            self.persisted_router_status
        )
        self.last_seen_persisted_at = {
            router_id: timestamp
            for router_id, timestamp
            in self.last_seen_persisted_at.items()
            if router_id in active_ids
        }

        self.display_routers(
            self.routers,
            selected_router_id=selected_router_id,
        )

        if hasattr(
            self,
            "router_status_worker",
        ):
            self.router_status_worker.set_routers(
                self.routers
            )

        self.statusBar().showMessage(
            f"Загружено роутеров: "
            f"{len(self.routers)}"
        )

    def display_routers(
        self,
        routers: list[dict],
        selected_router_id: int | None = None,
    ) -> None:
        self.router_list.clear()
        selected_row = -1
        visible_ids = {
            int(router["id"])
            for router in routers
        }
        complete_ids = {
            int(router["id"])
            for router in self.routers
        }
        reordering_enabled = bool(
            len(routers) > 1
            and visible_ids == complete_ids
            and not self.search_input.text().strip()
        )
        self.router_list.set_reordering_enabled(
            reordering_enabled
        )

        for row, router in enumerate(routers):
            router_id = int(router["id"])
            reachability = (
                self.router_reachability.get(
                    router_id
                )
            )
            monitor_result = self.connection_results.get(
                router_id
            )

            if (
                reachability is None
                and monitor_result is not None
                and bool(
                    getattr(
                        monitor_result,
                        "success",
                        False,
                    )
                )
            ):
                # Активная RCI-сессия сама подтверждает
                # доступность подключённого роутера.
                reachability = True

            status_state = self.router_status_state(
                reachability,
                checks_enabled=(
                    self.reachability_checkbox.isChecked()
                ),
            )

            item = QListWidgetItem(
                str(router["name"])
            )
            item.setData(
                self.ROUTER_ID_ROLE,
                router["id"],
            )
            item.setData(
                self.ROUTER_STATUS_ROLE,
                status_state,
            )
            # Перестановка реализована вручную в RouterListWidget.
            # Нативные флаги Qt DnD намеренно отключены, чтобы Qt
            # не удалял исходный элемент второй раз после переноса.
            item_flags = item.flags()
            item_flags &= ~Qt.ItemFlag.ItemIsDragEnabled
            item_flags &= ~Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(item_flags)

            tooltip_parts = []

            if router.get("local_ip"):
                tooltip_parts.append(
                    f"Локальный IP: "
                    f"{router['local_ip']}"
                )

            if router.get("keendns"):
                tooltip_parts.append(
                    f"KeenDNS: {router['keendns']}"
                )

            if router.get("group_name"):
                tooltip_parts.append(
                    f"Группа: "
                    f"{router['group_name']}"
                )

            checks_enabled = bool(
                self.reachability_checkbox.isChecked()
            )

            if not checks_enabled:
                tooltip_parts.insert(
                    0,
                    "Проверка доступности приостановлена",
                )
            elif reachability is True:
                tooltip_parts.insert(
                    0,
                    "Статус: доступен",
                )
            elif reachability is False:
                tooltip_parts.insert(
                    0,
                    "Статус: недоступен",
                )
            else:
                tooltip_parts.insert(
                    0,
                    "Статус: проверка...",
                )

            checked_address = (
                self.router_reachable_address.get(
                    router_id
                )
            )

            if checked_address:
                tooltip_parts.append(
                    f"Доступен через: "
                    f"{checked_address}"
                )

            tooltip_parts.append(
                (
                    "Порядок: перетащите за шеститочечную "
                    "ручку слева."
                    if reordering_enabled
                    else "Изменение порядка доступно без поиска."
                )
            )

            item.setToolTip(
                "\n".join(tooltip_parts)
            )

            self.router_list.addItem(item)

            if router["id"] == selected_router_id:
                selected_row = row

        if selected_row >= 0:
            self.router_list.setCurrentRow(
                selected_row
            )

        elif self.router_list.count() > 0:
            self.router_list.setCurrentRow(0)

        else:
            self.clear_router_information()

    def save_router_order(
        self,
        router_ids: list[int],
    ) -> None:
        """Сохраняет порядок после внутреннего drag-and-drop."""
        if self.search_input.text().strip():
            self.statusBar().showMessage(
                "Изменение порядка недоступно во время поиска",
                4000,
            )
            self.load_routers()
            return

        normalized = [int(value) for value in router_ids]
        expected_ids = {
            int(router["id"])
            for router in self.routers
        }

        if set(normalized) != expected_ids:
            self.statusBar().showMessage(
                "Не удалось сохранить неполный порядок роутеров",
                5000,
            )
            self.load_routers()
            return

        try:
            self.database.set_router_order(normalized)
        except (sqlite3.Error, ValueError) as error:
            QMessageBox.critical(
                self,
                "Ошибка сохранения порядка",
                str(error),
            )
            self.load_routers()
            return

        routers_by_id = {
            int(router["id"]): router
            for router in self.routers
        }
        reordered: list[dict] = []

        for sort_order, router_id in enumerate(normalized):
            router = routers_by_id[router_id]
            router["sort_order"] = sort_order
            reordered.append(router)

        self.routers = reordered

        if hasattr(self, "router_status_worker"):
            self.router_status_worker.set_routers(
                self.routers
            )

        self.statusBar().showMessage(
            "Порядок роутеров сохранён",
            3000,
        )

    def get_selected_router_id(
        self,
    ) -> int | None:
        current_item = (
            self.router_list.currentItem()
        )

        if current_item is None:
            return None

        return current_item.data(
            self.ROUTER_ID_ROLE
        )

    def set_router_monitor_ui_state(
        self,
        state: str,
    ) -> None:
        self.router_action_panel.set_state(state)
        self._update_router_management_actions()
        self._update_global_router_actions()

    def _router_is_busy(
        self,
        router_id: int | None,
    ) -> bool:
        if router_id is None:
            return False

        normalized = int(router_id)
        return (
            normalized in self.router_monitors
            or normalized in self.stopping_router_monitors
        )

    def router_monitor_ui_state(
        self,
        router_id: int,
    ) -> str:
        normalized = int(router_id)

        if normalized in self.stopping_router_monitors:
            return "stopping"

        if normalized not in self.router_monitors:
            return "disconnected"

        result = self.last_monitor_results.get(normalized)

        if result is None:
            return "connecting"

        if bool(getattr(result, "success", False)):
            return "connected"

        if (
            str(
                getattr(result, "connection_state", "")
                or ""
            ).strip().lower()
            == "authentication_required"
        ):
            return "authentication_required"

        if bool(
            getattr(
                result,
                "transient_failure_suppressed",
                False,
            )
        ):
            connection_state = str(
                getattr(result, "connection_state", "")
                or ""
            ).strip().lower()
            return (
                "retrying"
                if connection_state == "retrying"
                else "unstable"
            )

        return "reconnecting"

    def _update_router_management_actions(self) -> None:
        router_id = self.get_selected_router_id()
        has_selection = router_id is not None
        busy = self._router_is_busy(router_id)
        enabled = has_selection and not busy

        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

        if busy:
            reason = (
                "Сначала отключите роутер и дождитесь "
                "остановки мониторинга"
            )
            self.edit_button.setToolTip(reason)
            self.delete_button.setToolTip(reason)
        elif has_selection:
            self.edit_button.setToolTip(
                "Изменить параметры выбранного роутера"
            )
            self.delete_button.setToolTip(
                "Удалить выбранный роутер"
            )
        else:
            self.edit_button.setToolTip(
                "Сначала выберите роутер"
            )
            self.delete_button.setToolTip(
                "Сначала выберите роутер"
            )

    def _update_global_router_actions(self) -> None:
        active_count = len(self.router_monitors)
        self.refresh_button.setEnabled(active_count > 0)
        self.refresh_button.setToolTip(
            (
                "Обновить все подключённые роутеры "
                f"({active_count})"
                if active_count
                else "Нет подключённых роутеров для обновления"
            )
        )

    def open_add_router_dialog(self) -> None:
        dialog = AddRouterDialog(self)

        if (
            dialog.exec()
            != AddRouterDialog.DialogCode.Accepted
        ):
            return

        data = dialog.get_data()

        try:
            router_id = (
                self.database.add_router(**data)
            )

        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Не удалось сохранить",
                (
                    "Роутер с таким KeenDNS-адресом "
                    "уже существует."
                ),
            )
            return

        except (
            sqlite3.Error,
            ValueError,
            CredentialStoreError,
        ) as error:
            QMessageBox.critical(
                self,
                "Ошибка сохранения",
                str(error),
            )
            return

        self.load_routers()
        self.select_router_by_id(router_id)

        self.statusBar().showMessage(
            f"Роутер «{data['name']}» добавлен",
            5000,
        )

    def open_edit_router_dialog(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        if self._router_is_busy(router_id):
            QMessageBox.warning(
                self,
                "Подключение выполняется",
                (
                    "Нельзя изменять роутер, пока работает "
                    "или завершается мониторинг."
                ),
            )
            return

        router = self.database.get_router(
            router_id
        )

        if router is None:
            QMessageBox.warning(
                self,
                "Роутер не найден",
                "Запись отсутствует в базе данных.",
            )
            return

        dialog = AddRouterDialog(
            self,
            router_data=router,
        )

        if (
            dialog.exec()
            != AddRouterDialog.DialogCode.Accepted
        ):
            return

        data = dialog.get_data()

        try:
            self.database.update_router(
                router_id=router_id,
                **data,
            )

        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Не удалось сохранить",
                (
                    "Другой роутер уже использует "
                    "такой KeenDNS-адрес."
                ),
            )
            return

        except (
            sqlite3.Error,
            ValueError,
            CredentialStoreError,
        ) as error:
            QMessageBox.critical(
                self,
                "Ошибка сохранения",
                str(error),
            )
            return

        self.connection_results.pop(
            router_id,
            None,
        )
        self.last_monitor_results.pop(router_id, None)

        self.load_routers()
        self.select_router_by_id(router_id)

        self.statusBar().showMessage(
            f"Роутер «{data['name']}» изменён",
            5000,
        )

    def delete_selected_router(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        if self._router_is_busy(router_id):
            QMessageBox.warning(
                self,
                "Подключение выполняется",
                (
                    "Сначала отключите роутер и дождитесь "
                    "остановки мониторинга."
                ),
            )
            return

        router = self.database.get_router(
            router_id
        )

        if router is None:
            QMessageBox.warning(
                self,
                "Роутер не найден",
                "Запись отсутствует в базе данных.",
            )
            return

        answer = ask_yes_no(
            self,
            "Удалить роутер",
            (
                f"Удалить роутер "
                f"«{router['name']}»?\n\n"
                "Это действие нельзя отменить."
            ),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self.database.delete_router(
                router_id
            )

        except (
            sqlite3.Error,
            CredentialStoreError,
        ) as error:
            QMessageBox.critical(
                self,
                "Ошибка удаления",
                str(error),
            )
            return

        self.connection_results.pop(
            router_id,
            None,
        )
        self.last_monitor_results.pop(router_id, None)

        self.load_routers()

        self.statusBar().showMessage(
            f"Роутер «{router['name']}» удалён",
            5000,
        )

    def router_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous

        if current is None:
            self.clear_router_information()
            self.sync_developer_mode_for_selected_router()
            return

        router_id = current.data(
            self.ROUTER_ID_ROLE
        )

        router = self.database.get_router(
            router_id
        )

        if router is None:
            self.clear_router_information()
            return

        result = self.connection_results.get(
            router_id
        )
        has_connected_snapshot = bool(
            result is not None
            and getattr(result, "success", False)
        )

        self.show_router_information(
            router,
            preserve_live_content=has_connected_snapshot,
        )

        if hasattr(self, "internet_tab"):
            self.internet_tab.set_router(
                router_id
            )

        if hasattr(self, "wifi_tab"):
            self.wifi_tab.set_router(
                router_id
            )

        if hasattr(self, "journal_tab"):
            self.journal_tab.set_router(router_id)

        self.sync_developer_mode_for_selected_router()

        if has_connected_snapshot:
            self.show_connection_result(result)

        self.set_router_monitor_ui_state(
            self.router_monitor_ui_state(router_id)
        )

    def show_router_information(
        self,
        router: dict,
        *,
        preserve_live_content: bool = False,
    ) -> None:
        router_id = int(router.get("id") or 0)
        if hasattr(self, "journal_tab"):
            self.journal_tab.set_router(
                router_id if router_id > 0 else None
            )
        monitor_result = self.connection_results.get(
            router_id
        )
        known_online = bool(
            (
                monitor_result is not None
                and getattr(
                    monitor_result,
                    "success",
                    False,
                )
            )
            or self.router_reachability.get(router_id)
            is True
        )
        status_text = (
            "● Online"
            if known_online
            else "● Не подключён"
        )

        router_title = str(
            router.get("name") or "Без названия"
        )
        self.router_title.setText(router_title)
        self.router_title.setToolTip(router_title)
        self.status_label.setText(status_text)
        self.router_address_links.set_addresses(
            local_ip=router.get("local_ip"),
            keendns=router.get("keendns"),
        )
        self.connected_address_label.setText(
            "Фактический адрес: —\n"
            "Подключение: —"
        )

        # При переходе между двумя подключёнными роутерами
        # сохранённый снимок следующего роутера будет применён ниже
        # в том же событии Qt. Не подменяем рабочие вкладки
        # офлайн-страницами между этими двумя обновлениями: это
        # устраняет видимое мерцание и позволяет обновить карточки
        # на месте. Для офлайн-роутера полная очистка сохраняется.
        if not preserve_live_content:
            self.clear_router_state()

    def clear_router_state(self) -> None:
        """
        Полностью очищает динамические данные выбранного роутера.

        Метод вызывается при запуске, переключении роутера,
        ручном отключении и потере соединения.
        """
        self.set_router_content_connected(False)
        self.clear_live_information()
        self.sync_developer_mode_for_selected_router()

    def clear_live_information(self) -> None:
        # Основная информация.
        self.model_label.clear()
        self.version_label.clear()
        self.uptime_label.clear()
        self.clear_port_cards()
        self.port_overview.clear()
        self.rescue_mode_switch.clear()

        # CPU и RAM.
        self.cpu_metric.clear_metric()
        self.ram_metric.clear_metric()

        # Подключения: без надписи-заглушки.
        self.clear_internet_connection_cards()

        if hasattr(self, "internet_tab"):
            self.internet_tab.clear()

        # Wi-Fi и клиенты: без надписи-заглушки.
        self.clear_wifi_network_cards()

        if hasattr(self, "wifi_tab"):
            self.wifi_tab.clear()

        if hasattr(self, "clients_tab"):
            self.clients_tab.clear(reset_controls=False)

        if hasattr(self, "journal_tab"):
            self.journal_tab.clear(reset_controls=False)

        # Удаляем возможную временную плашку состояния.
        wrapper = getattr(
            self,
            "wifi_network_empty_wrapper",
            None,
        )

        if wrapper is not None:
            self.wifi_networks_layout.removeWidget(
                wrapper
            )
            wrapper.deleteLater()
            self.wifi_network_empty_wrapper = None
            self.wifi_network_empty_label = None

        # Удаляем обычную пустую подпись, если она была создана.
        empty_label = getattr(
            self,
            "wifi_network_empty_label",
            None,
        )

        if (
            empty_label is not None
            and empty_label.parent() is not None
        ):
            self.wifi_networks_layout.removeWidget(
                empty_label
            )
            empty_label.deleteLater()
            self.wifi_network_empty_label = None

    def clear_router_information(self) -> None:
        self.router_title.setText(
            "Информация о роутере"
        )
        self.router_title.setToolTip("")
        self.status_label.clear()
        self.router_address_links.clear_addresses()
        self.connected_address_label.setText(
            "Фактический адрес: —\n"
            "Подключение: —"
        )

        self.clear_router_state()
        self.set_router_monitor_ui_state("none")

    def filter_routers(
        self,
        search_text: str,
    ) -> None:
        search_text = search_text.strip().lower()
        selected_router_id = (
            self.get_selected_router_id()
        )

        if not search_text:
            self.display_routers(
                self.routers,
                selected_router_id=selected_router_id,
            )
            return

        filtered_routers = []

        for router in self.routers:
            searchable_text = " ".join(
                [
                    router.get("name") or "",
                    router.get("local_ip") or "",
                    router.get("keendns") or "",
                    router.get("group_name") or "",
                ]
            ).lower()

            if search_text in searchable_text:
                filtered_routers.append(router)

        self.display_routers(
            filtered_routers,
            selected_router_id=selected_router_id,
        )

    def toggle_auto_refresh(
        self,
        enabled: bool,
    ) -> None:
        enabled = bool(enabled)
        self.auto_refresh_enabled = enabled
        self.settings.setValue(
            "monitor/auto_refresh_enabled",
            enabled,
        )
        self.settings.sync()

        for monitor in self.router_monitors.values():
            monitor.set_paused(not enabled)

        self.statusBar().showMessage(
            (
                "Автообновление данных включено"
                if enabled
                else "Автообновление данных приостановлено"
            ),
            3000,
        )

    def toggle_reachability_checks(
        self,
        enabled: bool,
    ) -> None:
        enabled = bool(enabled)
        self.reachability_checks_enabled = enabled
        self.settings.setValue(
            "monitor/reachability_enabled",
            enabled,
        )
        self.settings.sync()

        self.router_status_worker.set_paused(
            not enabled
        )

        # Старое состояние не выдаётся за актуальное после
        # смены режима. При включении показываем «проверяется»,
        # при выключении — нейтральный неизвестный статус.
        self.router_reachability = {
            int(router["id"]): None
            for router in self.routers
        }
        self.router_reachable_address.clear()
        self.filter_routers(
            self.search_input.text()
        )

        self.statusBar().showMessage(
            (
                "Проверка доступности включена"
                if enabled
                else "Проверка доступности приостановлена"
            ),
            3000,
        )

    def change_refresh_interval(
        self,
        interval_name: str,
    ) -> None:
        interval_ms = self.monitor_intervals.get(
            interval_name
        )

        if interval_ms is None:
            return

        self.monitor_interval_ms = interval_ms
        self.settings.setValue(
            "monitor/interval_ms",
            interval_ms,
        )
        self.settings.sync()

        for monitor in self.router_monitors.values():
            monitor.set_interval(interval_ms)

        self.statusBar().showMessage(
            f"Интервал обновления: "
            f"{interval_name}",
            3000,
        )

    def refresh_all_routers(
        self,
        checked: bool = False,
        *,
        announce: bool = True,
    ) -> None:
        del checked

        if not self.router_monitors:
            if announce:
                self.statusBar().showMessage(
                    "Нет подключённых роутеров",
                    3000,
                )
            return

        for monitor in self.router_monitors.values():
            monitor.request_refresh()

        if announce:
            self.statusBar().showMessage(
                "Запрошено обновление подключённых роутеров",
                3000,
            )

    def refresh_selected_router(self) -> None:
        router_id = self.get_selected_router_id()
        monitor = (
            self.router_monitors.get(router_id)
            if router_id is not None
            else None
        )
        if monitor is None:
            self.statusBar().showMessage(
                "Выбранный роутер не подключён",
                3000,
            )
            return

        monitor.request_refresh()
        self.statusBar().showMessage(
            "Запрошено обновление клиентов",
            3000,
        )

    def connect_selected_router(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        if router_id in self.router_monitors:
            QMessageBox.information(
                self,
                "Подключение",
                "Этот роутер уже подключён.",
            )
            return

        if router_id in self.stopping_router_monitors:
            QMessageBox.information(
                self,
                "Отключение завершается",
                (
                    "Дождитесь полной остановки мониторинга, "
                    "затем подключите роутер снова."
                ),
            )
            return

        try:
            router = self.database.get_router(
                router_id,
                include_password=True,
            )
        except CredentialStoreError as error:
            QMessageBox.critical(
                self,
                "Пароль недоступен",
                str(error),
            )
            return

        if router is None:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Роутер не найден в базе данных.",
            )
            return

        self.pending_rescue_modes.pop(router_id, None)
        self.clear_router_state()

        self.status_label.setText(
            "● Подключение..."
        )
        self.connected_address_label.setText(
            "Фактический адрес: —\n"
            "Подключение: —"
        )

        monitor = RouterMonitor(
            router=router,
            interval_ms=self.monitor_interval_ms,
            paused=(
                not self.auto_refresh_checkbox.isChecked()
            ),
            parent=self,
        )

        monitor.updated.connect(
            self.connection_completed
        )
        monitor.interface_action_completed.connect(
            self.interface_action_completed
        )
        monitor.interface_reconnect_completed.connect(
            self.interface_reconnect_completed
        )
        monitor.interface_settings_completed.connect(
            self.interface_settings_completed
        )
        monitor.connection_creation_completed.connect(
            self.connection_creation_completed
        )
        monitor.wireless_scan_completed.connect(
            self.wireless_scan_completed
        )
        monitor.connection_delete_completed.connect(
            self.connection_delete_completed
        )
        monitor.connection_protocol_change_completed.connect(
            self.connection_protocol_change_completed
        )
        monitor.wifi_network_action_completed.connect(
            self.wifi_network_action_completed
        )
        monitor.wifi_client_access_completed.connect(
            self.wifi_client_access_completed
        )
        monitor.rescue_mode_action_completed.connect(
            self.rescue_mode_action_completed
        )
        monitor.priorities_action_completed.connect(
            self.internet_priorities_action_completed
        )
        monitor.diagnostic_action_completed.connect(
            self.diagnostic_action_completed
        )
        monitor.system_log_completed.connect(
            self.system_log_completed
        )
        monitor.monitor_stopped.connect(
            self.monitor_stopped
        )
        monitor.finished.connect(
            lambda rid=router_id, thread=monitor:
            self.router_monitor_finished(
                rid,
                thread,
            )
        )

        self.router_monitors[
            router_id
        ] = monitor

        self.set_router_monitor_ui_state("connecting")
        # Начальный снимок выполняется всегда. Если автообновление
        # выключено, после него монитор вернётся в состояние паузы.
        monitor.request_refresh()
        monitor.start()

        self.statusBar().showMessage(
            f"Подключение к «{router['name']}»..."
        )

    def connection_completed(
        self,
        router_id: int,
        result,
    ) -> None:
        if router_id not in self.router_monitors:
            return

        self.last_monitor_results[router_id] = result
        current_router_id = self.get_selected_router_id()
        confirmed_offline = bool(
            getattr(
                result,
                "confirmed_offline",
                not bool(result.success),
            )
        )
        transient_failure = bool(
            not result.success
            and getattr(
                result,
                "transient_failure_suppressed",
                False,
            )
        )
        connection_state = str(
            getattr(result, "connection_state", "") or ""
        ).strip().lower()
        independently_reachable = (
            self.router_reachability.get(router_id) is True
        )

        try:
            if result.success:
                self.persist_router_monitor_status(
                    router_id=router_id,
                    is_online=True,
                )
            elif confirmed_offline and not independently_reachable:
                self.persist_router_monitor_status(
                    router_id=router_id,
                    is_online=False,
                )
        except sqlite3.Error as error:
            QMessageBox.warning(
                self,
                "Ошибка базы данных",
                str(error),
            )

        if result.success:
            self.update_router_item_status(
                router_id=router_id,
                is_online=True,
            )
            self.connection_results[router_id] = result
            self.update_active_cable_dialog(router_id, result)
            self.update_active_wireless_isp_dialog(router_id, result)
        elif confirmed_offline:
            self.update_router_item_status(
                router_id=router_id,
                is_online=independently_reachable,
            )
            self.connection_results.pop(router_id, None)
            self.clear_router_traffic_history(router_id)
        # При transient-сбое карточки, адрес и зелёный статус списка
        # сохраняются до подтверждения третьей последовательной ошибки.

        if current_router_id != router_id:
            return

        if transient_failure:
            retrying = connection_state == "retrying"
            self.set_router_monitor_ui_state(
                "retrying"
                if retrying
                else "unstable"
            )
            self.status_label.setText(
                "● Повторная попытка"
                if retrying
                else "● Связь нестабильна"
            )
            self.statusBar().showMessage(result.message, 5000)
            self.sync_developer_mode_for_selected_router()
            return

        if connection_state == "authentication_required":
            self.set_router_monitor_ui_state(
                "authentication_required"
            )
            self.status_label.setText(
                "● Требуется авторизация"
            )
            self.connected_address_label.setText(
                "Фактический адрес: роутер доступен\n"
                "Подключение: проверьте логин и пароль"
            )
            self.clear_router_state()
            self.statusBar().showMessage(
                result.message
                + " Повторные попытки приостановлены.",
                8000,
            )
            self.sync_developer_mode_for_selected_router()
            return

        if not result.success:
            self.set_router_monitor_ui_state("reconnecting")
            self.status_label.setText("● Offline")
            self.connected_address_label.setText(
                "Фактический адрес: —\n"
                "Подключение: —"
            )
            self.clear_router_state()
            self.statusBar().showMessage(result.message, 5000)
            self.sync_developer_mode_for_selected_router()
            return

        self.set_router_monitor_ui_state("connected")
        self.show_connection_result(result)
        self.sync_developer_mode_for_selected_router()

    def persist_router_monitor_status(
        self,
        *,
        router_id: int,
        is_online: bool,
    ) -> None:
        """Сохраняет статус без постоянной записи SQLite."""
        router_id = int(router_id)
        is_online = bool(is_online)
        previous = self.persisted_router_status.get(
            router_id
        )
        now = monotonic()
        last_seen_write = (
            self.last_seen_persisted_at.get(
                router_id,
                0.0,
            )
        )
        status_changed = (
            previous is None
            or previous != is_online
        )
        touch_last_seen = (
            is_online
            and (
                status_changed
                or now - last_seen_write
                >= self.last_seen_write_interval_seconds
            )
        )

        if not status_changed and not touch_last_seen:
            return

        self.database.update_router_status(
            router_id=router_id,
            status=is_online,
            touch_last_seen=touch_last_seen,
        )
        self.persisted_router_status[
            router_id
        ] = is_online

        if touch_last_seen:
            self.last_seen_persisted_at[
                router_id
            ] = now

        for router in self.routers:
            if int(router.get("id", -1)) == router_id:
                router["status"] = int(is_online)
                break

    @staticmethod
    def percentage_value(
        value: str | None,
    ) -> int:
        if not value:
            return 0

        try:
            number = float(
                str(value).replace("%", "").strip()
            )
        except ValueError:
            return 0

        return max(
            0,
            min(100, round(number)),
        )

    def clear_internet_connection_cards(
        self,
    ) -> None:
        for card in self.internet_connection_cards.values():
            self.internet_connections_layout.removeWidget(
                card
            )
            card.deleteLater()

        self.internet_connection_cards = {}

        if self.internet_connection_empty_label is not None:
            self.internet_connections_layout.removeWidget(
                self.internet_connection_empty_label
            )
            self.internet_connection_empty_label.deleteLater()
            self.internet_connection_empty_label = None

        self.internet_connections_container.updateGeometry()

    def show_empty_internet_state(
        self,
        message: str,
    ) -> None:
        if self.internet_connection_empty_label is None:
            self.internet_connection_empty_label = QLabel()
            self.internet_connection_empty_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            self.internet_connection_empty_label.setObjectName(
                "emptyInternetState"
            )
            self.internet_connections_layout.addWidget(
                self.internet_connection_empty_label
            )

        if (
            self.internet_connection_empty_label.text()
            != message
        ):
            self.internet_connection_empty_label.setText(
                message
            )
        self.internet_connection_empty_label.show()

    def connection_history(
        self,
        router_id: int,
        interface_id: str,
    ) -> TrafficHistory:
        key = (
            router_id,
            interface_id,
        )

        if key not in self.traffic_history:
            self.traffic_history[key] = {
                "rx": deque(maxlen=90),
                "tx": deque(maxlen=90),
                "time": deque(maxlen=90),
                "last_sample_time": None,
            }

        return self.traffic_history[key]

    def open_wifi_network_creation(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(router_id)

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        capabilities = dict(
            getattr(
                self.wifi_tab,
                "capabilities",
                {},
            )
        )
        if (
            not bool(capabilities.get("mws_supported", True))
            and not bool(capabilities.get("legacy_supported", False))
        ):
            QMessageBox.warning(
                self,
                "Создание сети недоступно",
                (
                    "Роутер не предоставляет поддерживаемый способ "
                    "создания Wi-Fi сетей."
                ),
            )
            return
        dialog = WiFiNetworkDialog(
            capabilities=capabilities,
            parent=self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        settings = dialog.settings()
        answer = ask_yes_no(
            self,
            "Создать Wi-Fi сеть",
            (
                f"Создать сеть «{settings['ssid']}»?\n\n"
                "Диапазоны: "
                + (
                    ", ".join(settings.get("bands") or [])
                    or "не назначены"
                )
            ),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        monitor.request_create_wifi_network(settings)
        self.statusBar().showMessage(
            f"Создание Wi-Fi сети «{settings['ssid']}»..."
        )

    def open_wifi_network_edit(
        self,
        network: object,
    ) -> None:
        if not isinstance(network, dict):
            return

        router_id = self.get_selected_router_id()
        monitor = (
            self.router_monitors.get(router_id)
            if router_id is not None
            else None
        )

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        interfaces = self._wifi_network_interfaces(network)
        profile_id = str(
            network.get("mws_profile_id") or ""
        ).strip()

        if not interfaces and not profile_id:
            QMessageBox.warning(
                self,
                "Интерфейс не определён",
                "Роутер не передал интерфейсы этой сети.",
            )
            return

        dialog = WiFiNetworkDialog(
            network=network,
            capabilities=dict(
                getattr(
                    self.wifi_tab,
                    "capabilities",
                    {},
                )
            ),
            parent=self,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        settings = dialog.settings()
        old_ssid = str(
            network.get("ssid")
            or network.get("name")
            or "Wi-Fi сеть"
        )
        selected_bands = ", ".join(
            settings.get("bands") or []
        ) or "не назначены"
        answer = ask_yes_no(
            self,
            "Сохранить Wi-Fi сеть",
            (
                f"Сохранить изменения сети «{old_ssid}»?\n\n"
                f"Новое имя: {settings['ssid']}\n"
                f"Диапазоны: {selected_bands}"
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        monitor.request_update_wifi_network(
            interfaces,
            settings,
        )
        self.statusBar().showMessage(
            f"Изменение Wi-Fi сети «{settings['ssid']}»..."
        )

    def delete_wifi_network(
        self,
        network: object,
    ) -> None:
        if not isinstance(network, dict):
            return

        if not bool(network.get("deletable", False)):
            QMessageBox.information(
                self,
                "Удаление недоступно",
                (
                    "Основную и гостевую системные сети "
                    "можно выключить или изменить, но нельзя удалить."
                ),
            )
            return

        router_id = self.get_selected_router_id()
        monitor = (
            self.router_monitors.get(router_id)
            if router_id is not None
            else None
        )

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        interfaces = self._wifi_network_interfaces(network)
        profile_id = str(
            network.get("mws_profile_id") or ""
        ).strip()
        if not interfaces and not profile_id:
            QMessageBox.warning(
                self,
                "Удаление недоступно",
                "Не удалось определить профиль или интерфейсы Wi-Fi сети.",
            )
            return

        ssid = str(
            network.get("ssid")
            or network.get("name")
            or "Wi-Fi сеть"
        )
        clients = int(
            network.get("client_count")
            or len(network.get("clients") or [])
        )
        target_text = (
            f"Интерфейсы: {', '.join(interfaces)}"
            if interfaces
            else f"Профиль MWS: {profile_id} (диапазоны не назначены)"
        )

        answer = ask_yes_no(
            self,
            "Удалить Wi-Fi сеть",
            (
                f"Удалить сеть «{ssid}»?\n\n"
                f"{target_text}\n"
                f"Подключённых клиентов: {clients}\n\n"
                "Устройства потеряют соединение. "
                "Отменить операцию после подтверждения нельзя."
            ),
            icon=QMessageBox.Icon.Warning,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        monitor.request_delete_wifi_network(dict(network))
        self.statusBar().showMessage(
            f"Удаление Wi-Fi сети «{ssid}»..."
        )

    @staticmethod
    def _wifi_network_interfaces(
        network: dict,
    ) -> list[str]:
        raw = network.get("interfaces")

        if isinstance(raw, list):
            values = raw
        else:
            values = str(
                network.get("interface_id")
                or ""
            ).split(",")

        result: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if text and text not in result:
                result.append(text)

        return result

    def change_network_interfaces_state(
        self,
        network_or_interfaces: object,
        display_name: str,
        enabled: bool,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(router_id)

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        if isinstance(network_or_interfaces, dict):
            network = dict(network_or_interfaces)
            interfaces = self._wifi_network_interfaces(network)
            profile_id = str(
                network.get("mws_profile_id") or ""
            ).strip()
            legacy_managed = bool(network.get("legacy_managed", False))
            if not profile_id and not legacy_managed:
                QMessageBox.warning(
                    self,
                    "Профиль MWS не найден",
                    (
                        "Роутер не передал логический профиль этой "
                        "Wi-Fi сети. Управление остановлено, чтобы "
                        "ползунок Keenetic и точки доступа не разошлись."
                    ),
                )
                return

            if legacy_managed and not interfaces:
                QMessageBox.warning(
                    self,
                    "Диапазон не назначен",
                    "Сначала назначьте сети диапазон через кнопку «Изменить».",
                )
                return

            action = "включить" if enabled else "выключить"
            details = (
                (
                    f"Профиль Keenetic: {profile_id}"
                    if profile_id
                    else "Legacy-режим KeeneticOS 4"
                )
                + (
                    f"\nТочки доступа: {', '.join(interfaces)}"
                    if interfaces
                    else ""
                )
            )
            answer = ask_yes_no(
                self,
                "Включить сеть" if enabled else "Выключить сеть",
                (
                    f"{action.capitalize()} «{display_name}»?\n\n"
                    + details
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            monitor.request_set_wifi_network_enabled(
                network,
                enabled,
            )
            self.statusBar().showMessage(
                f"Команда MWS отправлена: {action} «{display_name}»"
            )
            return

        if isinstance(network_or_interfaces, str):
            interfaces = [network_or_interfaces]
        elif isinstance(network_or_interfaces, (list, tuple, set)):
            interfaces = [
                str(item).strip()
                for item in network_or_interfaces
                if str(item).strip()
            ]
        else:
            interfaces = []

        if not interfaces:
            QMessageBox.warning(
                self,
                "Интерфейс не определён",
                "Роутер не передал интерфейс для управления.",
            )
            return

        if router_id in self.pending_interface_batches:
            self.statusBar().showMessage(
                "Предыдущее переключение интерфейса ещё выполняется.",
                3000,
            )
            return

        action = "включить" if enabled else "выключить"
        answer = ask_yes_no(
            self,
            "Включить интерфейс" if enabled else "Выключить интерфейс",
            (
                f"{action.capitalize()} «{display_name}»?\n\n"
                f"Интерфейсы: {', '.join(interfaces)}"
                + (
                    "\n\nПриложение проверит административное состояние "
                    "и отменит изменение при неподтверждённом результате. "
                    "Порт, через который открыто управление роутером, "
                    "выключить нельзя."
                    if not enabled
                    else ""
                )
            ),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        unique_interfaces = list(dict.fromkeys(interfaces))
        self.pending_interface_batches[router_id] = {
            "remaining": set(unique_interfaces),
            "interfaces": list(unique_interfaces),
            "successes": [],
            "errors": [],
            "display_name": display_name,
            "enabled": enabled,
        }
        if hasattr(self, "clients_tab"):
            self.clients_tab.set_port_action_pending(
                unique_interfaces,
                True,
            )

        for interface_id in unique_interfaces:
            monitor.request_interface_state(
                interface_id=interface_id,
                enabled=enabled,
            )

        self.statusBar().showMessage(
            f"Команда отправлена: {action} «{display_name}»"
        )

    def change_wifi_client_access(
        self,
        client: object,
        allowed: bool,
    ) -> None:
        router_id = self.get_selected_router_id()
        reference = dict(client) if isinstance(client, dict) else {}
        if router_id is None:
            return

        monitor = self.router_monitors.get(router_id)
        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        if str(reference.get("connection_kind") or "").lower() != "wifi":
            QMessageBox.warning(
                self,
                "Не Wi-Fi клиент",
                "Это действие доступно только для Wi-Fi клиентов.",
            )
            return

        if bool(reference.get("management_client")) and not allowed:
            QMessageBox.warning(
                self,
                "Отключение заблокировано",
                (
                    "Этот компьютер используется для управления роутером. "
                    "Его отключение может прервать доступ к устройству."
                ),
            )
            return

        name = str(
            reference.get("name")
            or reference.get("hostname")
            or reference.get("mac")
            or "Wi-Fi клиент"
        ).strip()
        mac = str(reference.get("mac") or "—").strip()
        if allowed:
            title = "Разрешить Wi-Fi доступ"
            question = (
                f"Снова разрешить «{name}» подключаться к Wi-Fi?\n\n"
                f"MAC: {mac}"
            )
            status = f"Разрешение Wi-Fi доступа: {name}"
        else:
            title = "Отключить Wi-Fi клиента"
            question = (
                f"Отключить «{name}» от Wi-Fi?\n\n"
                f"MAC: {mac}\n\n"
                "Клиент потеряет соединение и не сможет подключиться "
                "повторно, пока вы не нажмёте «Разрешить Wi-Fi»."
            )
            status = f"Отключение Wi-Fi клиента: {name}"

        answer = ask_yes_no(self, title, question)
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.clients_tab.set_wifi_action_pending(True)
        monitor.request_wifi_client_access(
            reference,
            allowed=bool(allowed),
        )
        self.statusBar().showMessage(status)

    def change_connection_interface_state(
        self,
        interface_id: str,
        interface_name: str,
        enabled: bool,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(
            router_id
        )

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        action_text = (
            "включить"
            if enabled
            else "выключить"
        )
        title_text = (
            "Включить подключение"
            if enabled
            else "Выключить подключение"
        )

        warning_text = (
            f"Включить интерфейс "
            f"«{interface_name}»?"
            if enabled
            else (
                f"Выключить интерфейс "
                f"«{interface_name}»?\n\n"
                "Интернет или VPN через него "
                "перестанет работать."
            )
        )

        message_box = QMessageBox(self)
        message_box.setWindowTitle(title_text)
        message_box.setText(warning_text)
        message_box.setIcon(
            QMessageBox.Icon.Question
        )
        message_box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        message_box.setDefaultButton(
            QMessageBox.StandardButton.No
        )

        yes_button = message_box.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = message_box.button(
            QMessageBox.StandardButton.No
        )

        if yes_button is not None:
            yes_button.setText("Да")

        if no_button is not None:
            no_button.setText("Нет")

        answer = message_box.exec()

        if answer != QMessageBox.StandardButton.Yes:
            return

        monitor.request_interface_state(
            interface_id=interface_id,
            enabled=enabled,
        )

        self.statusBar().showMessage(
            f"Попытка {action_text} "
            f"«{interface_name}»..."
        )

    @staticmethod
    def cable_creation_options_from_result(
        result: object,
    ) -> dict:
        creation_options = getattr(
            result,
            "creation_options",
            None,
        )

        if not isinstance(creation_options, dict):
            return {}

        cable_options = creation_options.get(
            "ethernet.cable"
        )

        if not isinstance(cable_options, dict):
            cable_options = creation_options.get(
                "ethernet.ipoe",
                {},
            )

        return (
            dict(cable_options)
            if isinstance(cable_options, dict)
            else {}
        )

    def update_active_cable_dialog(
        self,
        router_id: int,
        result: object,
    ) -> None:
        dialog = self.active_cable_dialogs.get(
            int(router_id)
        )

        if dialog is None:
            return

        cable_options = self.cable_creation_options_from_result(
            result
        )
        ports = cable_options.get("ports")

        if not isinstance(ports, list):
            ports = []

        try:
            priority_count = int(
                cable_options.get(
                    "existing_priority_count",
                    len(
                        getattr(
                            result,
                            "internet_connections",
                            None,
                        )
                        or []
                    ),
                )
            )
        except (TypeError, ValueError):
            priority_count = 0

        dialog.update_available_ports(
            ports,
            existing_priority_count=priority_count,
        )

    @staticmethod
    def wireless_isp_creation_options_from_result(
        result: object,
    ) -> dict:
        creation_options = getattr(
            result,
            "creation_options",
            None,
        )

        if not isinstance(creation_options, dict):
            return {}

        options = creation_options.get(
            "wireless_isp",
            {},
        )
        return dict(options) if isinstance(options, dict) else {}

    def update_active_wireless_isp_dialog(
        self,
        router_id: int,
        result: object,
    ) -> None:
        dialog = self.active_wireless_isp_dialogs.get(
            int(router_id)
        )

        if dialog is None:
            return

        options = self.wireless_isp_creation_options_from_result(
            result
        )
        bands = options.get("bands")

        if not isinstance(bands, list):
            bands = []

        try:
            priority_count = int(
                options.get(
                    "existing_priority_count",
                    len(
                        getattr(
                            result,
                            "internet_connections",
                            None,
                        )
                        or []
                    ),
                )
            )
        except (TypeError, ValueError):
            priority_count = 0

        dialog.update_available_bands(
            bands,
            existing_priority_count=priority_count,
        )

    def wireless_scan_completed(
        self,
        router_id: int,
        band: str,
        success: bool,
        message: str,
        networks: object,
    ) -> None:
        dialog = self.active_wireless_isp_dialogs.get(
            int(router_id)
        )

        if dialog is None:
            return

        normalized_networks = (
            [dict(item) for item in networks if isinstance(item, dict)]
            if isinstance(networks, list)
            else []
        )
        dialog.update_scan_results(
            band=str(band),
            success=bool(success),
            message=str(message or ""),
            networks=normalized_networks,
        )

    def open_connection_creation(
        self,
        connection_type: str,
        connection_title: str,
    ) -> None:
        type_id = str(connection_type or "").strip()
        title = str(connection_title or "").strip()

        if not type_id or not title:
            QMessageBox.warning(
                self,
                "Создание подключения",
                "Не удалось определить тип подключения.",
            )
            return

        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(router_id)
        result = self.connection_results.get(router_id)

        if monitor is None or result is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        if type_id == "wireless.isp":
            wireless_options = (
                self.wireless_isp_creation_options_from_result(
                    result
                )
            )
            bands = wireless_options.get("bands")

            if not isinstance(bands, list):
                bands = []

            try:
                priority_count = int(
                    wireless_options.get(
                        "existing_priority_count",
                        len(
                            getattr(
                                result,
                                "internet_connections",
                                None,
                            )
                            or []
                        ),
                    )
                )
            except (TypeError, ValueError):
                priority_count = 0

            dialog = WirelessIspCreationDialog(
                bands=bands,
                existing_priority_count=priority_count,
                parent=self,
            )
            self.active_wireless_isp_dialogs[
                router_id
            ] = dialog
            dialog.scan_requested.connect(
                monitor.request_scan_wireless_isp
            )
            dialog.options_refresh_requested.connect(
                monitor.request_refresh
            )
            monitor.request_refresh()

            try:
                dialog_result = dialog.exec()
            finally:
                self.active_wireless_isp_dialogs.pop(
                    router_id,
                    None,
                )

            if (
                dialog_result
                != QDialog.DialogCode.Accepted
            ):
                return

            settings = dialog.creation_settings()

            if not isinstance(settings, dict):
                return

            band = str(
                settings.get("band") or ""
            ).strip()
            band_title = (
                "2,4 ГГц"
                if band == "2.4"
                else "5 ГГц"
            )
            ssid = str(
                settings.get("ssid") or ""
            ).strip()
            description = str(
                settings.get("description")
                or f"Wireless ISP {band_title}"
            ).strip()
            open_network = bool(
                settings.get("open_network")
            )
            ipv4 = dict(
                settings.get("ipv4") or {}
            )
            ipv4_text = (
                "DHCP"
                if ipv4.get("dhcp")
                else (
                    f"{ipv4.get('address')}/"
                    f"{ipv4.get('prefix')}, "
                    f"шлюз {ipv4.get('gateway')}"
                )
            )
            bssid = str(
                settings.get("bssid") or ""
            ).strip()

            information = [
                f"Диапазон: {band_title}",
                f"Wi-Fi-сеть: {ssid}",
                (
                    "Защита: открытая сеть"
                    if open_network
                    else (
                        "Защита: WPA/WPA2; пароль будет "
                        "сохранён только в конфигурации роутера"
                    )
                ),
                f"IPv4: {ipv4_text}",
                (
                    "Приоритет: "
                    f"{int(settings.get('priority_order', 0)) + 1}"
                ),
            ]

            if bssid:
                information.append(
                    f"Привязка к BSSID: {bssid}"
                )

            confirmation = QMessageBox(self)
            confirmation.setWindowTitle(
                "Создать Wireless ISP"
            )
            confirmation.setIcon(
                QMessageBox.Icon.Question
            )
            confirmation.setText(
                f"Создать подключение «{description}»?"
            )
            confirmation.setInformativeText(
                "\n".join(information)
                + (
                    "\n\nВо время подключения выбранный "
                    "Wi-Fi-модуль может кратковременно "
                    "приостановить передачу данных. Пароль "
                    "не будет показан в карточке, сообщениях "
                    "или диагностическом архиве."
                )
            )
            confirmation.setStandardButtons(
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            )
            confirmation.setDefaultButton(
                QMessageBox.StandardButton.No
            )

            yes_button = confirmation.button(
                QMessageBox.StandardButton.Yes
            )
            no_button = confirmation.button(
                QMessageBox.StandardButton.No
            )

            if yes_button is not None:
                yes_button.setText("Создать")

            if no_button is not None:
                no_button.setText("Назад")

            if (
                confirmation.exec()
                != QMessageBox.StandardButton.Yes
            ):
                return

            self.internet_tab.create_connection_button.setEnabled(
                False
            )
            monitor.request_create_wireless_isp(
                settings
            )
            self.statusBar().showMessage(
                f"Создание Wireless ISP {band_title}..."
            )
            return

        if type_id != "ethernet.cable":
            message_box = QMessageBox(self)
            message_box.setWindowTitle(
                "Создание подключения"
            )
            message_box.setIcon(
                QMessageBox.Icon.Information
            )
            message_box.setText(
                f"Выбрано подключение: {title}"
            )
            message_box.setInformativeText(
                "Мастер этого типа будет подключён "
                "на одном из следующих этапов.\n\n"
                f"Внутренний тип: {type_id}"
            )
            message_box.exec()
            return

        cable_options = self.cable_creation_options_from_result(
            result
        )
        ports = cable_options.get("ports")

        if not isinstance(ports, list):
            ports = []

        dialog = CableInternetCreationDialog(
            ports=ports,
            existing_priority_count=int(
                cable_options.get(
                    "existing_priority_count",
                    len(
                        getattr(
                            result,
                            "internet_connections",
                            None,
                        )
                        or []
                    ),
                )
            ),
            parent=self,
        )

        self.active_cable_dialogs[router_id] = dialog
        dialog.ports_refresh_requested.connect(
            monitor.request_refresh
        )
        monitor.request_refresh()

        try:
            dialog_result = dialog.exec()
        finally:
            self.active_cable_dialogs.pop(
                router_id,
                None,
            )

        if dialog_result != QDialog.DialogCode.Accepted:
            return

        settings = dialog.creation_settings()

        if not isinstance(settings, dict):
            return

        port = settings.get("port")
        port_label = (
            str(port.get("port_label") or "?")
            if isinstance(port, dict)
            else "?"
        )
        description = str(
            settings.get("description")
            or "Кабельный Интернет"
        )
        protocol_settings = settings.get("protocol")
        protocol = (
            str(protocol_settings.get("type") or "none")
            if isinstance(protocol_settings, dict)
            else "none"
        ).strip().lower()
        protocol_title = {
            "pppoe": "PPPoE",
            "pptp": "PPTP",
            "l2tp": "L2TP",
        }.get(protocol, "IPoE")
        ipv4 = dict(settings.get("ipv4") or {})
        ipv4_text = (
            "DHCP"
            if ipv4.get("dhcp")
            else (
                f"{ipv4.get('address')}/"
                f"{ipv4.get('prefix')}, "
                f"шлюз {ipv4.get('gateway')}"
            )
        )

        info_lines = [
            f"Протокол: {protocol_title}",
            f"IPv4 транспорта: {ipv4_text}",
            (
                "Приоритет: "
                f"{int(settings.get('priority_order', 0)) + 1}"
            ),
        ]

        if protocol == "pppoe":
            username = str(
                protocol_settings.get("username") or ""
            )
            service = str(
                protocol_settings.get("service") or ""
            )
            info_lines.append(
                f"Пользователь PPPoE: {username}"
            )
            info_lines.append(
                "Сервис PPPoE: " + (service or "любой")
            )
            info_lines.append(
                "Пароль PPPoE: сохранится в конфигурации "
                "роутера и не будет показан в диагностике."
            )
        elif protocol in {"pptp", "l2tp"}:
            username = str(
                protocol_settings.get("username") or ""
            )
            server = str(
                protocol_settings.get("server") or ""
            )
            protocol_upper = protocol.upper()
            info_lines.append(
                f"Сервер {protocol_upper}: {server}"
            )
            info_lines.append(
                f"Пользователь {protocol_upper}: {username}"
            )
            info_lines.append(
                f"Пароль {protocol_upper}: сохранится в "
                "конфигурации роутера и не будет показан "
                "в диагностике."
            )

        port_link = (
            str(port.get("link") or "unknown").strip().lower()
            if isinstance(port, dict)
            else "unknown"
        )

        if port_link == "up":
            info_lines.append(
                "ВНИМАНИЕ: на выбранном LAN-порту подключён "
                "кабель. После подтверждения порт будет немедленно "
                "выведен из домашней сети; связь через него пропадёт."
            )
        elif port_link not in {"down"}:
            info_lines.append(
                "ВНИМАНИЕ: состояние кабеля определить не удалось. "
                "Убедитесь, что этот компьютер подключён к роутеру "
                "через другой порт или Wi-Fi."
            )

        confirmation = QMessageBox(self)
        confirmation.setWindowTitle(
            "Создать кабельный Интернет"
        )
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText(
            f"Создать подключение «{description}» "
            f"на порту {port_label}?"
        )
        confirmation.setInformativeText(
            "\n".join(info_lines)
            + "\n\nПорт будет исключён из домашней сети. "
            "Устройство, подключённое к этому порту, потеряет "
            "локальный доступ. При ошибке программа попытается "
            "автоматически вернуть исходную конфигурацию."
        )
        confirmation.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        confirmation.setDefaultButton(
            QMessageBox.StandardButton.No
        )

        yes_button = confirmation.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = confirmation.button(
            QMessageBox.StandardButton.No
        )

        if yes_button is not None:
            yes_button.setText("Создать")

        if no_button is not None:
            no_button.setText("Назад")

        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return

        self.internet_tab.create_connection_button.setEnabled(False)
        monitor.request_create_cable_internet(settings)
        self.statusBar().showMessage(
            f"Создание {protocol_title} на порту {port_label}..."
        )

    def connection_creation_completed(
        self,
        router_id: int,
        connection_type: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        del connection_type

        if (
            router_id
            == self.get_selected_router_id()
        ):
            self.internet_tab.create_connection_button.setEnabled(
                True
            )

        if (
            router_id
            != self.get_selected_router_id()
        ):
            return

        details_dict = (
            dict(details)
            if isinstance(details, dict)
            else {}
        )

        if success:
            interface_id = str(
                details_dict.get(
                    "interface_id"
                )
                or ""
            )
            extra = (
                f"\n\nИнтерфейс: {interface_id}"
                if interface_id
                else ""
            )
            QMessageBox.information(
                self,
                "Подключение создано",
                message + extra,
            )
            self.statusBar().showMessage(
                message,
                7000,
            )
            return

        rollback_success = details_dict.get(
            "rollback_success"
        )
        rollback_text = ""

        if (
            rollback_success is True
            and "исходная конфигурация"
            not in message.lower()
            and not details_dict.get(
                "preflight_rejected"
            )
        ):
            rollback_text = (
                "\n\nИсходная конфигурация порта "
                "восстановлена."
            )
        elif rollback_success is False:
            rollback_text = (
                "\n\nАвтоматический откат завершился "
                "с ошибкой. Не отключайте питание роутера "
                "и проверьте назначение порта в веб-интерфейсе."
            )

        QMessageBox.warning(
            self,
            "Подключение не создано",
            message + rollback_text,
        )
        self.statusBar().showMessage(
            "Создание подключения завершилось ошибкой",
            7000,
        )

    def change_cable_connection_protocol(
        self,
        interface_id: str,
        interface_name: str,
        connection: object,
    ) -> None:
        details = (
            dict(connection)
            if isinstance(connection, dict)
            else {}
        )
        requested_settings = details.pop(
            "_requested_protocol_settings",
            None,
        )
        router_id = self.get_selected_router_id()

        if router_id is None:
            self.internet_tab.finish_protocol_change(
                interface_id
            )
            return

        monitor = self.router_monitors.get(router_id)

        if monitor is None:
            self.internet_tab.finish_protocol_change(
                interface_id
            )
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        if not details.get("protocol_change_supported"):
            self.internet_tab.finish_protocol_change(
                interface_id
            )
            QMessageBox.warning(
                self,
                "Смена типа недоступна",
                str(
                    details.get("protocol_change_reason")
                    or "Это подключение нельзя изменить автоматически."
                ),
            )
            return

        if isinstance(requested_settings, dict):
            settings = dict(requested_settings)
        else:
            # Обратная совместимость для старых вызовов и
            # внешних тестов. В обычном интерфейсе смена типа
            # теперь выполняется внутри окна «Настроить».
            dialog = CableProtocolChangeDialog(
                details,
                self,
            )

            if (
                dialog.exec()
                != QDialog.DialogCode.Accepted
            ):
                self.internet_tab.finish_protocol_change(
                    interface_id
                )
                return

            settings = dialog.change_settings()

            if not isinstance(settings, dict):
                self.internet_tab.finish_protocol_change(
                    interface_id
                )
                return

        target = str(
            settings.get("target_protocol") or "none"
        ).strip().lower()
        target_title = {
            "none": "IPoE",
            "pppoe": "PPPoE",
            "pptp": "PPTP",
            "l2tp": "L2TP",
        }.get(target, target.upper())
        current = str(
            details.get("protocol_change_current") or "none"
        ).strip().lower()
        current_title = {
            "none": "IPoE",
            "pppoe": "PPPoE",
            "pptp": "PPTP",
            "l2tp": "L2TP",
        }.get(current, current.upper())
        transport_id = str(
            details.get("protocol_change_transport_id")
            or interface_id
        )

        warning_lines = [
            f"Текущий тип: {current_title}",
            f"Новый тип: {target_title}",
            f"Транспорт: {transport_id}",
            (
                "Физический порт, транспортный IPv4 и WAN MAC "
                "останутся прежними. Приоритет и Ping Check будут "
                "перенесены на новый интерфейс."
            ),
            (
                "Во время переключения доступ в Интернет через "
                "это подключение кратковременно прервётся."
            ),
        ]

        if bool(details.get("protocol_change_link_active")):
            warning_lines.append(
                "Кабель подключён и будет оставлен на месте. "
                "Убедитесь, что управление роутером доступно через "
                "другой LAN-порт или Wi-Fi."
            )

        try:
            is_primary = int(details.get("global_order")) == 0
        except (TypeError, ValueError):
            is_primary = False

        if is_primary:
            warning_lines.append(
                "Это основной интернет-канал; переключение может "
                "временно прервать удалённый доступ к роутеру."
            )

        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("Сменить тип подключения")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText(
            f"Изменить «{interface_name}» на {target_title}?"
        )
        confirmation.setInformativeText(
            "\n\n".join(warning_lines)
        )
        confirmation.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        confirmation.setDefaultButton(
            QMessageBox.StandardButton.No
        )
        yes_button = confirmation.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = confirmation.button(
            QMessageBox.StandardButton.No
        )
        if yes_button is not None:
            yes_button.setText("Сменить тип")
        if no_button is not None:
            no_button.setText("Назад")

        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            self.internet_tab.finish_protocol_change(
                interface_id
            )
            return

        monitor.request_change_cable_protocol(
            interface_id,
            settings,
        )
        self.statusBar().showMessage(
            f"Смена типа «{interface_name}» на {target_title}..."
        )

    def connection_protocol_change_completed(
        self,
        router_id: int,
        interface_id: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        details_dict = (
            dict(details)
            if isinstance(details, dict)
            else {}
        )

        if router_id == self.get_selected_router_id():
            self.internet_tab.finish_protocol_change(
                interface_id
            )
        else:
            return

        if success:
            QMessageBox.information(
                self,
                "Тип подключения изменён",
                message,
            )
            self.statusBar().showMessage(
                "Тип проводного подключения изменён",
                7000,
            )
            return

        title = (
            "Тип изменён частично"
            if details_dict.get("partial_success")
            else "Тип подключения не изменён"
        )
        QMessageBox.warning(self, title, message)
        self.statusBar().showMessage(
            "Смена типа подключения завершилась ошибкой",
            7000,
        )

    def delete_internet_connection(
        self,
        interface_id: str,
        interface_name: str,
        connection: object,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            self.internet_tab.finish_connection_delete(
                interface_id
            )
            return

        monitor = self.router_monitors.get(router_id)

        if monitor is None:
            self.internet_tab.finish_connection_delete(
                interface_id
            )
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        details = (
            dict(connection)
            if isinstance(connection, dict)
            else {}
        )

        if not details.get("delete_supported"):
            self.internet_tab.finish_connection_delete(
                interface_id
            )
            QMessageBox.warning(
                self,
                "Удаление недоступно",
                str(
                    details.get("delete_reason")
                    or (
                        "Это подключение нельзя безопасно "
                        "удалить в текущей версии."
                    )
                ),
            )
            return

        delete_kind = str(
            details.get("delete_kind") or ""
        ).strip().lower()

        if delete_kind not in {
            "ethernet.dedicated",
            "ethernet.ipoe",
            "ethernet.pppoe",
            "ethernet.pptp",
            "ethernet.l2tp",
            "wireless.isp",
        }:
            self.internet_tab.finish_connection_delete(
                interface_id
            )
            QMessageBox.warning(
                self,
                "Удаление недоступно",
                "Не удалось определить безопасный сценарий удаления.",
            )
            return

        if delete_kind == "wireless.isp":
            band_title = str(
                details.get("wireless_isp_band_title")
                or "не определён"
            )
            ssid = str(
                details.get("wireless_isp_ssid")
                or details.get("ssid")
                or interface_name
            ).strip()
            order_value = details.get("global_order")

            try:
                order = int(order_value)
            except (TypeError, ValueError):
                order = None

            warning_lines = [
                (
                    f"Настройки Wireless ISP {band_title} для сети "
                    f"«{ssid}» будут удалены. Диапазон снова станет "
                    "доступен в мастере создания подключений."
                )
            ]

            if bool(details.get("delete_port_link_active")):
                warning_lines.append(
                    "Текущее беспроводное соединение будет немедленно "
                    "разорвано. Домашние Wi-Fi-сети этого диапазона "
                    "при этом не удаляются."
                )

            if order == 0:
                warning_lines.append(
                    "Это основной интернет-канал. Доступ в Интернет "
                    "может прерваться до переключения на резервный канал."
                )

            message_box = QMessageBox(self)
            message_box.setWindowTitle(
                "Удалить Wireless ISP"
            )
            message_box.setIcon(
                QMessageBox.Icon.Warning
            )
            message_box.setText(
                f"Удалить подключение «{interface_name}»?"
            )
            message_box.setInformativeText(
                "\n\n".join(warning_lines)
            )
            message_box.setStandardButtons(
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            )
            message_box.setDefaultButton(
                QMessageBox.StandardButton.No
            )

            yes_button = message_box.button(
                QMessageBox.StandardButton.Yes
            )
            no_button = message_box.button(
                QMessageBox.StandardButton.No
            )

            if yes_button is not None:
                yes_button.setText("Удалить")

            if no_button is not None:
                no_button.setText("Назад")

            if (
                message_box.exec()
                != QMessageBox.StandardButton.Yes
            ):
                self.internet_tab.finish_connection_delete(
                    interface_id
                )
                return

            monitor.request_delete_wireless_isp(
                interface_id
            )
            self.statusBar().showMessage(
                f"Удаление Wireless ISP «{interface_name}»..."
            )
            return

        port_label = str(
            details.get("delete_port_label")
            or "?"
        )
        dedicated_wan = bool(
            details.get("delete_dedicated_wan")
            or delete_kind == "ethernet.dedicated"
        )
        port_display_name = str(
            details.get("delete_port_display_name")
            or (
                "WAN (порт "
                + str(details.get("delete_port_number") or "0")
                + ")"
                if dedicated_wan
                else "LAN" + port_label
            )
        )
        port_link_known = details.get(
            "delete_port_link_known"
        )
        port_link_active = bool(
            details.get("delete_port_link_active")
        )

        home_vlan = details.get(
            "delete_restore_vlan"
        )
        try:
            order = int(
                details.get("global_order")
            )
        except (TypeError, ValueError):
            order = None

        if delete_kind == "ethernet.dedicated":
            transport_id = str(
                details.get("delete_transport_id")
                or interface_id
            )
            protocol_title = str(
                details.get("provider_protocol")
                or details.get("type")
                or "IPoE"
            ).strip()
            if transport_id != interface_id:
                removal_text = (
                    f"{protocol_title}-интерфейс {interface_id} будет "
                    f"удалён, а его аппаратный транспорт {transport_id} "
                    "будет переведён в домашнюю сеть."
                )
            else:
                removal_text = (
                    f"Настройки интернет-канала {interface_id} будут "
                    "удалены, а физический WAN-порт будет переведён "
                    "в домашнюю сеть."
                )
        elif delete_kind in {
            "ethernet.pppoe",
            "ethernet.pptp",
            "ethernet.l2tp",
        }:
            transport_id = str(
                details.get("delete_transport_id") or "?"
            )
            protocol_title = {
                "ethernet.pppoe": "PPPoE",
                "ethernet.pptp": "PPTP",
                "ethernet.l2tp": "L2TP",
            }[delete_kind]
            removal_text = (
                f"{protocol_title}-интерфейс {interface_id} и его "
                f"транспорт {transport_id} будут удалены вместе "
                "со всеми настройками без возможности отмены."
            )
        else:
            removal_text = (
                f"Интерфейс {interface_id} и все его настройки "
                "будут удалены без возможности отмены."
            )

        warning_lines = [removal_text]

        if dedicated_wan:
            warning_lines.append(
                f"{port_display_name} станет локальным LAN0 и будет "
                "включён в домашний сетевой мост."
            )
        else:
            warning_lines.append(
                f"{port_display_name} будет возвращён в домашнюю сеть"
                + (
                    f" (VLAN {home_vlan})."
                    if home_vlan is not None
                    else "."
                )
            )

        if port_link_active:
            warning_lines.append(
                f"ВНИМАНИЕ: на {port_display_name} сейчас подключён "
                "кабель. Сразу после удаления этот кабель окажется "
                "в домашней сети. Если он подключён к провайдеру "
                "или другому роутеру, возможны чужой DHCP и потеря "
                "доступа к Keenetic."
            )
        elif port_link_known is False:
            warning_lines.append(
                "ВНИМАНИЕ: состояние кабеля не определено. После "
                "удаления проверьте, что к возвращённому LAN-порту "
                "не подключена внешняя сеть."
            )

        if order == 0:
            warning_lines.append(
                (
                    "Это основной интернет-канал. Во время "
                    "удаления доступ в Интернет может прерваться."
                )
            )

        message_box = QMessageBox(self)
        message_box.setWindowTitle(
            "Удалить интернет-подключение"
        )
        message_box.setIcon(
            QMessageBox.Icon.Warning
        )
        message_box.setText(
            (
                f"Удалить подключение "
                f"«{interface_name}»?"
            )
        )
        message_box.setInformativeText(
            "\n\n".join(warning_lines)
        )
        message_box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        message_box.setDefaultButton(
            QMessageBox.StandardButton.No
        )

        yes_button = message_box.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = message_box.button(
            QMessageBox.StandardButton.No
        )

        if yes_button is not None:
            yes_button.setText("Удалить")

        if no_button is not None:
            no_button.setText("Назад")

        if (
            message_box.exec()
            != QMessageBox.StandardButton.Yes
        ):
            self.internet_tab.finish_connection_delete(
                interface_id
            )
            return

        if delete_kind == "ethernet.dedicated":
            monitor.request_delete_dedicated_wan(
                interface_id
            )
        elif delete_kind == "ethernet.pppoe":
            monitor.request_delete_wired_pppoe(
                interface_id
            )
        elif delete_kind == "ethernet.pptp":
            monitor.request_delete_wired_pptp(
                interface_id
            )
        elif delete_kind == "ethernet.l2tp":
            monitor.request_delete_wired_l2tp(
                interface_id
            )
        else:
            monitor.request_delete_wired_ipoe(
                interface_id
            )

        self.statusBar().showMessage(
            f"Удаление подключения «{interface_name}»..."
        )

    def connection_delete_completed(
        self,
        router_id: int,
        interface_id: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        del details

        if (
            router_id == self.get_selected_router_id()
            and hasattr(self, "internet_tab")
        ):
            self.internet_tab.finish_connection_delete(
                interface_id
            )

        if router_id != self.get_selected_router_id():
            return

        if success:
            QMessageBox.information(
                self,
                "Подключение удалено",
                message,
            )
            self.statusBar().showMessage(
                "Интернет-подключение удалено",
                7000,
            )
        else:
            QMessageBox.warning(
                self,
                "Подключение не удалено",
                message,
            )
            self.statusBar().showMessage(
                "Удаление подключения завершилось ошибкой",
                7000,
            )

    def change_connection_interface_settings(
        self,
        interface_id: str,
        interface_name: str,
        settings: dict,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(
            router_id
        )

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        descriptions: list[str] = []

        if "description" in settings:
            descriptions.append(
                "Имя: "
                f"{settings['description']}"
            )

        if "mtu" in settings:
            descriptions.append(
                f"MTU: {settings['mtu']}"
            )

        if "use_provider_dns" in settings:
            descriptions.append(
                (
                    "DNS от провайдера: "
                    + (
                        "использовать"
                        if settings["use_provider_dns"]
                        else "не использовать"
                    )
                )
            )

        if "manual_dns" in settings:
            manual_dns = list(
                settings.get("manual_dns")
                or []
            )

            descriptions.append(
                (
                    "Ручные DNS: "
                    + (
                        ", ".join(manual_dns)
                        if manual_dns
                        else "удалить"
                    )
                )
            )

        if "ipv4" in settings:
            ipv4 = dict(
                settings.get("ipv4")
                or {}
            )

            if ipv4.get("dhcp"):
                descriptions.append(
                    "IPv4: автоматически (DHCP)"
                )
            else:
                descriptions.append(
                    (
                        "IPv4: "
                        f"{ipv4.get('address')}/"
                        f"{ipv4.get('prefix')}"
                    )
                )
                descriptions.append(
                    (
                        "Шлюз: "
                        f"{ipv4.get('gateway')}"
                    )
                )

        if "dhcp_client" in settings:
            dhcp_client = dict(
                settings.get("dhcp_client")
                or {}
            )
            descriptions.append(
                (
                    "Имя DHCP-клиента: "
                    f"{dhcp_client.get('hostname')}"
                )
            )
            descriptions.append(
                (
                    "Маршруты DHCP: "
                    + (
                        "принимать"
                        if dhcp_client.get("routes")
                        else "не принимать"
                    )
                )
            )
            descriptions.append(
                (
                    "Маршруты к DNS: "
                    + (
                        "создавать"
                        if dhcp_client.get(
                            "dns_routes"
                        )
                        else "не создавать"
                    )
                )
            )

        if "mac" in settings:
            mac_settings = dict(
                settings.get("mac")
                or {}
            )

            if mac_settings.get("mode") == "factory":
                descriptions.append(
                    "MAC: заводской WAN"
                )
            else:
                descriptions.append(
                    (
                        "MAC: "
                        f"{mac_settings.get('address')}"
                    )
                )

        if "ping_check" in settings:
            ping_check = dict(
                settings.get("ping_check")
                or {}
            )
            ping_mode = str(
                ping_check.get("mode")
                or (
                    "icmp"
                    if ping_check.get("enabled")
                    else "disabled"
                )
            )

            if ping_mode == "disabled":
                descriptions.append(
                    "Ping Check: выключить"
                )
            elif ping_mode == "automatic":
                descriptions.append(
                    "Ping Check: автоматически (Keenetic)"
                )
            else:
                mode_titles = {
                    "icmp": "ICMP echo",
                    "tls": "TCP/TLS",
                    "connect": "TCP",
                }
                descriptions.append(
                    (
                        "Ping Check "
                        f"{mode_titles.get(ping_mode, ping_mode)}: "
                        f"{ping_check.get('host')}"
                    )
                )

                if ping_mode in {
                    "tls",
                    "connect",
                }:
                    descriptions.append(
                        (
                            "TCP-порт: "
                            f"{ping_check.get('port')}"
                        )
                    )

                descriptions.append(
                    (
                        "Интервал: "
                        f"{ping_check.get('interval')} с"
                    )
                )
                descriptions.append(
                    (
                        "Неудачных попыток: "
                        f"{ping_check.get('max_fails')}"
                    )
                )

        if "wireless_isp" in settings:
            wireless_settings = dict(
                settings.get("wireless_isp")
                or {}
            )

            if "ssid" in wireless_settings:
                descriptions.append(
                    "Wi-Fi-сеть: "
                    + str(wireless_settings.get("ssid") or "")
                )

            if "bssid" in wireless_settings:
                descriptions.append(
                    "BSSID: "
                    + (
                        str(wireless_settings.get("bssid") or "")
                        or "любая точка с этим SSID"
                    )
                )

            if "open_network" in wireless_settings:
                descriptions.append(
                    "Защита Wi-Fi: "
                    + (
                        "открытая сеть"
                        if wireless_settings.get("open_network")
                        else "WPA/WPA2"
                    )
                )

            if wireless_settings.get("password"):
                descriptions.append(
                    "Пароль Wi-Fi: заменить (значение скрыто)"
                )

        message_box = QMessageBox(self)
        message_box.setWindowTitle(
            "Сохранить параметры"
        )
        message_box.setText(
            (
                f"Изменить параметры подключения "
                f"«{interface_name}»?\n\n"
                + "\n".join(descriptions)
            )
        )
        if "wireless_isp" in settings:
            message_box.setInformativeText(
                "Смена SSID, BSSID или пароля кратковременно "
                "разорвёт Wireless ISP. Пароль не будет показан "
                "в сообщениях и диагностике."
            )
        elif "ipv4" in settings:
            message_box.setInformativeText(
                "Изменение IPv4 может прервать текущее "
                "соединение с роутером. Проверьте адрес, "
                "префикс и шлюз перед сохранением."
            )
        elif "mac" in settings:
            message_box.setInformativeText(
                "Изменение WAN MAC может временно "
                "разорвать соединение и привести к выдаче "
                "нового IP-адреса провайдером."
            )
        elif (
            "ping_check" in settings
            and str(
                settings["ping_check"].get("mode")
                or (
                    "icmp"
                    if settings["ping_check"].get("enabled")
                    else "disabled"
                )
            )
            != "disabled"
        ):
            message_box.setInformativeText(
                "При недоступном узле проверки Keenetic "
                "может признать канал нерабочим и перейти "
                "на подключение с более низким приоритетом."
            )
        else:
            message_box.setInformativeText(
                "Во время применения MTU соединение может "
                "кратковременно прерваться."
            )
        message_box.setIcon(
            QMessageBox.Icon.Question
        )
        message_box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        message_box.setDefaultButton(
            QMessageBox.StandardButton.No
        )

        yes_button = message_box.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = message_box.button(
            QMessageBox.StandardButton.No
        )

        if yes_button is not None:
            yes_button.setText("Да")

        if no_button is not None:
            no_button.setText("Нет")

        if (
            message_box.exec()
            != QMessageBox.StandardButton.Yes
        ):
            return

        monitor.request_interface_settings(
            interface_id=interface_id,
            settings=settings,
        )

        self.statusBar().showMessage(
            f"Сохранение параметров «{interface_name}»..."
        )

    def interface_settings_completed(
        self,
        router_id: int,
        interface_id: str,
        success: bool,
        message: str,
    ) -> None:
        del interface_id

        if success:
            QMessageBox.information(
                self,
                "Параметры сохранены",
                message,
            )
        else:
            QMessageBox.warning(
                self,
                "Параметры не сохранены",
                message,
            )

    def reconnect_connection_interface(
        self,
        interface_id: str,
        interface_name: str,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        monitor = self.router_monitors.get(
            router_id
        )

        if monitor is None:
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle(
            "Переподключить подключение"
        )
        message_box.setText(
            (
                f"Переподключить интерфейс "
                f"«{interface_name}»?\n\n"
                "Соединение кратковременно прервётся."
            )
        )
        message_box.setIcon(
            QMessageBox.Icon.Question
        )
        message_box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        message_box.setDefaultButton(
            QMessageBox.StandardButton.No
        )

        yes_button = message_box.button(
            QMessageBox.StandardButton.Yes
        )
        no_button = message_box.button(
            QMessageBox.StandardButton.No
        )

        if yes_button is not None:
            yes_button.setText("Да")

        if no_button is not None:
            no_button.setText("Нет")

        answer = message_box.exec()

        if answer != QMessageBox.StandardButton.Yes:
            if hasattr(
                self,
                "internet_tab",
            ):
                self.internet_tab.finish_interface_reconnect(
                    interface_id
                )

            monitor.request_refresh()
            return

        monitor.request_interface_reconnect(
            interface_id
        )

        self.statusBar().showMessage(
            f"Переподключение «{interface_name}»..."
        )

    def interface_reconnect_completed(
        self,
        router_id: int,
        interface_id: str,
        success: bool,
        message: str,
    ) -> None:
        if (
            router_id
            == self.get_selected_router_id()
            and hasattr(
                self,
                "internet_tab",
            )
        ):
            self.internet_tab.finish_interface_reconnect(
                interface_id
            )

        if success:
            QMessageBox.information(
                self,
                "Готово",
                message,
            )
        else:
            QMessageBox.warning(
                self,
                "Переподключение не выполнено",
                message,
            )

    def interface_action_completed(
        self,
        router_id: int,
        interface_id: str,
        success: bool,
        message: str,
    ) -> None:
        batch = self.pending_interface_batches.get(
            router_id
        )

        if batch is not None:
            remaining = batch.get("remaining")

            if (
                isinstance(remaining, set)
                and interface_id in remaining
            ):
                remaining.discard(interface_id)

                result_item = (
                    f"{interface_id}: {message}"
                )

                target_list = (
                    batch.get("successes")
                    if success
                    else batch.get("errors")
                )

                if isinstance(target_list, list):
                    target_list.append(result_item)

                if remaining:
                    return

                self.pending_interface_batches.pop(
                    router_id,
                    None,
                )
                if (
                    router_id == self.get_selected_router_id()
                    and hasattr(self, "clients_tab")
                ):
                    self.clients_tab.set_port_action_pending(
                        list(batch.get("interfaces") or [interface_id]),
                        False,
                    )

                if router_id != self.get_selected_router_id():
                    return

                errors = batch.get("errors")
                display_name = str(
                    batch.get("display_name")
                    or "интерфейс"
                )
                enabled = bool(
                    batch.get("enabled")
                )

                if isinstance(errors, list) and errors:
                    QMessageBox.warning(
                        self,
                        "Команда выполнена не полностью",
                        (
                            f"Не удалось полностью "
                            f"{'включить' if enabled else 'выключить'} "
                            f"«{display_name}».\n\n"
                            + "\n".join(errors)
                        ),
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Готово",
                        (
                            f"Операция завершена: «{display_name}» — "
                            f"интерфейс {'включён' if enabled else 'выключен'}."
                        ),
                    )

                return

        # Обычная операция над одним WAN/VPN/портом.
        if success:
            QMessageBox.information(
                self,
                "Готово",
                message,
            )

        else:
            QMessageBox.warning(
                self,
                "Команда не выполнена",
                message,
            )

    def clear_wifi_network_cards(self) -> None:
        cards = getattr(
            self,
            "wifi_network_cards",
            {},
        )

        for card in cards.values():
            self.wifi_networks_layout.removeWidget(
                card
            )
            card.deleteLater()

        self.wifi_network_cards = {}

        wrapper = getattr(
            self,
            "wifi_network_empty_wrapper",
            None,
        )

        if wrapper is not None:
            self.wifi_networks_layout.removeWidget(
                wrapper
            )
            wrapper.deleteLater()
            self.wifi_network_empty_wrapper = None
            self.wifi_network_empty_label = None

    def clear_port_cards(self) -> None:
        cards = getattr(
            self,
            "port_cards",
            {},
        )

        for card in cards.values():
            self.ports_layout.removeWidget(card)
            card.deleteLater()

        self.port_cards = {}

    def open_usb_smb_manager(
        self,
        device: dict,
    ) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        router = self.database.get_router(
            router_id
        )

        if not router:
            QMessageBox.warning(
                self,
                "Роутер не найден",
                "Не удалось получить параметры роутера.",
            )
            return

        local_ip = str(
            router.get("local_ip")
            or ""
        ).strip()

        if not local_ip:
            QMessageBox.warning(
                self,
                "Нет локального адреса",
                (
                    "Для SMB-доступа у роутера должен быть "
                    "указан локальный IP-адрес."
                ),
            )
            return

        dialog = self.smb_file_managers.get(
            router_id
        )

        if dialog is None:
            dialog = SmbFileManagerDialog(
                router_id=router_id,
                host=local_ip,
                device=device,
                parent=self,
            )
            dialog.finished.connect(
                lambda _result, rid=router_id:
                self.smb_file_managers.pop(
                    rid,
                    None,
                )
            )
            self.smb_file_managers[
                router_id
            ] = dialog

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_ports(
        self,
        ports: list[dict] | None,
    ) -> None:
        """
        Отображает уже подготовленный PortEngine список портов.

        Повторная дедупликация в UI намеренно не выполняется:
        PortEngine является единственным источником нормализации.
        """
        if not hasattr(self, "port_cards"):
            self.port_cards = {}

        self.port_overview.set_ports(
            ports
        )

        incoming: dict[str, dict] = {}

        for index, port in enumerate(
            ports or []
        ):
            interface_id = str(
                port.get("interface_id")
                or ""
            ).strip()
            port_id = str(
                port.get("id")
                or ""
            ).strip()
            kind = str(
                port.get("kind")
                or "ethernet"
            ).strip().lower()

            # PortEngine уже возвращает стабильный физический ID.
            key = (
                f"{kind}:"
                f"{interface_id or port_id or index}"
            ).lower()

            # Дополнительная страховка от точного повторения ключа.
            # Никакие методы PortEngine здесь не вызываются.
            if key in incoming:
                current = incoming[key]
                merged = dict(current)

                merged["connected"] = bool(
                    current.get("connected")
                    or port.get("connected")
                )
                merged["enabled"] = bool(
                    current.get("enabled", True)
                    and port.get("enabled", True)
                )

                for field, value in port.items():
                    existing = merged.get(field)

                    if (
                        existing in (None, "", "-", "—")
                        and value
                        not in (None, "", "-", "—")
                    ):
                        merged[field] = value

                incoming[key] = merged
            else:
                incoming[key] = dict(port)

        for key in list(self.port_cards):
            if key in incoming:
                continue

            card = self.port_cards.pop(key)
            self.ports_layout.removeWidget(card)
            card.deleteLater()

        ordered_keys = list(incoming.keys())

        for index, key in enumerate(
            ordered_keys
        ):
            port = incoming[key]
            card = self.port_cards.get(key)

            if card is None:
                card = PortCard(
                    port,
                    self.ports_container,
                )
                card.usb_open_requested.connect(
                    self.open_usb_smb_manager
                )
                self.port_cards[key] = card
                self.ports_layout.addWidget(card)
            else:
                card.update_port(port)

            if (
                self.ports_layout.indexOf(card)
                != index
            ):
                self.ports_layout.insertWidget(
                    index,
                    card,
                )

        self.ports_layout.activate()
        self.ports_container.updateGeometry()

    def show_wifi_networks(
        self,
        networks: list[dict] | None,
    ) -> None:
        """
        Обновляет существующие карточки без удаления и
        повторного создания при каждом цикле мониторинга.
        """
        temporary_wrapper = getattr(
            self,
            "wifi_network_empty_wrapper",
            None,
        )

        if temporary_wrapper is not None:
            self.wifi_networks_layout.removeWidget(
                temporary_wrapper
            )
            temporary_wrapper.deleteLater()
            self.wifi_network_empty_wrapper = None
            self.wifi_network_empty_label = None

        if not hasattr(
            self,
            "wifi_network_cards",
        ):
            self.wifi_network_cards = {}

        networks = networks or []

        incoming = {
            WifiNetworkCard.make_network_key(
                network
            ): network
            for network in networks
        }

        # Удаляем только сети, реально исчезнувшие из данных.
        for key in list(
            self.wifi_network_cards
        ):
            if key in incoming:
                continue

            card = self.wifi_network_cards.pop(
                key
            )
            self.wifi_networks_layout.removeWidget(
                card
            )
            card.deleteLater()

        ordered_cards = []

        for network in networks:
            key = WifiNetworkCard.make_network_key(
                network
            )
            card = self.wifi_network_cards.get(
                key
            )

            if card is None:
                card = WifiNetworkCard(
                    network,
                    self.wifi_networks_container,
                )
                card.state_change_requested.connect(
                    self.change_network_interfaces_state
                )
                card.delete_requested.connect(
                    self.delete_wifi_network
                )
                self.wifi_network_cards[key] = card
                self.wifi_networks_layout.addWidget(
                    card
                )
            else:
                card.update_network(
                    network
                )

            ordered_cards.append(card)

        # Меняем порядок без уничтожения карточек.
        for index, card in enumerate(
            ordered_cards
        ):
            self.wifi_networks_layout.insertWidget(
                index,
                card,
            )

        empty = getattr(
            self,
            "wifi_network_empty_label",
            None,
        )

        if empty is not None:
            empty.hide()

    def show_internet_connections(
        self,
        connections: list[dict] | None,
    ) -> None:
        """Обновляет карточки без уничтожения на каждом цикле."""
        router_id = self.get_selected_router_id()
        normalized = list(connections or [])

        if router_id is None:
            normalized = []

        incoming_ids: list[str] = []
        incoming: dict[str, dict] = {}

        for index, raw_connection in enumerate(
            normalized
        ):
            connection = dict(raw_connection)
            interface_id = str(
                connection.get("interface_id")
                or connection.get("name")
                or f"connection-{index}"
            ).strip()
            connection["_router_id"] = router_id
            incoming_ids.append(interface_id)
            incoming[interface_id] = connection

        for interface_id in list(
            self.internet_connection_cards
        ):
            if interface_id in incoming:
                continue
            card = self.internet_connection_cards.pop(
                interface_id
            )
            old_router_id = card.connection.get("_router_id")
            if old_router_id is not None:
                self.traffic_history.pop(
                    (int(old_router_id), interface_id),
                    None,
                )
            self.internet_connections_layout.removeWidget(
                card
            )
            card.deleteLater()

        if not incoming_ids:
            self.show_empty_internet_state(
                "Нет подключений"
            )
            self.internet_connections_container.updateGeometry()
            return

        if self.internet_connection_empty_label is not None:
            self.internet_connection_empty_label.hide()

        ordered_cards: list[ConnectionCard] = []

        for interface_id in incoming_ids:
            connection = incoming[interface_id]
            history = self.connection_history(
                router_id=int(router_id),
                interface_id=interface_id,
            )
            try:
                sample_time = float(
                    connection.get("traffic_sample_time")
                )
            except (TypeError, ValueError):
                sample_time = monotonic()
            if not math.isfinite(sample_time):
                sample_time = monotonic()

            # Один и тот же кэшированный результат может повторно
            # отобразиться при смене вкладки или роутера. Такая
            # перерисовка не является новым измерением.
            if history["last_sample_time"] != sample_time:
                history["rx"].append(
                    self.normalized_traffic_speed(
                        connection.get("rxspeed")
                    )
                )
                history["tx"].append(
                    self.normalized_traffic_speed(
                        connection.get("txspeed")
                    )
                )
                history["time"].append(sample_time)
                history["last_sample_time"] = sample_time

            card = self.internet_connection_cards.get(
                interface_id
            )

            if card is None:
                card = ConnectionCard(
                    connection=connection,
                    rx_history=history["rx"],
                    tx_history=history["tx"],
                    sample_times=history["time"],
                    parent=self.internet_connections_container,
                )
                card.state_change_requested.connect(
                    self.change_connection_interface_state
                )
                self.internet_connection_cards[
                    interface_id
                ] = card
            else:
                card.update_connection(
                    connection=connection,
                    rx_history=history["rx"],
                    tx_history=history["tx"],
                    sample_times=history["time"],
                )

            ordered_cards.append(card)

        for index, card in enumerate(ordered_cards):
            self.internet_connections_layout.insertWidget(
                index,
                card,
            )

        self.internet_connections_layout.activate()
        self.internet_connections_container.updateGeometry()

    @staticmethod
    def normalized_traffic_speed(value: object) -> float:
        return TrafficService.normalize_rate(value)

    def clear_router_traffic_history(self, router_id: int) -> None:
        for key in tuple(self.traffic_history):
            if key[0] == router_id:
                self.traffic_history.pop(key, None)

    def show_connection_result(
        self,
        result,
    ) -> None:
        self.set_router_content_connected(True)

        self.status_label.setText(
            "● Online"
        )
        self.set_router_monitor_ui_state("connected")

        connected_url = (
            result.connected_url
            or "неизвестно"
        )
        connection_type = (
            result.connection_type
            or "неизвестно"
        )

        connection_method = (
            self._format_connection_method(
                connection_type
            )
        )

        self.connected_address_label.setText(
            "Фактический адрес: "
            f"{connected_url}\n"
            "Подключение: "
            f"{connection_method}"
        )
        self.model_label.setText(
            f"{result.model or 'Не определено'}"
        )
        self.version_label.setText(
            "KeeneticOS: "
            f"{result.version or 'Не определена'}"
        )
        self.update_settings_router_profile(result)
        self.uptime_label.setText(
            "Время работы: "
            f"{result.uptime or 'Не определено'}"
        )
        cpu_text = (
            result.cpu_load
            or "Не определено"
        )
        cpu_value = self.percentage_value(
            result.cpu_load
        )

        self.cpu_metric.set_metric(
            cpu_text,
            cpu_value,
        )

        ram_text = (
            result.ram_usage
            or "Не определено"
        )
        ram_value = self.percentage_value(
            result.ram_usage
        )

        self.ram_metric.set_metric(
            ram_text,
            ram_value,
        )

        self.show_internet_connections(
            result.internet_connections
        )

        if hasattr(self, "internet_tab"):
            self.internet_tab.set_router(
                self.get_selected_router_id()
            )
            self.internet_tab.set_connections(
                result.internet_connections
            )

        self.show_wifi_networks(
            result.wifi_networks
        )

        rescue_context = RescueModeService.inspect(
            result.internet_connections,
            result.wifi_networks,
        )
        pending_rescue_mode = self.pending_rescue_modes.get(
            int(self.get_selected_router_id() or 0)
        )
        if pending_rescue_mode:
            self.rescue_mode_switch.set_busy(
                pending_rescue_mode
            )
        else:
            self.rescue_mode_switch.set_context(rescue_context)

        if hasattr(self, "wifi_tab"):
            self.wifi_tab.set_router(
                self.get_selected_router_id()
            )
            self.wifi_tab.set_capabilities(
                getattr(
                    result,
                    "wifi_capabilities",
                    None,
                )
            )
            self.wifi_tab.set_networks(
                result.wifi_networks
            )

        if hasattr(self, "clients_tab"):
            self.clients_tab.set_router(
                self.get_selected_router_id()
            )
            client_inventory = getattr(result, "clients", None)
            if client_inventory is None:
                self.clients_tab.set_networks(
                    result.wifi_networks
                )
            else:
                self.clients_tab.set_clients(
                    client_inventory
                )
            self.clients_tab.set_ports(
                result.ports
            )

        if hasattr(self, "journal_tab"):
            self.journal_tab.set_router(
                self.get_selected_router_id()
            )
            current_index = self.tabs.currentIndex()
            if (
                0 <= current_index < len(self.tab_titles)
                and self.tab_titles[current_index] == "Журнал"
            ):
                self.load_current_journal_if_needed()

        self.show_ports(
            result.ports
        )


    def change_rescue_mode(
        self,
        mode: str,
    ) -> None:
        router_id = self.get_selected_router_id()
        if router_id is None:
            return

        result = self.connection_results.get(router_id)
        monitor = self.router_monitors.get(router_id)
        if (
            result is None
            or not getattr(result, "success", False)
            or monitor is None
        ):
            QMessageBox.warning(
                self,
                "Режим работы",
                "Сначала подключитесь к роутеру.",
            )
            return

        context = RescueModeService.inspect(
            getattr(result, "internet_connections", None),
            getattr(result, "wifi_networks", None),
        )
        if not context.available:
            self.rescue_mode_switch.set_context(context)
            QMessageBox.warning(
                self,
                "Режим работы",
                context.message,
            )
            return

        requested_mode = str(mode or "").strip().lower()
        if requested_mode not in {
            RESCUER_MODE,
            RESCUED_MODE,
        }:
            return

        if router_id in self.pending_rescue_modes:
            self.statusBar().showMessage(
                "Переключение режима уже выполняется.",
                3000,
            )
            return

        if context.mode == requested_mode:
            self.rescue_mode_switch.set_context(context)
            self.statusBar().showMessage(
                context.message,
                3000,
            )
            return

        self.pending_rescue_modes[router_id] = requested_mode
        self.rescue_mode_switch.set_busy(requested_mode)
        try:
            monitor.request_rescue_mode(
                requested_mode,
                context.payload(),
            )
        except Exception as error:
            self.pending_rescue_modes.pop(router_id, None)
            self.rescue_mode_switch.set_context(context)
            QMessageBox.warning(
                self,
                "Переключение режима",
                f"Не удалось поставить команду в очередь: {error}",
            )
            return
        title = (
            "Спасатель"
            if requested_mode == RESCUER_MODE
            else "Спасаемый"
        )
        self.statusBar().showMessage(
            f"Включение режима «{title}»..."
        )

    def rescue_mode_action_completed(
        self,
        router_id: int,
        mode: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        del details

        requested_mode = str(mode or "").strip().lower()
        pending_mode = self.pending_rescue_modes.pop(
            int(router_id),
            None,
        )
        if requested_mode not in {
            RESCUER_MODE,
            RESCUED_MODE,
        }:
            requested_mode = str(pending_mode or "")

        if router_id != self.get_selected_router_id():
            return

        result = self.connection_results.get(router_id)
        context = (
            RescueModeService.inspect(
                getattr(result, "internet_connections", None),
                getattr(result, "wifi_networks", None),
            )
            if result is not None
            else None
        )

        if success:
            if context is not None:
                self.rescue_mode_switch.set_context(
                    RescueModeService.with_confirmed_mode(
                        context,
                        requested_mode,
                    )
                )
            else:
                self.rescue_mode_switch.clear()
            self.statusBar().showMessage(
                message,
                5000,
            )
            return

        # До поступления немедленного свежего снимка возвращаем
        # переключатель к последнему подтверждённому состоянию.
        if context is not None:
            self.rescue_mode_switch.set_context(context)
        else:
            self.rescue_mode_switch.clear()

        QMessageBox.warning(
            self,
            "Переключение режима",
            message,
        )

    def wifi_network_action_completed(
        self,
        router_id: int,
        operation: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        if router_id != self.get_selected_router_id():
            return

        # Request an immediate fresh snapshot after every Wi-Fi action.
        # The normal two-second poll can otherwise leave the card in the
        # pre-action state while the modal result message is open.
        monitor = self.router_monitors.get(router_id)
        if monitor is not None:
            monitor.request_refresh()

        title = {
            "create": "Создание Wi-Fi сети",
            "update": "Изменение Wi-Fi сети",
            "state": "Состояние Wi-Fi сети",
            "delete": "Удаление Wi-Fi сети",
        }.get(operation, "Wi-Fi")

        if success:
            QMessageBox.information(
                self,
                "Готово",
                message,
            )
        else:
            QMessageBox.warning(
                self,
                title,
                message,
            )

    def wifi_client_access_completed(
        self,
        router_id: int,
        mac: str,
        success: bool,
        message: str,
        details: object,
    ) -> None:
        del mac, details
        if router_id != self.get_selected_router_id():
            return

        self.clients_tab.set_wifi_action_pending(False)
        monitor = self.router_monitors.get(router_id)
        if monitor is not None:
            monitor.request_refresh()

        if success:
            QMessageBox.information(
                self,
                "Готово",
                message,
            )
        else:
            QMessageBox.warning(
                self,
                "Доступ Wi-Fi клиента",
                message,
            )

    def internet_priorities_changed(
        self,
        priorities: dict,
    ) -> None:
        router_id = self.get_selected_router_id()

        _priority_trace(
            router_id,
            f"2. MainWindow received: {priorities}",
        )

        if router_id is None:
            self.internet_tab.rollback_priorities()
            return

        monitor = self.router_monitors.get(
            router_id
        )

        if monitor is None:
            self.internet_tab.rollback_priorities()
            QMessageBox.warning(
                self,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        _priority_trace(
            router_id,
            (
                f"3. MainWindow queue router={router_id}: "
                f"{priorities}"
            ),
        )
        monitor.request_internet_priorities(
            priorities
        )

        self.statusBar().showMessage(
            "Изменение приоритетов... "
            f"Диагностика: priority_debug_router_{router_id}.log"
        )

    def internet_priorities_action_completed(
        self,
        router_id: int,
        success: bool,
        message: str,
        applied_priorities: object,
    ) -> None:
        if (
            router_id
            != self.get_selected_router_id()
        ):
            return

        if success:
            priorities = (
                dict(applied_priorities)
                if isinstance(
                    applied_priorities,
                    dict,
                )
                else {}
            )
            self.internet_tab.confirm_priorities(
                priorities
            )

            QMessageBox.information(
                self,
                "Приоритеты изменены",
                message,
            )
            self.statusBar().showMessage(
                message,
                5000,
            )
            return

        self.internet_tab.rollback_priorities(
            message
        )
        QMessageBox.warning(
            self,
            "Приоритеты не изменены",
            message,
        )

    def disconnect_selected_router(self) -> None:
        router_id = self.get_selected_router_id()

        if router_id is None:
            return

        router = self.database.get_router(
            router_id
        )

        if router is None:
            QMessageBox.warning(
                self,
                "Роутер не найден",
                "Запись отсутствует в базе данных.",
            )
            return

        try:
            self.database.update_router_status(
                router_id=router_id,
                status=False,
                touch_last_seen=False,
            )
            self.persisted_router_status[
                router_id
            ] = False

        except sqlite3.Error as error:
            QMessageBox.critical(
                self,
                "Ошибка базы данных",
                str(error),
            )
            return

        monitor = self.router_monitors.pop(
            router_id,
            None,
        )

        self.connection_results.pop(
            router_id,
            None,
        )
        self.pending_rescue_modes.pop(router_id, None)
        self.clear_router_traffic_history(router_id)
        self.last_monitor_results.pop(router_id, None)
        self.developer_mode_by_router.pop(
            router_id,
            None,
        )

        self.set_router_content_connected(False)
        self.sync_developer_mode_for_selected_router()

        if monitor is not None:
            # Не вызываем wait(3000) и deleteLater() здесь.
            # Текущий HTTP-запрос может завершаться дольше
            # трёх секунд. Поток остаётся сохранённым до
            # настоящего сигнала QThread.finished.
            self.stopping_router_monitors[
                router_id
            ] = monitor

            # Обновления от уже отключаемого монитора больше
            # не должны возвращать рабочий интерфейс.
            try:
                monitor.updated.disconnect(
                    self.connection_completed
                )
            except (RuntimeError, TypeError):
                pass

            monitor.stop()

        for router_item in self.routers:
            if int(router_item.get("id", -1)) == router_id:
                router_item["status"] = 0
                break

        self.set_router_item_disconnected(
            router_id
        )

        self.status_label.setText(
            "● Отключено"
        )
        self.connected_address_label.setText(
            "Фактический адрес: —\n"
            "Подключение: —"
        )
        self.clear_router_state()

        self.set_router_monitor_ui_state(
            "stopping"
            if monitor is not None
            else "disconnected"
        )

        self.sync_developer_mode_for_selected_router()

        self.statusBar().showMessage(
            f"Роутер «{router['name']}» отключён",
            5000,
        )

    @staticmethod
    def _format_connection_method(
        value: object,
    ) -> str:
        text = str(
            value
            or "неизвестно"
        ).strip()

        lowered = text.lower()

        if "keen" in lowered:
            return "KeenDNS"

        if lowered in {
            "ip",
            "local",
            "local ip",
            "direct",
            "direct ip",
        }:
            return "по IP-адресу"

        return text

    @staticmethod
    def router_status_state(
        state: bool | None,
        *,
        checks_enabled: bool = True,
    ) -> str:
        if state is True:
            return "online"

        if state is False:
            return "offline"

        return (
            "checking"
            if checks_enabled
            else "unknown"
        )

    def router_reachability_changed(
        self,
        router_id: int,
        is_online: bool,
        checked_address: str,
    ) -> None:
        if not self.reachability_checkbox.isChecked():
            return

        previous = self.router_reachability.get(
            router_id
        )

        authenticated_result = self.connection_results.get(
            router_id
        )
        authenticated_online = bool(
            authenticated_result is not None
            and getattr(authenticated_result, "success", False)
        )
        previous_effective = bool(
            previous is True or authenticated_online
        )

        self.router_reachability[
            router_id
        ] = is_online

        if checked_address:
            self.router_reachable_address[
                router_id
            ] = checked_address
        else:
            self.router_reachable_address.pop(
                router_id,
                None,
            )

        self.update_router_item_status(
            router_id,
            bool(is_online or authenticated_online),
        )

        # При включённой отдельной проверке статус списка
        # обновляется независимо от подключения мониторинга.
        effective_online = bool(
            is_online or authenticated_online
        )
        if previous_effective != effective_online:
            router = self.database.get_router(
                router_id
            )

            if router is not None:
                state_text = (
                    "доступен"
                    if effective_online
                    else "недоступен"
                )
                self.statusBar().showMessage(
                    f"Роутер «{router['name']}» "
                    f"{state_text}",
                    3000,
                )

    def refresh_router_statuses(
        self,
    ) -> None:
        self.router_status_worker.set_routers(
            self.routers
        )
        self.router_status_worker.request_refresh()

    def set_router_item_disconnected(
        self,
        router_id: int,
    ) -> None:
        for index in range(
            self.router_list.count()
        ):
            item = self.router_list.item(index)
            item_router_id = item.data(
                self.ROUTER_ID_ROLE
            )

            if item_router_id != router_id:
                continue

            router = self.database.get_router(
                router_id
            )

            if router is None:
                return

            reachability = (
                self.router_reachability.get(
                    router_id
                )
            )
            item.setText(str(router["name"]))
            item.setData(
                self.ROUTER_STATUS_ROLE,
                self.router_status_state(
                    reachability,
                    checks_enabled=(
                        self.reachability_checkbox.isChecked()
                    ),
                ),
            )
            return

    def monitor_stopped(
        self,
        router_id: int,
    ) -> None:
        """
        Сигнал приходит из finally метода run().

        Здесь нельзя удалять QThread: после emit() метод run()
        ещё должен окончательно вернуться. Удаление выполняется
        только обработчиком встроенного сигнала finished.
        """
        self.disable_developer_mode_for_router(
            router_id
        )
        self.pending_rescue_modes.pop(router_id, None)

        # Самопроизвольная остановка всё ещё удаляет монитор
        # из активного списка, но объект сохраняется до finished.
        monitor = self.router_monitors.pop(
            router_id,
            None,
        )

        if monitor is not None:
            self.stopping_router_monitors[
                router_id
            ] = monitor

        current_router_id = (
            self.get_selected_router_id()
        )

        if current_router_id == router_id:
            self.connection_results.pop(
                router_id,
                None,
            )
            self.clear_router_state()
            self.status_label.setText(
                "● Не подключён"
            )
            self.connected_address_label.setText(
                "Фактический адрес: —\n"
                "Подключение: —"
            )

            self.set_router_monitor_ui_state("stopping")

    def router_monitor_finished(
        self,
        router_id: int,
        monitor: RouterMonitor,
    ) -> None:
        """
        Удаляет RouterMonitor только после полного завершения
        системного потока QThread.
        """
        active = self.router_monitors.get(
            router_id
        )

        if active is monitor:
            self.router_monitors.pop(
                router_id,
                None,
            )

        stopping = self.stopping_router_monitors.get(
            router_id
        )

        if stopping is monitor:
            self.stopping_router_monitors.pop(
                router_id,
                None,
            )

        monitor.deleteLater()

        if self.get_selected_router_id() == router_id:
            if router_id in self.router_monitors:
                self.set_router_monitor_ui_state("connecting")
            else:
                self.set_router_monitor_ui_state("disconnected")
        else:
            self._update_router_management_actions()
            self._update_global_router_actions()

    def update_router_item_status(
        self,
        router_id: int,
        is_online: bool,
    ) -> None:
        router = next(
            (
                item
                for item in self.routers
                if int(item["id"]) == router_id
            ),
            None,
        )

        if router is None:
            return

        for index in range(
            self.router_list.count()
        ):
            item = self.router_list.item(index)
            item_router_id = item.data(
                self.ROUTER_ID_ROLE
            )

            if item_router_id != router_id:
                continue

            item.setText(str(router["name"]))
            item.setData(
                self.ROUTER_STATUS_ROLE,
                self.router_status_state(is_online),
            )

            tooltip_parts = [
                (
                    "Статус: доступен"
                    if is_online
                    else "Статус: недоступен"
                )
            ]

            if router.get("local_ip"):
                tooltip_parts.append(
                    f"Локальный IP: "
                    f"{router['local_ip']}"
                )

            if router.get("keendns"):
                tooltip_parts.append(
                    f"KeenDNS: "
                    f"{router['keendns']}"
                )

            checked_address = (
                self.router_reachable_address.get(
                    router_id
                )
            )

            if checked_address:
                tooltip_parts.append(
                    f"Доступен через: "
                    f"{checked_address}"
                )

            item.setToolTip(
                "\\n".join(tooltip_parts)
            )
            return

    def _shutdown_thread_candidates(self) -> list[object]:
        candidates = [
            self.router_status_worker,
            *self.router_monitors.values(),
            *self.stopping_router_monitors.values(),
        ]
        unique: list[object] = []
        seen: set[int] = set()

        for thread in candidates:
            identity = id(thread)

            if identity in seen:
                continue

            seen.add(identity)
            unique.append(thread)

        return unique

    @staticmethod
    def _thread_is_running(thread: object) -> bool:
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            # QObject уже безопасно удалён через deleteLater().
            return False

    def _poll_shutdown_threads(self) -> None:
        if any(
            self._thread_is_running(thread)
            for thread in self._shutdown_threads
        ):
            return

        self._shutdown_poll_timer.stop()
        self._shutdown_threads = []
        self._shutdown_ready = True
        QTimer.singleShot(
            0,
            self._finish_shutdown,
        )

    def _finish_shutdown(self) -> None:
        self.close()
        application = QApplication.instance()

        if application is not None:
            application.quit()

    def closeEvent(self, event) -> None:
        if self._shutdown_ready:
            event.accept()
            return

        event.ignore()

        if self._shutdown_in_progress:
            return

        self._shutdown_in_progress = True
        self.setEnabled(False)
        self.statusBar().showMessage(
            "Завершение фоновых запросов..."
        )

        # Окно исчезает сразу, а цикл Qt остаётся активным,
        # пока сетевые потоки не выйдут самостоятельно.
        self.hide()
        self._shutdown_threads = (
            self._shutdown_thread_candidates()
        )

        for thread in self._shutdown_threads:
            try:
                thread.stop()
            except RuntimeError:
                continue

        self._shutdown_poll_timer.start()
        QTimer.singleShot(
            0,
            self._poll_shutdown_threads,
        )

    def select_router_by_id(
        self,
        router_id: int,
    ) -> None:
        for index in range(
            self.router_list.count()
        ):
            item = self.router_list.item(index)
            item_router_id = item.data(
                self.ROUTER_ID_ROLE
            )

            if item_router_id == router_id:
                self.router_list.setCurrentRow(
                    index
                )
                return
