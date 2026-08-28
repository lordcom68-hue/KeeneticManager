from __future__ import annotations

import re
import time
from typing import Any


class CableProtocolLifecycleService:
    """Анализирует, применяет, проверяет и откатывает смену кабельного протокола."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def inspect_cable_protocol_change(
        cls,
        *,
        interface_id: str,
        interface_config: dict[str, Any],
        interface_runtime: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        name = str(interface_id or "").strip()
        item = interface_config.get(name)
    
        if not isinstance(item, dict):
            return {}, "Конфигурация подключения не найдена."
    
        current_protocol = cls.cable_protocol_from_config(
            name,
            item,
        )
    
        if current_protocol == "none":
            transport_id = name
        else:
            connect = item.get("connect")
            transport_id = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )
    
        if not transport_id:
            return {}, "Не удалось определить транспорт подключения."
    
        if not cls.is_wired_transport_interface(transport_id):
            return {}, "Смена типа доступна только для проводного WAN."
    
        transport_item = interface_config.get(transport_id)
    
        if not isinstance(transport_item, dict):
            return {}, "Конфигурация проводного транспорта не найдена."
    
        source_ip = item.get("ip")
        source_global = (
            source_ip.get("global")
            if isinstance(source_ip, dict)
            else None
        )
    
        if not isinstance(source_global, dict):
            return {}, "Подключение не является активным интернет-каналом."
    
        references: list[str] = []
    
        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue
    
            candidate_protocol = cls.cable_protocol_from_config(
                str(candidate_id),
                candidate,
            )
    
            if candidate_protocol == "none":
                continue
    
            connect = candidate.get("connect")
            via = (
                str(connect.get("via") or "").strip()
                if isinstance(connect, dict)
                else ""
            )
    
            if via == transport_id:
                references.append(str(candidate_id))
    
        if current_protocol == "none":
            if references:
                return (
                    {},
                    "Транспорт уже используется протоколом "
                    + ", ".join(references)
                    + ".",
                )
        elif any(reference != name for reference in references):
            return (
                {},
                "Транспорт используется несколькими протоколами; "
                "автоматическая смена типа заблокирована.",
            )
    
        try:
            global_order = int(source_global.get("order"))
        except (TypeError, ValueError):
            return {}, "Не удалось определить приоритет подключения."
    
        port_id = ""
        port_label = ""
        port_link_known = False
        port_link_active = False
        port_link = "unknown"
    
        bindings: list[tuple[str, dict[str, Any]]] = []
        for candidate_id, candidate in interface_config.items():
            if not isinstance(candidate, dict):
                continue
            for role in cls.extract_port_roles(candidate):
                if (
                    str(role.get("role") or "").strip().lower() == "inet"
                    and str(role.get("ifor") or "").strip() == transport_id
                ):
                    bindings.append((str(candidate_id), candidate))
                    break
    
        if len(bindings) == 1:
            port_id, port_item = bindings[0]
            port_label = str(port_item.get("rename") or "").strip()
            parent_id = port_id.rsplit("/", 1)[0] if "/" in port_id else ""
    
            if not port_label and "/" in port_id:
                suffix = port_id.rsplit("/", 1)[1]
                if suffix.isdigit():
                    port_label = str(int(suffix) + 1)
    
            runtime_state = cls.runtime_ethernet_port_state(
                interface_runtime=(
                    interface_runtime
                    if isinstance(interface_runtime, dict)
                    else {}
                ),
                port_id=port_id,
                parent_id=parent_id,
            )
            port_link_known = bool(runtime_state.get("known"))
            port_link_active = bool(runtime_state.get("active"))
            port_link = str(runtime_state.get("link") or "unknown")
        else:
            runtime_item = (
                interface_runtime.get(transport_id)
                if isinstance(interface_runtime, dict)
                else None
            )
            if isinstance(runtime_item, dict):
                link = str(runtime_item.get("link") or "").strip().lower()
                connected = str(
                    runtime_item.get("connected") or ""
                ).strip().lower()
                state = str(runtime_item.get("state") or "").strip().lower()
                port_link_known = (
                    link in {"up", "down"}
                    or connected in {"yes", "no", "true", "false", "1", "0"}
                    or state in {"up", "down", "connected", "disconnected"}
                )
                port_link_active = (
                    link == "up"
                    or connected in {"yes", "true", "1"}
                    or state in {"up", "connected"}
                )
                port_link = link or state or "unknown"
    
            if re.fullmatch(
                r"(?:GigabitEthernet|FastEthernet|Ethernet)\d+",
                transport_id,
                flags=re.IGNORECASE,
            ):
                port_label = "WAN"
    
        return (
            {
                "interface_id": name,
                "current_protocol": current_protocol,
                "transport_id": transport_id,
                "global_order": global_order,
                "port_id": port_id,
                "port_label": port_label,
                "port_link_known": port_link_known,
                "port_link_active": port_link_active,
                "port_link": port_link,
            },
            "",
        )
    
    def create_protocol_on_existing_transport(
        self,
        *,
        target_protocol: str,
        target_id: str,
        transport_id: str,
        description: str,
        username: str,
        password: str,
        service: str,
        server: str,
    ) -> tuple[bool, str]:
        target_arg = self.client.quote_cli_argument(target_id)
        transport_arg = self.client.quote_cli_argument(transport_id)
        commands: list[dict[str, Any]] = [
            {"parse": f"interface {target_arg}"},
            {"parse": f"interface {target_arg} no up"},
            {
                "parse": (
                    f"interface {target_arg} description "
                    + self.client.quote_cli_argument(description)
                )
            },
            {
                "parse": (
                    f"interface {target_arg} security-level public"
                )
            },
            {"parse": f"interface {target_arg} role inet"},
            {
                "parse": (
                    f"interface {target_arg} authentication identity "
                    + self.client.quote_cli_argument(username)
                )
            },
            {
                "parse": (
                    f"interface {target_arg} authentication password "
                    + self.client.quote_cli_argument(password)
                )
            },
            {"parse": f"interface {target_arg} no ccp"},
            {"parse": f"interface {target_arg} lcp echo 30 3"},
            {"parse": f"interface {target_arg} ipcp address"},
            {"parse": f"interface {target_arg} ipcp default-route"},
            {"parse": f"interface {target_arg} ipcp dns-routes"},
            {"parse": f"interface {target_arg} ipv6cp"},
            {
                "parse": (
                    f"interface {target_arg} connect via {transport_arg}"
                )
            },
            {
                "parse": (
                    f"interface {target_arg} ip tcp adjust-mss pmtu"
                )
            },
        ]
    
        if target_protocol == "pppoe" and service:
            commands.append(
                {
                    "parse": (
                        f"interface {target_arg} pppoe service "
                        + self.client.quote_cli_argument(service)
                    )
                }
            )
        elif target_protocol in {"pptp", "l2tp"}:
            commands.append(
                {
                    "parse": (
                        f"interface {target_arg} peer "
                        + self.client.quote_cli_argument(server)
                    )
                }
            )
    
        commands.append({"system": {"configuration": {"save": {}}}})
        success, message = self.client.execute_rci_batch(
            commands,
            operation_name=(
                f"Не удалось создать {target_protocol.upper()}"
            ),
        )
        return success, self.client.redact_secrets_from_text(message, password)
    
    def rollback_cable_protocol_change(
        self,
        *,
        source_id: str,
        target_id: str,
        target_protocol: str,
        transport_id: str,
        old_priorities: dict[str, int],
        ping_profile: str,
        transport_restore_settings: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        errors: list[str] = []
        source_arg = self.client.quote_cli_argument(source_id)
        target_arg = self.client.quote_cli_argument(target_id)
    
        if ping_profile:
            self.client.execute_parse_command(
                f"interface {target_arg} no ping-check profile"
            )
    
        if target_protocol != "none":
            success, message = self.client.execute_parse_command(
                f"no interface {target_arg}"
            )
            if not success:
                errors.append("не удалось удалить новый протокол: " + message)
        else:
            success, message = self.client.set_interface_global_enabled(
                target_id,
                False,
            )
            if not success:
                errors.append("не удалось отключить временный IPoE: " + message)
    
            if transport_restore_settings:
                success, message = self.client.set_interface_settings(
                    interface_id=transport_id,
                    settings=dict(transport_restore_settings),
                )
                if not success:
                    errors.append("не удалось восстановить транспорт: " + message)
    
        if ping_profile:
            success, message = self.client.execute_parse_command(
                f"interface {source_arg} ping-check profile "
                + self.client.quote_cli_argument(ping_profile)
            )
            if not success:
                errors.append("не удалось восстановить Ping Check: " + message)
    
        if old_priorities:
            success, message = self.client.set_internet_priorities(old_priorities)
            if not success:
                errors.append("не удалось восстановить приоритеты: " + message)
    
        self.client.execute_parse_command(f"interface {source_arg} up")
        self.client.execute_parse_command("system configuration save")
    
        if errors:
            return False, "; ".join(errors)
        return True, "Исходный тип подключения восстановлен."
    
    def renew_authenticated_rci_session(
        self,
    ) -> tuple[bool, str]:
        """Пересоздаёт авторизованную сессию через RciTransport."""
        if not self.client.base_url:
            return False, "Адрес подключённого роутера неизвестен."
    
        if self.client.transport.reauthenticate():
            return True, "RCI-сессия восстановлена."
    
        return (
            False,
            self.client.transport.last_error
            or "Не удалось восстановить RCI-сессию.",
        )
    
    def verify_changed_cable_protocol(
        self,
        *,
        source_id: str,
        target_id: str,
        target_protocol: str,
        transport_id: str,
        expected_order: int,
        expected_username: str,
        expected_service: str,
        expected_server: str,
        expected_mtu: int,
        expected_ping_profile: str,
    ) -> tuple[bool, str, bool]:
        """
        Подтверждает результат смены протокола с повторной авторизацией.
    
        Возвращает третий флаг, если для подтверждения понадобилось
        восстановить HTTP/RCI-сессию после временного обрыва.
        """
        mismatches: list[str] = []
        session_recovered = False
        recovery_messages: list[str] = []
        empty_reads = 0
    
        # После save/up роутеру требуется время на перестройку L3 и RCI.
        time.sleep(1.0)
    
        for attempt in range(18):
            if attempt:
                time.sleep(min(0.55 + attempt * 0.05, 1.0))
    
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
    
            if not isinstance(config, dict) or not config:
                empty_reads += 1
    
                # Не переавторизуемся после каждого пустого ответа.
                # Двух подряд достаточно, чтобы считать старое
                # keep-alive соединение закрытым роутером.
                if empty_reads >= 2 and attempt in {1, 4, 8, 12, 15}:
                    recovered, recovery_message = (
                        self.renew_authenticated_rci_session()
                    )
                    recovery_messages.append(recovery_message)
                    if recovered:
                        session_recovered = True
                        empty_reads = 0
                continue
    
            empty_reads = 0
            mismatches = []
            item = config.get(target_id)
    
            if source_id != target_id:
                source_item = config.get(source_id)
                source_ip = (
                    source_item.get("ip")
                    if isinstance(source_item, dict)
                    else None
                )
                source_global = (
                    source_ip.get("global")
                    if isinstance(source_ip, dict)
                    else None
                )
                if isinstance(source_global, dict):
                    mismatches.append(
                        "прежний интерфейс остался интернет-каналом"
                    )
    
            if not isinstance(item, dict):
                mismatches.append("целевой интерфейс не найден")
            else:
                actual_protocol = self.client.cable_protocol_from_config(
                    target_id,
                    item,
                )
                if actual_protocol != target_protocol:
                    mismatches.append("тип протокола не совпадает")
    
                if target_protocol != "none":
                    connect = item.get("connect")
                    via = (
                        str(connect.get("via") or "").strip()
                        if isinstance(connect, dict)
                        else ""
                    )
                    if via != transport_id:
                        mismatches.append("неверный транспорт")
    
                    auth = item.get("authentication")
                    identity = (
                        str(auth.get("identity") or "").strip()
                        if isinstance(auth, dict)
                        else ""
                    )
                    if identity != expected_username:
                        mismatches.append("логин не сохранён")
                    if not (
                        isinstance(auth, dict)
                        and auth.get("password") is not None
                    ):
                        mismatches.append("пароль не сохранён")
    
                    if target_protocol == "pppoe":
                        pppoe = item.get("pppoe")
                        service = (
                            str(pppoe.get("service") or "").strip()
                            if isinstance(pppoe, dict)
                            else ""
                        )
                        if service != expected_service:
                            mismatches.append("сервис PPPoE не совпадает")
                    else:
                        peer = str(item.get("peer") or "").strip()
                        if peer != expected_server:
                            mismatches.append("сервер провайдера не совпадает")
    
                ip_config = item.get("ip")
                global_config = (
                    ip_config.get("global")
                    if isinstance(ip_config, dict)
                    else None
                )
                try:
                    actual_order = int(global_config.get("order"))
                except (TypeError, ValueError, AttributeError):
                    actual_order = None
                if actual_order != expected_order:
                    mismatches.append("приоритет не перенесён")
    
                try:
                    actual_mtu = int(ip_config.get("mtu"))
                except (TypeError, ValueError, AttributeError):
                    actual_mtu = None
                if actual_mtu != expected_mtu:
                    mismatches.append(
                        f"MTU: ожидалось {expected_mtu}, получено {actual_mtu}"
                    )
    
                if expected_ping_profile:
                    actual_profile = self.client.assigned_ping_profile(item)
                    if actual_profile != expected_ping_profile:
                        mismatches.append("Ping Check не перенесён")
    
            if not mismatches:
                message = "Новый протокол подтверждён."
                if session_recovered:
                    message = (
                        "Новый протокол подтверждён после "
                        "восстановления RCI-сессии."
                    )
                return True, message, session_recovered
    
        suffix = ""
        if recovery_messages:
            last_recovery = next(
                (
                    value
                    for value in reversed(recovery_messages)
                    if value
                ),
                "",
            )
            if last_recovery and not session_recovered:
                suffix = f" Последняя попытка восстановления: {last_recovery}"
    
        return (
            False,
            "Проверка нового протокола не пройдена: "
            + "; ".join(mismatches or ["нет актуальных данных"])
            + suffix,
            session_recovered,
        )
    
    def verify_old_protocol_removed_after_transient_failure(
        self,
        *,
        source_id: str,
        target_id: str,
    ) -> tuple[bool, str, bool]:
        """Проверяет, успел ли роутер удалить старый интерфейс до обрыва."""
        session_recovered = False
        empty_reads = 0
    
        for attempt in range(10):
            if attempt:
                time.sleep(min(0.55 + attempt * 0.08, 1.0))
    
            config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
    
            if not isinstance(config, dict) or not config:
                empty_reads += 1
                if empty_reads >= 2 and attempt in {1, 4, 7}:
                    recovered, _message = (
                        self.renew_authenticated_rci_session()
                    )
                    if recovered:
                        session_recovered = True
                        empty_reads = 0
                continue
    
            target_exists = isinstance(config.get(target_id), dict)
            source_exists = isinstance(config.get(source_id), dict)
    
            if target_exists and not source_exists:
                return (
                    True,
                    "Прежний интерфейс удалён; результат подтверждён.",
                    session_recovered,
                )
    
        return (
            False,
            "Не удалось подтвердить удаление прежнего интерфейса.",
            session_recovered,
        )
