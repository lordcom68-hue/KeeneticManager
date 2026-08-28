from __future__ import annotations

from typing import Any

from core.keenetic_client import ConnectionResult, KeeneticClient
from core.services.router_self_check_service import RouterSelfCheckService


class RouterActionDispatcher:
    """Dispatch queued router actions without coupling them to the monitor loop."""

    _HANDLERS = {
        "interface_state": "_handle_interface_state",
        "interface_settings": "_handle_interface_settings",
        "create_wired_ipoe": "_handle_create_wired_ipoe",
        "create_cable_internet": "_handle_create_cable_internet",
        "scan_wireless_isp": "_handle_scan_wireless_isp",
        "create_wireless_isp": "_handle_create_wireless_isp",
        "delete_wireless_isp": "_handle_delete_wireless_isp",
        "create_wifi_network": "_handle_create_wifi_network",
        "update_wifi_network": "_handle_update_wifi_network",
        "set_wifi_network_enabled": "_handle_set_wifi_network_enabled",
        "rescue_mode": "_handle_rescue_mode",
        "delete_wifi_network": "_handle_delete_wifi_network",
        "wifi_client_access": "_handle_wifi_client_access",
        "change_cable_protocol": "_handle_change_cable_protocol",
        "delete_dedicated_wan": "_handle_delete_dedicated_wan",
        "delete_wired_ipoe": "_handle_delete_wired_ipoe",
        "delete_wired_pppoe": "_handle_delete_wired_pppoe",
        "delete_wired_pptp": "_handle_delete_wired_pptp",
        "delete_wired_l2tp": "_handle_delete_wired_l2tp",
        "interface_reconnect": "_handle_interface_reconnect",
        "internet_priorities": "_handle_internet_priorities",
        "diagnostic_archive": "_handle_diagnostic_archive",
        "system_log": "_handle_system_log",
        "router_self_check": "_handle_router_self_check",
        "clear_diagnostics": "_handle_clear_diagnostics",
    }

    def __init__(self, monitor: Any) -> None:
        self._monitor = monitor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._monitor, name)

    def dispatch(
        self,
        *,
        client: KeeneticClient,
        action_type: str,
        payload: object,
    ) -> bool:
        handler_name = self._HANDLERS.get(action_type)
        if handler_name is None:
            raise ValueError(f"Неизвестное действие: {action_type}")

        handler = getattr(self, handler_name)
        return bool(handler(client=client, payload=payload))

    def _handle_interface_state(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id, enabled = payload
        success, message = (
            client.set_interface_enabled(
                interface_id=str(interface_id),
                enabled=bool(enabled),
            )
        )
        self._record_operation(
            client,
            "interface_state",
            message,
            success=success,
            details={
                "interface_id": str(interface_id),
                "display_name": str(interface_id),
                "requested_enabled": bool(enabled),
            },
        )
        self.interface_action_completed.emit(
            self.router_id,
            str(interface_id),
            success,
            message,
        )
        return True

    def _handle_interface_settings(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id, settings = payload
        success, message = (
            client.set_interface_settings(
                interface_id=str(interface_id),
                settings=dict(settings),
            )
        )
        self.interface_settings_completed.emit(
            self.router_id,
            str(interface_id),
            success,
            message,
        )
        return True

    def _handle_create_wired_ipoe(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        success, message, details = (
            client.wired_internet_service.create_wired_ipoe(
                dict(payload)
            )
        )
        self.connection_creation_completed.emit(
            self.router_id,
            "ethernet.ipoe",
            success,
            message,
            details,
        )
        return True

    def _handle_create_cable_internet(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        settings = dict(payload)
        protocol = str(
            settings.get("protocol", {}).get("type")
            if isinstance(settings.get("protocol"), dict)
            else "none"
        ).strip().lower()
        success, message, details = (
            client.wired_internet_service.create_cable_internet(settings)
        )
        dedicated_wan = bool(
            isinstance(details, dict)
            and details.get("dedicated_wan")
        ) or str(settings.get("mode") or "") == "dedicated_wan"
        event_type = (
            "dedicated_wan"
            if dedicated_wan
            else "wired_connection"
        )
        event_details = {
            "operation": "create",
            "display_name": self._safe_display_name(
                details,
                settings,
            ),
            "protocol": protocol,
            "mode": str(settings.get("mode") or ""),
            "interface_id": str(
                details.get("interface_id") or ""
            ) if isinstance(details, dict) else "",
            "transport_interface_id": str(
                details.get("transport_interface_id") or ""
            ) if isinstance(details, dict) else "",
        }
        self._record_operation(
            client,
            event_type,
            message,
            success=success,
            details=event_details,
            result_details=details,
        )
        self._record_diagnostic_event(
            client,
            event_type,
            message,
            endpoint="internet/cable/create",
            success=success,
            details=event_details,
            result_details=details,
        )
        connection_type = {
            "pppoe": "ethernet.pppoe",
            "pptp": "ethernet.pptp",
            "l2tp": "ethernet.l2tp",
        }.get(protocol, "ethernet.ipoe")
        self.connection_creation_completed.emit(
            self.router_id,
            connection_type,
            success,
            message,
            details,
        )
        return True

    def _handle_scan_wireless_isp(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        band = str(payload)
        success, message, networks = (
            client.wireless_isp_service.scan_wireless_isp(band)
        )
        self.wireless_scan_completed.emit(
            self.router_id,
            band,
            success,
            message,
            networks,
        )
        return False

    def _handle_create_wireless_isp(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        success, message, details = (
            client.wireless_isp_service.create_wireless_isp(
                dict(payload)
            )
        )
        self.connection_creation_completed.emit(
            self.router_id,
            "wireless.isp",
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wireless_isp(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wireless_isp_service.delete_wireless_isp(
                interface_id=interface_id,
            )
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_create_wifi_network(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        success, message, details = (
            client.wifi_network_service.create_network(
                dict(payload)
            )
        )
        self._record_operation(
            client,
            "wifi_network",
            message,
            success=success,
            details={
                "operation": "create",
                "display_name": self._safe_display_name(
                    details,
                    payload,
                ),
            },
            result_details=details,
        )
        self.wifi_network_action_completed.emit(
            self.router_id,
            "create",
            success,
            message,
            details,
        )
        return True

    def _handle_update_wifi_network(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_ids, settings = payload
        success, message, details = (
            client.wifi_network_service.update_network(
                list(interface_ids),
                dict(settings),
            )
        )
        self._record_operation(
            client,
            "wifi_network",
            message,
            success=success,
            details={
                "operation": "update",
                "display_name": self._safe_display_name(
                    details,
                    settings,
                ),
            },
            result_details=details,
        )
        self.wifi_network_action_completed.emit(
            self.router_id,
            "update",
            success,
            message,
            details,
        )
        return True

    def _handle_set_wifi_network_enabled(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        network_reference, enabled = payload
        success, message, details = (
            client.wifi_network_service.set_network_enabled(
                network_reference,
                bool(enabled),
            )
        )
        self._record_operation(
            client,
            "wifi_network",
            message,
            success=success,
            details={
                "operation": "state",
                "display_name": self._safe_display_name(
                    details,
                    network_reference,
                ),
                "requested_enabled": bool(enabled),
            },
            result_details=details,
        )
        self.wifi_network_action_completed.emit(
            self.router_id,
            "state",
            success,
            message,
            details,
        )
        return True

    def _handle_rescue_mode(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        mode, context = payload
        success, message, details = (
            client.rescue_mode_service.apply(
                str(mode),
                dict(context),
            )
        )
        self._record_operation(
            client,
            "rescue_mode",
            message,
            success=success,
            details={
                "operation": str(mode),
                "display_name": {
                    "rescuer": "Спасатель",
                    "rescued": "Спасаемый",
                }.get(str(mode), str(mode)),
            },
            result_details=details,
        )
        self.rescue_mode_action_completed.emit(
            self.router_id,
            str(mode),
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wifi_network(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        success, message, details = (
            client.wifi_network_service.delete_network(payload)
        )
        self._record_operation(
            client,
            "wifi_network",
            message,
            success=success,
            details={
                "operation": "delete",
                "display_name": self._safe_display_name(
                    details,
                    payload,
                ),
            },
            result_details=details,
        )
        self.wifi_network_action_completed.emit(
            self.router_id,
            "delete",
            success,
            message,
            details,
        )
        return True

    def _handle_wifi_client_access(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        client_reference, allowed = payload
        success, message, details = (
            client.wifi_client_service.set_access(
                dict(client_reference),
                allowed=bool(allowed),
            )
        )
        self._record_operation(
            client,
            "wifi_client_access",
            message,
            success=success,
            details={
                "display_name": self._safe_display_name(
                    details,
                    client_reference,
                ),
                "allowed": bool(allowed),
            },
            result_details=details,
        )
        self.wifi_client_access_completed.emit(
            self.router_id,
            str(client_reference.get("mac") or ""),
            success,
            message,
            details,
        )
        return True

    def _handle_change_cable_protocol(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id, settings = payload
        success, message, details = (
            client.wired_internet_service.change_cable_internet_protocol(
                interface_id=str(interface_id),
                settings=dict(settings),
            )
        )
        event_details = {
            "operation": "change_protocol",
            "display_name": str(interface_id),
            "source_interface_id": str(interface_id),
            "target_interface_id": str(
                details.get("interface_id") or ""
            ) if isinstance(details, dict) else "",
            "old_protocol": str(
                details.get("old_protocol") or ""
            ) if isinstance(details, dict) else "",
            "target_protocol": str(
                settings.get("target_protocol") or "none"
            ).strip().lower(),
            "rollback_success": bool(
                details.get("rollback_success")
            ) if isinstance(details, dict) else False,
            "partial_success": bool(
                details.get("partial_success")
            ) if isinstance(details, dict) else False,
            "transient_recovered": bool(
                details.get("transient_recovered")
            ) if isinstance(details, dict) else False,
        }
        self._record_operation(
            client,
            "cable_protocol_change",
            message,
            success=success,
            details=event_details,
            result_details=details,
        )
        self._record_diagnostic_event(
            client,
            "cable_protocol_change",
            message,
            endpoint="internet/cable/protocol",
            success=success,
            details=event_details,
            result_details=details,
        )
        self.connection_protocol_change_completed.emit(
            self.router_id,
            str(interface_id),
            success,
            message,
            details,
        )
        return True

    def _handle_delete_dedicated_wan(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wired_internet_service.delete_dedicated_wan(
                interface_id=interface_id,
            )
        )
        event_details = {
            "operation": "delete",
            "display_name": self._safe_display_name(
                details,
                interface_id,
            ),
            "interface_id": interface_id,
            "transport_interface_id": str(
                details.get("transport_interface_id") or ""
            ) if isinstance(details, dict) else "",
            "bridge_id": str(details.get("bridge_id") or "")
            if isinstance(details, dict)
            else "",
            "rollback_success": bool(
                details.get("rollback_success")
            ) if isinstance(details, dict) else False,
        }
        self._record_operation(
            client,
            "dedicated_wan",
            message,
            success=success,
            details=event_details,
            result_details=details,
        )
        self._record_diagnostic_event(
            client,
            "dedicated_wan",
            message,
            endpoint="internet/dedicated-wan/delete",
            success=success,
            details=event_details,
            result_details=details,
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wired_ipoe(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wired_internet_service.delete_wired_ipoe(
                interface_id=interface_id,
            )
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wired_pppoe(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wired_internet_service.delete_wired_pppoe(
                interface_id=interface_id,
            )
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wired_pptp(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wired_internet_service.delete_wired_pptp(
                interface_id=interface_id,
            )
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_delete_wired_l2tp(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message, details = (
            client.wired_internet_service.delete_wired_l2tp(
                interface_id=interface_id,
            )
        )
        self.connection_delete_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
            details,
        )
        return True

    def _handle_interface_reconnect(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        interface_id = str(payload)
        success, message = (
            client.reconnect_interface(
                interface_id=interface_id,
            )
        )
        self.interface_reconnect_completed.emit(
            self.router_id,
            interface_id,
            success,
            message,
        )
        return True

    def _handle_internet_priorities(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        priorities = dict(payload)
        success, message = (
            client.priority_service.set_internet_priorities(
                priorities
            )
        )
        self.priorities_action_completed.emit(
            self.router_id,
            success,
            message,
            priorities,
        )
        return True

    def _handle_diagnostic_archive(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        diagnostic_payload = (
            dict(payload)
            if isinstance(payload, dict)
            else {"destination": str(payload)}
        )
        success, message = (
            client.create_diagnostic_archive(
                str(
                    diagnostic_payload.get(
                        "destination",
                        "",
                    )
                ),
                application_settings=dict(
                    diagnostic_payload.get(
                        "application_settings",
                        {},
                    )
                    or {}
                ),
            )
        )
        self.diagnostic_action_completed.emit(
            self.router_id,
            "create",
            success,
            message,
        )
        return False

    def _handle_system_log(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        success, message, entries = (
            client.system_log_service.read(int(payload))
        )
        self.system_log_completed.emit(
            self.router_id,
            success,
            message,
            entries,
        )
        return False

    def _handle_router_self_check(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        router = self.router_snapshot()
        service = RouterSelfCheckService(
            client,
            router_id=self.router_id,
        )
        result = service.run(
            local_ip=router.get("local_ip"),
            keendns=router.get("keendns"),
            progress=lambda value, message: (
                self.router_self_check_progress.emit(
                    self.router_id,
                    int(value),
                    str(message),
                )
            ),
            cancelled=lambda: not self.is_running(),
            check_keys=(
                set(payload)
                if isinstance(payload, (tuple, list, set))
                else None
            ),
        )
        snapshot = getattr(service, "last_refresh_result", None)
        if isinstance(snapshot, ConnectionResult):
            snapshot = self.apply_reachability_state(snapshot, client)
            self.updated.emit(self.router_id, snapshot)
        self.router_self_check_completed.emit(
            self.router_id,
            True,
            result.overall_label,
            result,
        )
        return False

    def _handle_clear_diagnostics(
        self,
        client: KeeneticClient,
        payload: object,
    ) -> bool:
        client.clear_diagnostics()
        self.diagnostic_action_completed.emit(
            self.router_id,
            "clear",
            True,
            "Диагностические данные очищены.",
        )
        return False
