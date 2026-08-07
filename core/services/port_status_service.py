from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.port_engine import PortEngine
from core.services.operation_result import OperationResult, OperationState


@dataclass(frozen=True)
class PortSnapshot:
    """Physical Ethernet port state used by safe mutations."""

    interface_id: str
    exists: bool
    enabled: bool | None
    connected: bool | None
    display_name: str
    management: bool
    runtime_enabled: bool | None = None
    config_enabled: bool | None = None


class PortStatusService:
    """Safely toggles physical Ethernet ports and rolls back uncertainty."""

    PHYSICAL_PORT_PATTERN = re.compile(
        r"^(?:GigabitEthernet|FastEthernet|Ethernet)\d+/\d+$",
        re.IGNORECASE,
    )

    def __init__(self, client: Any) -> None:
        self.client = client

    def snapshot(
        self,
        interface_id: str,
        *,
        runtime: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> PortSnapshot:
        name = str(interface_id or "").strip()
        interface_service = self.client.interface_service
        if runtime is None or config is None:
            raw = interface_service.read_snapshot()
            runtime = raw.runtime if runtime is None else runtime
            config = raw.config if config is None else config

        runtime_map = runtime if isinstance(runtime, dict) else {}
        config_map = config if isinstance(config, dict) else {}
        port = next(
            (
                item
                for item in PortEngine(runtime_map).build()
                if str(item.get("kind") or "").lower() == "ethernet"
                and str(
                    item.get("interface_id") or item.get("id") or ""
                ).strip().casefold()
                == name.casefold()
            ),
            None,
        )
        runtime_item = interface_service.find_interface_item(
            runtime_map,
            name,
        )
        config_item = interface_service.find_interface_item(
            config_map,
            name,
        )
        runtime_enabled = interface_service.runtime_admin_state(runtime_item)
        config_enabled = interface_service.config_admin_state(config_item)
        known = [
            value
            for value in (runtime_enabled, config_enabled)
            if value is not None
        ]
        enabled = known[0] if known and len(set(known)) == 1 else None
        exists = bool(
            port is not None
            or (
                self.PHYSICAL_PORT_PATTERN.fullmatch(name)
                and isinstance(config_item, dict)
            )
        )
        connected = (
            bool(port.get("connected"))
            if isinstance(port, dict)
            else None
        )
        display_name = (
            str(
                port.get("physical_name")
                or port.get("name")
                or name
            ).strip()
            if isinstance(port, dict)
            else name
        )
        return PortSnapshot(
            interface_id=name,
            exists=exists,
            enabled=enabled,
            connected=connected,
            display_name=display_name,
            management=self.is_management_port(name),
            runtime_enabled=runtime_enabled,
            config_enabled=config_enabled,
        )

    def is_management_port(self, interface_id: str) -> bool:
        name = str(interface_id or "").strip().casefold()
        if not name:
            return False

        network_service = getattr(self.client, "wifi_network_service", None)
        inventory = getattr(network_service, "client_inventory", ())
        management_ip = getattr(self.client, "_management_local_ip", None)
        same_ip = getattr(self.client, "same_ip_address", None)

        for item in inventory if isinstance(inventory, list) else ():
            if not isinstance(item, dict):
                continue
            is_management = bool(item.get("management_client"))
            if not is_management and management_ip and callable(same_ip):
                is_management = bool(same_ip(item.get("ip"), management_ip))
            if not is_management:
                continue
            candidate = str(item.get("interface") or "").strip()
            if candidate.casefold() == name:
                return True
        return False

    def set_enabled(
        self,
        interface_id: str,
        enabled: bool,
    ) -> tuple[bool, str]:
        return self.set_enabled_result(interface_id, enabled).as_legacy_tuple()

    def set_enabled_result(
        self,
        interface_id: str,
        enabled: bool,
    ) -> OperationResult:
        target = bool(enabled)
        initial = self.snapshot(interface_id)
        details = {
            "interface_id": initial.interface_id,
            "display_name": initial.display_name,
            "requested_enabled": target,
            "initial_enabled": initial.enabled,
            "management_port": initial.management,
        }

        if not initial.interface_id:
            return self._finish(
                OperationResult.rejected_result(
                    "Не указан физический Ethernet-порт.",
                    **details,
                )
            )
        if not initial.exists:
            return self._finish(
                OperationResult.rejected_result(
                    "Интерфейс не является подтверждённым физическим Ethernet-портом.",
                    **details,
                )
            )
        if initial.management and not target:
            return self._finish(
                OperationResult.rejected_result(
                    "Нельзя выключить порт управления: через него приложение связано с роутером.",
                    **details,
                )
            )
        if initial.enabled is None:
            return self._finish(
                OperationResult.rejected_result(
                    "Не удалось надёжно определить административное состояние порта.",
                    **details,
                )
            )
        if initial.enabled == target:
            self._remember_state(initial.interface_id, target)
            return self._finish(
                OperationResult.confirmed_result(
                    "Порт уже включён." if target else "Порт уже выключен.",
                    unchanged=True,
                    **details,
                )
            )

        mutation = self.client.interface_service.set_enabled_result(
            initial.interface_id,
            target,
        )
        if mutation.confirmed and bool(mutation.details.get("verified")):
            self._remember_state(initial.interface_id, target)
            return self._finish(
                OperationResult.confirmed_result(
                    "Ethernet-порт включён." if target else "Ethernet-порт выключен.",
                    verification=mutation.details.get("verification"),
                    **details,
                )
            )

        may_have_applied = (
            mutation.state in {OperationState.UNKNOWN, OperationState.PARTIAL}
            or bool(mutation.details.get("mutation_may_have_applied"))
            or mutation.confirmed
        )
        if not may_have_applied:
            return self._finish(
                OperationResult.rejected_result(
                    mutation.message,
                    mutation_state=mutation.state.value,
                    **details,
                )
            )

        verified, verification = self.client.interface_service.verify_admin_state(
            initial.interface_id,
            target,
        )
        if verified:
            self._remember_state(initial.interface_id, target)
            return self._finish(
                OperationResult.confirmed_result(
                    "Ethernet-порт включён." if target else "Ethernet-порт выключен.",
                    verification=verification,
                    recovered_confirmation=True,
                    **details,
                )
            )

        rollback = self.client.interface_service.set_enabled_result(
            initial.interface_id,
            initial.enabled,
        )
        rollback_verified = rollback.confirmed and bool(
            rollback.details.get("verified")
        )
        rollback_state = str(rollback.details.get("verification") or "")
        if not rollback_verified:
            rollback_verified, rollback_state = (
                self.client.interface_service.verify_admin_state(
                    initial.interface_id,
                    initial.enabled,
                )
            )

        if rollback_verified:
            self._remember_state(initial.interface_id, initial.enabled)
            return self._finish(
                OperationResult.rolled_back_result(
                    "Новое состояние порта не подтвердилось; исходное состояние восстановлено.",
                    failed_verification=verification,
                    rollback_verification=rollback_state,
                    **details,
                )
            )

        return self._finish(
            OperationResult.partial_result(
                "Состояние порта не подтверждено, автоматический откат также не подтверждён. Проверьте порт через другой способ подключения.",
                failed_verification=verification,
                rollback_state=rollback.state.value,
                rollback_message=rollback.message,
                rollback_verification=rollback_state,
                **details,
            )
        )

    def _remember_state(self, interface_id: str, enabled: bool) -> None:
        disabled = self.client._manually_disabled_interfaces
        if enabled:
            disabled.discard(interface_id)
        else:
            disabled.add(interface_id)

    def _finish(self, result: OperationResult) -> OperationResult:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_event = getattr(diagnostics, "record_event", None)
        if callable(record_event):
            try:
                record_event(
                    "ethernet_port_state",
                    result.message,
                    endpoint="interface/state",
                    details={
                        "state": result.state.value,
                        **result.details,
                    },
                )
            except Exception:
                pass
        return result
