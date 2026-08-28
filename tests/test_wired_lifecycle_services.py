from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.keenetic_client import KeeneticClient
from core.services.wired_ipoe_lifecycle_service import (
    WiredIpoeLifecycleService,
)
from core.services.wired_tunnel_lifecycle_service import (
    WiredTunnelLifecycleService,
)


IPOE_METHODS = (
    "verify_wired_ipoe_skeleton",
    "verify_created_wired_ipoe",
    "rollback_wired_ipoe_creation",
    "verify_wired_ipoe_delete_preparation",
    "rollback_wired_ipoe_deletion",
    "verify_deleted_wired_ipoe",
)

TUNNEL_METHODS = (
    "rollback_wired_pppoe_creation",
    "rollback_wired_pptp_creation",
    "rollback_wired_l2tp_creation",
    "verify_created_wired_pppoe",
    "verify_created_wired_pptp",
    "verify_created_wired_l2tp",
    "verify_wired_pppoe_delete_preparation",
    "verify_wired_pptp_delete_preparation",
    "verify_wired_l2tp_delete_preparation",
    "rollback_wired_pppoe_deletion",
    "rollback_wired_pptp_deletion",
    "rollback_wired_l2tp_deletion",
    "verify_deleted_wired_pppoe",
    "verify_deleted_wired_pptp",
    "verify_deleted_wired_l2tp",
)


