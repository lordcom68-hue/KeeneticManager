from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.keenetic_client import KeeneticClient


INSPECTIONS = (
    ("inspect_dedicated_wan_deletion", "ISP"),
    ("inspect_wired_pppoe_deletion", "PPPoE0"),
    ("inspect_wired_pptp_deletion", "PPTP0"),
    ("inspect_wired_l2tp_deletion", "L2TP0"),
    ("inspect_wired_ipoe_deletion", "GigabitEthernet0/Vlan101"),
)


class WiredDeletionInspectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = KeeneticClient(
            username="admin",
            password="secret",
        )

    def test_missing_interfaces_are_rejected_without_mutation(self) -> None:
        for method_name, interface_id in INSPECTIONS:
            with self.subTest(method=method_name):
                config: dict[str, Any] = {}
                runtime: dict[str, Any] = {}
                method = getattr(
                    self.client.wired_deletion_inspection_service,
                    method_name,
                )

                context, reason = method(
                    interface_id=interface_id,
                    interface_config=config,
                    interface_runtime=runtime,
                )

                self.assertEqual(context, {})
                self.assertTrue(reason)
                self.assertEqual(config, {})
                self.assertEqual(runtime, {})

    def test_none_runtime_is_supported(self) -> None:
        context, reason = (
            self.client.wired_deletion_inspection_service
            .inspect_wired_ipoe_deletion(
                interface_id="GigabitEthernet0/Vlan101",
                interface_config={},
                interface_runtime=None,
            )
        )

        self.assertEqual(context, {})
        self.assertTrue(reason)

    def test_authenticated_protocol_contexts_are_built(self) -> None:
        for method_name, interface_id in (
            ("inspect_wired_pppoe_deletion", "PPPoE0"),
            ("inspect_wired_pptp_deletion", "PPTP0"),
            ("inspect_wired_l2tp_deletion", "L2TP0"),
        ):
            with self.subTest(method=method_name):
                transport_id = "GigabitEthernet0/Vlan101"
                config = self._switched_provider_config(
                    interface_id=interface_id,
                    transport_id=transport_id,
                )

                context, reason = getattr(
                    self.client.wired_deletion_inspection_service,
                    method_name,
                )(
                    interface_id=interface_id,
                    interface_config=config,
                    interface_runtime={},
                )

                self.assertEqual(reason, "")
                self.assertEqual(context["interface_id"], interface_id)
                self.assertEqual(context["transport_id"], transport_id)
                self.assertEqual(context["port_id"], "GigabitEthernet0/1")
                self.assertEqual(context["home_vlan_id"], 1)
                self.assertEqual(context["global_order"], 0)

    def test_ipoe_context_is_built_for_reassigned_lan_port(self) -> None:
        interface_id = "GigabitEthernet0/Vlan101"
        config = self._switched_provider_config(
            interface_id=interface_id,
            transport_id=interface_id,
            ipoe=True,
        )

        context, reason = (
            self.client.wired_deletion_inspection_service
            .inspect_wired_ipoe_deletion(
                interface_id=interface_id,
                interface_config=config,
                interface_runtime={},
            )
        )

        self.assertEqual(reason, "")
        self.assertEqual(context["interface_id"], interface_id)
        self.assertEqual(context["vlan_id"], 101)
        self.assertEqual(context["home_vlan_id"], 1)
        self.assertEqual(context["old_access_vid"], 101)

    def test_dedicated_wan_context_is_built(self) -> None:
        interface_id = "GigabitEthernet0"
        config = {
            interface_id: {
                "description": "WAN",
                "ip": {"global": {"order": 0}},
                "security-level": "public",
                "up": True,
            },
            "Bridge0": {
                "description": "Home network",
                "security-level": "private",
                "include": {"interface": "GigabitEthernet1/Vlan1"},
            },
        }
        runtime = {
            interface_id: {
                "type": "Ethernet",
                "link": "up",
                "state": "up",
                "port": {
                    "id": "GigabitEthernet0/0",
                    "label": "0",
                    "link": "up",
                    "speed": 1_000_000_000,
                },
            }
        }

        context, reason = (
            self.client.wired_deletion_inspection_service
            .inspect_dedicated_wan_deletion(
                interface_id=interface_id,
                interface_config=config,
                interface_runtime=runtime,
            )
        )

        self.assertEqual(reason, "")
        self.assertEqual(context["transport_id"], interface_id)
        self.assertEqual(context["bridge_id"], "Bridge0")
        self.assertEqual(context["port_id"], "GigabitEthernet0/0")
        self.assertTrue(context["port_link_known"])
        self.assertTrue(context["port_link_active"])

    @staticmethod
    def _switched_provider_config(
        *,
        interface_id: str,
        transport_id: str,
        ipoe: bool = False,
    ) -> dict[str, Any]:
        provider: dict[str, Any] = {
            "description": interface_id,
            "security-level": "public",
            "ip": {"global": {"order": 0}},
            "up": True,
        }
        if not ipoe:
            provider["connect"] = {"via": transport_id}

        return {
            interface_id: provider,
            transport_id: (
                provider
                if ipoe
                else {
                    "description": "Provider transport",
                    "ip": {},
                    "up": True,
                }
            ),
            "GigabitEthernet0/1": {
                "rename": "LAN1",
                "role": {"role": "inet", "ifor": transport_id},
                "switchport": {"access": {"vid": 101}},
            },
            "Bridge0": {
                "description": "Home network",
                "security-level": "private",
                "include": {"interface": "GigabitEthernet0/Vlan1"},
            },
        }

    def test_client_facades_delegate_exact_arguments(self) -> None:
        calls: list[tuple[str, str, Any, Any]] = []

        class InspectionStub:
            def __getattr__(self, name: str) -> Any:
                def inspect(
                    *,
                    interface_id: str,
                    interface_config: dict[str, Any],
                    interface_runtime: dict[str, Any] | None = None,
                ) -> tuple[dict[str, Any], str]:
                    calls.append(
                        (
                            name,
                            interface_id,
                            interface_config,
                            interface_runtime,
                        )
                    )
                    return {"delegated": True}, ""

                return inspect

        self.client.wired_deletion_inspection_service = InspectionStub()
        config = {"ISP": {"global": True}}
        runtime = {"ISP": {"link": "up"}}

        for method_name, interface_id in INSPECTIONS:
            with self.subTest(method=method_name):
                result = getattr(self.client, method_name)(
                    interface_id=interface_id,
                    interface_config=config,
                    interface_runtime=runtime,
                )
                self.assertEqual(result, ({"delegated": True}, ""))

        self.assertEqual(len(calls), len(INSPECTIONS))
        for call, (method_name, interface_id) in zip(
            calls,
            INSPECTIONS,
            strict=True,
        ):
            self.assertEqual(call, (method_name, interface_id, config, runtime))


class SourceBoundaryTests(unittest.TestCase):
    @staticmethod
    def method_span(path: Path, class_name: str, method_name: str) -> int:
        module = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        return int(method.end_lineno) - int(method.lineno) + 1

    def test_inspections_are_physically_in_read_only_service(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root
            / "core"
            / "services"
            / "wired_deletion_inspection_service.py"
        )
        client = root / "core" / "keenetic_client.py"

        for method_name, _interface_id in INSPECTIONS:
            with self.subTest(method=method_name):
                self.assertGreater(
                    self.method_span(
                        service,
                        "WiredDeletionInspectionService",
                        method_name,
                    ),
                    150,
                )
                self.assertLess(
                    self.method_span(client, "KeeneticClient", method_name),
                    20,
                )

    def test_inspection_service_contains_no_router_writes(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "services"
            / "wired_deletion_inspection_service.py"
        ).read_text(encoding="utf-8")

        for fragment in (
            "execute_rci_batch",
            "execute_parse_command",
            "set_interface_settings",
            "set_internet_priorities",
        ):
            self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
