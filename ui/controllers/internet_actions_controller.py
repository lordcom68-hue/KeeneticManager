from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from ui.dialogs.wired_ipoe_dialog import (
    CableInternetCreationDialog,
    CableProtocolChangeDialog,
)
from ui.dialogs.wireless_isp_dialog import WirelessIspCreationDialog


class InternetActionsController:
    """Own dialogs and commands for creating, changing, and deleting Internet links."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def __getattr__(self, name: str) -> Any:
        return getattr(self._window, name)

    def open_connection_creation(
        self,
        connection_type: str,
        connection_title: str,
    ) -> None:
        type_id = str(connection_type or "").strip()
        title = str(connection_title or "").strip()

        if not type_id or not title:
            QMessageBox.warning(
                self._window,
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
                self._window,
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
                parent=self._window,
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

            confirmation = QMessageBox(self._window)
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
            message_box = QMessageBox(self._window)
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
            parent=self._window,
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

        confirmation = QMessageBox(self._window)
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
                self._window,
                "Роутер не подключён",
                "Сначала подключитесь к роутеру.",
            )
            return

        if not details.get("protocol_change_supported"):
            self.internet_tab.finish_protocol_change(
                interface_id
            )
            QMessageBox.warning(
                self._window,
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
                self._window,
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

        confirmation = QMessageBox(self._window)
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
                self._window,
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
                self._window,
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
                self._window,
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

            message_box = QMessageBox(self._window)
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

        message_box = QMessageBox(self._window)
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
                self._window,
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

        message_box = QMessageBox(self._window)
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