class WiredLifecycleServiceTests(unittest.TestCase):
    def make_client(self) -> KeeneticClient:
        return KeeneticClient("admin", "secret")

    def test_ipoe_skeleton_is_verified_from_saved_configuration(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={
                "ISP2": {"up": False},
                "GigabitEthernet0/1": {"synthetic": True},
            }
        )
        client.extract_port_access_vid = MagicMock(return_value=101)
        client.extract_port_roles = MagicMock(
            return_value=[{"role": "inet", "ifor": "ISP2"}]
        )

        with patch(
            "core.services.wired_ipoe_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_wired_ipoe_skeleton(
                interface_id="ISP2",
                port_id="GigabitEthernet0/1",
                vlan_id=101,
            )

        self.assertTrue(success, message)
        client.get_rci_data.assert_called_once_with(
            endpoint="show/sc/interface",
            required=False,
        )

    def test_deleted_pppoe_is_verified_with_port_and_priorities(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={
                "GigabitEthernet0/1": {"synthetic": True},
                "ISP": {"ip": {"global": {"order": 0}}},
            }
        )
        client.extract_port_access_vid = MagicMock(return_value=1)
        client.extract_port_roles = MagicMock(return_value=[])
        client.current_global_orders = MagicMock(return_value={"ISP": 0})

        with patch(
            "core.services.wired_tunnel_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_deleted_wired_pppoe(
                pppoe_id="PPPoE0",
                transport_id="GigabitEthernet0/Vlan101",
                port_id="GigabitEthernet0/1",
                home_vlan_id=1,
                remaining_priorities={"ISP": 0},
            )

        self.assertTrue(success, message)

    def test_created_ipoe_and_final_deletion_are_verified(self) -> None:
        client = self.make_client()
        client.extract_port_access_vid = MagicMock(return_value=101)
        client.extract_port_roles = MagicMock(return_value=[])
        created_config = {
            "ISP2": {
                "up": True,
                "ip": {"global": {"order": 1}},
            },
            "GigabitEthernet0/1": {"synthetic": True},
        }
        client.get_rci_data = MagicMock(return_value=created_config)

        with patch(
            "core.services.wired_ipoe_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_created_wired_ipoe(
                interface_id="ISP2",
                port_id="GigabitEthernet0/1",
                vlan_id=101,
                expected_order=1,
            )
        self.assertTrue(success, message)

        client.extract_port_access_vid.return_value = 1
        client.get_rci_data.return_value = {
            "GigabitEthernet0/1": {"synthetic": True},
            "ISP": {"ip": {"global": {"order": 0}}},
        }
        with patch(
            "core.services.wired_ipoe_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_deleted_wired_ipoe(
                interface_id="ISP2",
                port_id="GigabitEthernet0/1",
                home_vlan_id=1,
                remaining_priorities={"ISP": 0},
            )
        self.assertTrue(success, message)

    def test_created_tunnel_protocols_are_verified(self) -> None:
        client = self.make_client()
        transport_id = "GigabitEthernet0/Vlan101"
        port_id = "GigabitEthernet0/1"
        client.extract_port_access_vid = MagicMock(return_value=101)
        client.extract_port_roles = MagicMock(
            return_value=[{"role": "inet", "ifor": transport_id}]
        )
        config = {
            transport_id: {"ip": {}},
            port_id: {"synthetic": True},
            "PPPoE0": {
                "connect": {"via": transport_id},
                "authentication": {
                    "identity": "user",
                    "password": "stored",
                },
                "pppoe": {"service": "provider"},
                "ip": {"mtu": 1492, "global": {"order": 1}},
                "up": True,
            },
            "PPTP0": {
                "connect": {"via": transport_id},
                "authentication": {
                    "identity": "user",
                    "password": "stored",
                },
                "peer": "vpn.example",
                "ip": {"mtu": 1400, "global": {"order": 2}},
                "up": True,
            },
            "L2TP0": {
                "connect": {"via": transport_id},
                "authentication": {
                    "identity": "user",
                    "password": "stored",
                },
                "peer": "vpn.example",
                "ip": {"mtu": 1400, "global": {"order": 3}},
                "up": True,
            },
        }
        client.get_rci_data = MagicMock(return_value=config)

        with patch(
            "core.services.wired_tunnel_lifecycle_service.time.sleep",
            return_value=None,
        ):
            pppoe = client.verify_created_wired_pppoe(
                pppoe_id="PPPoE0",
                transport_id=transport_id,
                port_id=port_id,
                vlan_id=101,
                expected_order=1,
                expected_identity="user",
                expected_service="provider",
                expected_mtu=1492,
            )
            pptp = client.verify_created_wired_pptp(
                pptp_id="PPTP0",
                transport_id=transport_id,
                port_id=port_id,
                vlan_id=101,
                expected_order=2,
                expected_identity="user",
                expected_server="vpn.example",
                expected_mtu=1400,
            )
            l2tp = client.verify_created_wired_l2tp(
                l2tp_id="L2TP0",
                transport_id=transport_id,
                port_id=port_id,
                vlan_id=101,
                expected_order=3,
                expected_identity="user",
                expected_server="vpn.example",
                expected_mtu=1400,
            )

        for success, message in (pppoe, pptp, l2tp):
            self.assertTrue(success, message)

    def test_pppoe_delete_preparation_and_rollback_are_preserved(self) -> None:
        client = self.make_client()
        transport_id = "GigabitEthernet0/Vlan101"
        port_id = "GigabitEthernet0/1"
        client.get_rci_data = MagicMock(
            return_value={
                "PPPoE0": {"up": False},
                transport_id: {"up": False},
                port_id: {"synthetic": True},
            }
        )
        client.extract_port_access_vid = MagicMock(return_value=1)
        client.extract_port_roles = MagicMock(return_value=[])

        with patch(
            "core.services.wired_tunnel_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_wired_pppoe_delete_preparation(
                pppoe_id="PPPoE0",
                transport_id=transport_id,
                port_id=port_id,
                home_vlan_id=1,
                manager_profile_detached=True,
            )
        self.assertTrue(success, message)

        client.execute_parse_command = MagicMock(return_value=(True, ""))
        client.set_internet_priorities = MagicMock(return_value=(True, ""))
        success, message = client.rollback_wired_pppoe_deletion(
            context={
                "interface_id": "PPPoE0",
                "transport_id": transport_id,
                "port_id": port_id,
                "old_switch_mode": "access",
                "old_access_vid": 1,
                "old_roles": [],
                "ping_profile": "_KM_PPPoE0",
                "transport_was_up": True,
                "was_up": True,
            },
            old_priorities={"ISP": 0, "PPPoE0": 1},
        )
        self.assertTrue(success, message)
        self.assertEqual(
            client.execute_parse_command.call_args_list[-1].args[0],
            "system configuration save",
        )

    def test_tunnel_wrappers_translate_protocol_and_identifier(self) -> None:
        service = WiredTunnelLifecycleService(MagicMock())
        service.verify_deleted_wired_pppoe = MagicMock(
            return_value=(False, "PPPoE verification failed")
        )

        success, message = service.verify_deleted_wired_pptp(
            pptp_id="PPTP0",
            transport_id="ISP2",
            port_id="GigabitEthernet0/1",
            home_vlan_id=1,
            remaining_priorities={"ISP": 0},
        )

        self.assertFalse(success)
        self.assertEqual(message, "PPTP verification failed")
        service.verify_deleted_wired_pppoe.assert_called_once_with(
            pppoe_id="PPTP0",
            transport_id="ISP2",
            port_id="GigabitEthernet0/1",
            home_vlan_id=1,
            remaining_priorities={"ISP": 0},
        )

    def test_ipoe_deletion_rollback_restores_port_state_and_priorities(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={"ISP2": {"up": False}}
        )
        client.execute_parse_command = MagicMock(return_value=(True, ""))
        client.set_internet_priorities = MagicMock(return_value=(True, ""))
        context = {
            "interface_id": "ISP2",
            "port_id": "GigabitEthernet0/1",
            "old_switch_mode": "access",
            "old_access_vid": 1,
            "old_roles": [{"role": "private", "ifor": "Bridge0"}],
            "ping_profile": "_KM_ISP2",
            "was_up": True,
        }

        success, message = client.rollback_wired_ipoe_deletion(
            context=context,
            old_priorities={"ISP": 0, "ISP2": 1},
        )

        self.assertTrue(success, message)
        commands = [
            call.args[0]
            for call in client.execute_parse_command.call_args_list
        ]
        self.assertEqual(commands[0], "interface GigabitEthernet0/1 no role")
        self.assertIn(
            "interface GigabitEthernet0/1 role private for Bridge0",
            commands,
        )
        self.assertEqual(commands[-1], "system configuration save")
        client.set_internet_priorities.assert_called_once_with(
            {"ISP": 0, "ISP2": 1}
        )

    def test_ipoe_creation_rollback_removes_partial_objects(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            side_effect=[
                {"_KM_ISP2": {"mode": "icmp"}},
                {"ISP2": {"up": False}},
            ]
        )
        client.find_ping_check_profile = MagicMock(
            return_value={"mode": "icmp"}
        )
        client.execute_parse_command = MagicMock(return_value=(True, ""))
        client.set_internet_priorities = MagicMock(return_value=(True, ""))

        success, message = client.rollback_wired_ipoe_creation(
            interface_id="ISP2",
            port_id="GigabitEthernet0/1",
            old_access_vid=1,
            old_switch_mode="access",
            old_roles=[{"role": "private", "ifor": "Bridge0"}],
            old_priorities={"ISP": 0},
        )

        self.assertTrue(success, message)
        commands = [
            call.args[0]
            for call in client.execute_parse_command.call_args_list
        ]
        self.assertIn("interface ISP2 no ping-check profile", commands)
        self.assertIn("no interface ISP2", commands)
        self.assertTrue(
            any(
                command.startswith("no ping-check profile _KM_")
                for command in commands
            )
        )
        self.assertEqual(commands[-1], "system configuration save")
        client.set_internet_priorities.assert_called_once_with({"ISP": 0})

    def test_ipoe_delete_preparation_accepts_disabled_detached_channel(self) -> None:
        client = self.make_client()
        client.get_rci_data = MagicMock(
            return_value={
                "ISP2": {"up": False},
                "GigabitEthernet0/1": {"synthetic": True},
            }
        )
        client.extract_port_access_vid = MagicMock(return_value=1)
        client.extract_port_roles = MagicMock(return_value=[])

        with patch(
            "core.services.wired_ipoe_lifecycle_service.time.sleep",
            return_value=None,
        ):
            success, message = client.verify_wired_ipoe_delete_preparation(
                interface_id="ISP2",
                port_id="GigabitEthernet0/1",
                home_vlan_id=1,
                manager_profile_detached=True,
            )

        self.assertTrue(success, message)


class WiredLifecycleSourceBoundaryTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    @staticmethod
    def method_spans(path: Path, class_name: str) -> dict[str, int]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        return {
            node.name: int(node.end_lineno) - int(node.lineno) + 1
            for node in class_node.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_lifecycle_implementations_are_physically_moved(self) -> None:
        client_spans = self.method_spans(
            self.root / "core" / "keenetic_client.py",
            "KeeneticClient",
        )
        ipoe_spans = self.method_spans(
            self.root
            / "core"
            / "services"
            / "wired_ipoe_lifecycle_service.py",
            "WiredIpoeLifecycleService",
        )
        tunnel_spans = self.method_spans(
            self.root
            / "core"
            / "services"
            / "wired_tunnel_lifecycle_service.py",
            "WiredTunnelLifecycleService",
        )

        for name in (*IPOE_METHODS, *TUNNEL_METHODS):
            self.assertLess(client_spans[name], 35, name)

        self.assertGreater(
            ipoe_spans["rollback_wired_ipoe_creation"],
            150,
        )
        self.assertGreater(
            tunnel_spans["verify_created_wired_pppoe"],
            120,
        )

    def test_client_constructs_both_lifecycle_services(self) -> None:
        source = (
            self.root / "core" / "keenetic_client.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "self.wired_ipoe_lifecycle_service = WiredIpoeLifecycleService(self)",
            source,
        )
        self.assertIn(
            "self.wired_tunnel_lifecycle_service = (",
            source,
        )
        self.assertIn(
            "self.wired_ipoe_lifecycle_service.verify_created_wired_ipoe(",
            source,
        )
        self.assertIn(
            "self.wired_tunnel_lifecycle_service.verify_created_wired_pppoe(",
            source,
        )

    def test_services_export_required_contracts(self) -> None:
        for service_type, names in (
            (WiredIpoeLifecycleService, IPOE_METHODS),
            (WiredTunnelLifecycleService, TUNNEL_METHODS),
        ):
            for name in names:
                self.assertTrue(
                    callable(getattr(service_type, name, None)),
                    name,
                )


if __name__ == "__main__":
    unittest.main()
