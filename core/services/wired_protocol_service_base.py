from __future__ import annotations

from typing import Any


class WiredProtocolServiceBase:
    """Общие безопасные операции проводных протоколов."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _deletion_priorities(
        self,
        interface_config: dict[str, Any],
        interface_id: str,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        old_priorities = (
            self.client.priority_service.current_global_orders(
                interface_config
            )
        )
        ordered_remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
            if current_id != interface_id
        ]
        remaining_priorities = {
            current_id: order
            for order, current_id in enumerate(ordered_remaining)
        }
        pre_delete_priorities = dict(remaining_priorities)
        pre_delete_priorities[interface_id] = len(remaining_priorities)
        return (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        )

    def _forget_interfaces(self, *interface_ids: str) -> None:
        for interface_id in interface_ids:
            self.client._manually_disabled_interfaces.discard(interface_id)
            self.client.traffic_service.reset(interface_id)
            self.client.ping_check_service.runtime_cache.pop(
                interface_id,
                None,
            )

