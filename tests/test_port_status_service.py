from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from core.services.interface_service import InterfaceService, InterfaceSnapshot
from core.services.operation_result import OperationResult, OperationState
from core.services.port_status_service import PortStatusService


def physical_port(
    interface_id: str = "GigabitEthernet0/1",
    *,
    conf: str = "running",
    link: str = "up",
) -> dict[str, Any]:
    return {
        interface_id: {
            "id": interface_id,
            "index": 1,
            "label": "2",
            "type": "Port",
            "traits": ["EthernetPort", "SwitchPort"],
            "link": link,
            "summary": {"layer": {"conf": conf}},
        }
    }


class FakeDiagnostics:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def record_event(
        self,
        event_type: str,
        message: str,
        *,
        endpoint: str,
        details: dict[str, Any],
    ) -> None:
        self.events.append((event_type, message, details))


class FakeInterfaceService:
    find_interface_item = staticmethod(InterfaceService.find_interface_item)
    runtime_admin_state = staticmethod(InterfaceService.runtime_admin_state)
    config_admin_state = staticmethod(InterfaceService.config_admin_state)

    def __init__(
        self,
        runtime: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        self.runtime = runtime
        self.config = config
        self.mutations: list[tuple[str, bool]] = []
        self.results: list[OperationResult] = []
        self.verifications: list[tuple[bool, str]] = []

    def read_snapshot(self) -> InterfaceSnapshot:
        return InterfaceSnapshot(self.runtime, self.config)

    def set_enabled_result(
        self,
        interface_id: str,
        enabled: bool,
    ) -> OperationResult:
        self.mutations.append((interface_id, enabled))
        if self.results:
            return self.results.pop(0)
        return OperationResult.confirmed_result(
            "ok",
            verified=True,
            verification="runtime=confirmed",
        )

    def verify_admin_state(
        self,
        interface_id: str,
        expected_enabled: bool,
    ) -> tuple[bool, str]:
        del interface_id, expected_enabled
        if self.verifications:
            return self.verifications.pop(0)
        return False, "unknown"


class FakeClient:
    def __init__(
        self,
        runtime: dict[str, Any],
        config: dict[str, Any],
        inventory: list[dict[str, Any]] | None = None,
    ) -> None:
        self.interface_service = FakeInterfaceService(runtime, config)
        self.wifi_network_service = SimpleNamespace(
            client_inventory=list(inventory or [])
        )
        self._management_local_ip = "192.168.1.10"
        self._manually_disabled_interfaces: set[str] = set()
        self.diagnostics = FakeDiagnostics()

    @staticmethod
    def same_ip_address(left: object, right: object) -> bool:
        return str(left or "") == str(right or "")


class PortStatusServiceTests(unittest.TestCase):
    def test_kos4_link_down_is_not_treated_as_admin_disabled(self) -> None:
        interface_id = "FastEthernet0/1"
        client = FakeClient(
            physical_port(interface_id, link="down"),
            {interface_id: {"up": True}},
        )

        snapshot = PortStatusService(client).snapshot(interface_id)

        self.assertTrue(snapshot.exists)
        self.assertTrue(snapshot.enabled)
        self.assertFalse(snapshot.connected)

    def test_management_port_is_rejected_before_router_mutation(self) -> None:
        interface_id = "GigabitEthernet0/1"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
            [
                {
                    "ip": "192.168.1.10",
                    "interface": interface_id,
                    "connection_kind": "wired",
                }
            ],
        )

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.REJECTED)
        self.assertIn("порт управления", result.message.lower())
        self.assertEqual(client.interface_service.mutations, [])

    def test_confirmed_kos5_disable_is_remembered(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.CONFIRMED)
        self.assertIn(interface_id, client._manually_disabled_interfaces)
        self.assertEqual(
            client.interface_service.mutations,
            [(interface_id, False)],
        )

    def test_disabled_port_can_always_be_enabled(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id, conf="disabled", link="down"),
            {interface_id: {"up": False}},
        )
        client._manually_disabled_interfaces.add(interface_id)

        result = PortStatusService(client).set_enabled_result(interface_id, True)

        self.assertTrue(result.confirmed)
        self.assertNotIn(interface_id, client._manually_disabled_interfaces)

    def test_unconfirmed_mutation_rolls_back_original_state(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )
        client.interface_service.results = [
            OperationResult.unknown_result("connection lost"),
            OperationResult.confirmed_result(
                "restored",
                verified=True,
                verification="runtime=enabled",
            ),
        ]
        client.interface_service.verifications = [(False, "unknown")]

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.ROLLED_BACK)
        self.assertFalse(result.accepted)
        self.assertEqual(
            client.interface_service.mutations,
            [(interface_id, False), (interface_id, True)],
        )

    def test_transport_loss_can_be_confirmed_by_fresh_state(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )
        client.interface_service.results = [
            OperationResult.unknown_result("connection lost")
        ]
        client.interface_service.verifications = [
            (True, "runtime=disabled, config=disabled")
        ]

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertTrue(result.confirmed)
        self.assertTrue(result.details["recovered_confirmation"])
        self.assertIn(interface_id, client._manually_disabled_interfaces)

    def test_explicit_router_rejection_does_not_trigger_rollback(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )
        client.interface_service.results = [
            OperationResult.rejected_result("permission denied")
        ]

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.REJECTED)
        self.assertEqual(
            client.interface_service.mutations,
            [(interface_id, False)],
        )

    def test_failed_rollback_is_reported_as_partial_state(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )
        client.interface_service.results = [
            OperationResult.unknown_result("connection lost"),
            OperationResult.rejected_result("restore failed"),
        ]
        client.interface_service.verifications = [
            (False, "target unknown"),
            (False, "rollback unknown"),
        ]

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.PARTIAL)
        self.assertFalse(result.accepted)
        self.assertIn("откат", result.message)

    def test_conflicting_runtime_and_config_state_blocks_mutation(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id, conf="disabled", link="down"),
            {interface_id: {"up": True}},
        )

        result = PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(result.state, OperationState.REJECTED)
        self.assertIn("административное состояние", result.message)
        self.assertEqual(client.interface_service.mutations, [])

    def test_logical_interface_cannot_enter_physical_port_path(self) -> None:
        client = FakeClient(
            {"Bridge0": {"id": "Bridge0", "state": "up"}},
            {"Bridge0": {"up": True}},
        )

        result = PortStatusService(client).set_enabled_result("Bridge0", False)

        self.assertEqual(result.state, OperationState.REJECTED)
        self.assertEqual(client.interface_service.mutations, [])

    def test_every_operation_is_written_to_diagnostics(self) -> None:
        interface_id = "GigabitEthernet0/2"
        client = FakeClient(
            physical_port(interface_id),
            {interface_id: {"up": True}},
        )

        PortStatusService(client).set_enabled_result(interface_id, False)

        self.assertEqual(client.diagnostics.events[0][0], "ethernet_port_state")
        self.assertEqual(client.diagnostics.events[0][2]["state"], "confirmed")


if __name__ == "__main__":
    unittest.main()
