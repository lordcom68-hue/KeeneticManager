from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import requests

from core.services.priority_service import PriorityService, PrioritySnapshot


class FakeResponse:
    def __init__(self, *, data: Any = None, status_code: int = 200, text: str = "") -> None:
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self) -> Any:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeClient:
    def __init__(self) -> None:
        self.router_id = 7
        self.session: FakeSession | None = FakeSession()
        self.base_url = "https://router.invalid"
        self.timeout = 2.0
        self.verify_ssl = False
        self.config: dict[str, Any] = {}
        self.rci_calls: list[tuple[str, bool]] = []

    def get_rci_data(self, *, endpoint: str, required: bool) -> Any:
        self.rci_calls.append((endpoint, required))
        return self.config

    def execute_rci_batch(
        self,
        commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        assert self.session is not None
        try:
            response = self.session.post(
                f"{self.base_url}/rci/",
                json=commands,
                timeout=max(self.timeout, 15),
                verify=self.verify_ssl,
                allow_redirects=True,
            )
        except requests.RequestException as error:
            return False, f"{operation_name}: {error}"
        if response.status_code in (401, 403):
            return False, "Недостаточно прав для изменения подключений."
        data = response.json()
        errors = self.extract_batch_rci_errors(data)
        if errors:
            return False, f"{operation_name}: " + "; ".join(errors)
        return True, f"{operation_name}: выполнено."

    @staticmethod
    def extract_batch_rci_errors(data: Any) -> list[str]:
        if isinstance(data, dict) and data.get("error"):
            return [str(data["error"])]
        return []

    @staticmethod
    def truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


class PriorityServiceTests(unittest.TestCase):
    def build(self) -> tuple[FakeClient, PriorityService]:
        client = FakeClient()
        return client, PriorityService(client, sleep=lambda _s: None)

    def test_snapshot_reports_primary_and_backups(self) -> None:
        client, service = self.build()
        client.config = {
            "WifiStation0": {"ip": {"global": {"order": 1}}},
            "Ethernet0": {"ip": {"global": {"order": 0}}},
            "Lte0": {"ip": {"global": {"order": 2}}},
        }
        snapshot = service.read_snapshot()
        self.assertIsInstance(snapshot, PrioritySnapshot)
        self.assertEqual(snapshot.primary_interface, "Ethernet0")
        self.assertEqual(snapshot.backup_interfaces, ("WifiStation0", "Lte0"))
        self.assertEqual(client.rci_calls, [("show/sc/interface", False)])

    def test_current_orders_ignore_invalid_and_negative(self) -> None:
        config = {
            "A": {"ip": {"global": {"order": "0"}}},
            "B": {"ip": {"global": {"order": -1}}},
            "C": {"ip": {"global": {"order": "bad"}}},
            "D": [],
        }
        self.assertEqual(PriorityService.current_global_orders(config), {"A": 0})

    def test_normalize_order_map_removes_gaps_and_duplicates(self) -> None:
        self.assertEqual(
            PriorityService.normalize_order_map({"A": 5, "B": 1, "C": 1, "": 0, "D": -1}),
            {"B": 0, "C": 1, "A": 2},
        )

    def test_insert_and_remove_interface_are_contiguous(self) -> None:
        old = {"A": 0, "B": 1, "C": 2}
        self.assertEqual(
            PriorityService.priorities_with_inserted_interface(
                old, interface_id="C", requested_order=0
            ),
            {"C": 0, "A": 1, "B": 2},
        )
        self.assertEqual(
            PriorityService.priorities_without_interface(old, interface_id="B"),
            {"A": 0, "C": 1},
        )

    def test_roles_and_type_fallback_order(self) -> None:
        self.assertEqual(PriorityService.role_for_order(0), "primary")
        self.assertEqual(PriorityService.role_for_order(2), "backup")
        self.assertEqual(PriorityService.role_for_order("bad"), "")
        self.assertEqual(PriorityService.connection_role_order("PPPoE"), 0)
        self.assertEqual(PriorityService.connection_role_order("WifiStation"), 1)
        self.assertEqual(PriorityService.connection_role_order("LTE"), 2)

    def test_priority_extractors_keep_legacy_contract(self) -> None:
        self.assertEqual(PriorityService.interface_priority({"priority": "7.0"}), 7)
        self.assertEqual(PriorityService.interface_priority({}), 999999)
        self.assertEqual(PriorityService.extract_valid_priority({"priority": 1}), 1)
        self.assertIsNone(PriorityService.extract_valid_priority({"priority": 0}))

    def test_merge_adds_missing_tunnel_with_role(self) -> None:
        client, service = self.build()
        result = service.merge_configured_global_connections(
            connections=[{"interface_id": "Ethernet0", "global_order": 0}],
            interface_data={"PPTP0": {"state": "down"}},
            interface_config_data={
                "Ethernet0": {"ip": {"global": {"order": 0}}},
                "PPTP0": {"description": "Provider", "up": True, "ip": {"global": {"order": 1}}},
            },
        )
        self.assertEqual([x["interface_id"] for x in result], ["Ethernet0", "PPTP0"])
        self.assertEqual(result[1]["role"], "backup")
        self.assertEqual(result[1]["type"], "PPTP")

    def test_sort_connections_puts_unordered_last(self) -> None:
        values = [
            {"interface_id": "Z"},
            {"interface_id": "B", "global_order": 1},
            {"interface_id": "A", "global_order": 0},
        ]
        self.assertEqual(
            [x["interface_id"] for x in PriorityService.sort_connections(values)], ["A", "B", "Z"]
        )

    def test_validation_rejects_empty_negative_and_gaps(self) -> None:
        for value, expected in [({}, "пуст"), ({"A": -1}, "отриц"), ({"A": 0, "B": 2}, "непрерыв")]:
            ok, msg, _ = PriorityService.validate_requested_orders(value)
            self.assertFalse(ok)
            self.assertIn(expected, msg)

    def test_batch_commands_are_sorted_and_saved(self) -> None:
        commands = PriorityService.build_batch_commands({"B": 1, "A": 0})
        self.assertEqual(commands[0]["interface"]["name"], "A")
        self.assertEqual(commands[1]["interface"]["name"], "B")
        self.assertIn("system", commands[-1])

    def test_set_priorities_without_session(self) -> None:
        client, service = self.build()
        client.session = None
        self.assertEqual(
            service.set_internet_priorities({"A": 0}), (False, "Нет активной сессии с роутером.")
        )

    def test_set_priorities_posts_batch_and_verifies(self) -> None:
        client, service = self.build()
        assert client.session is not None
        client.session.responses = [FakeResponse(data={})]
        client.config = {
            "A": {"ip": {"global": {"order": 0}}},
            "B": {"ip": {"global": {"order": 1}}},
        }
        service.write_log = lambda _m: None  # type: ignore[method-assign]
        self.assertEqual(
            service.set_internet_priorities({"B": 1, "A": 0}),
            (True, "Порядок интернет-каналов изменён и сохранён."),
        )
        payload = client.session.calls[0]["json"]
        self.assertEqual(payload[0]["interface"]["name"], "A")
        self.assertEqual(payload[1]["interface"]["name"], "B")

    def test_set_priorities_reports_request_error(self) -> None:
        client, service = self.build()
        assert client.session is not None
        client.session.responses = [requests.Timeout("timeout")]
        service.write_log = lambda _m: None  # type: ignore[method-assign]
        ok, msg = service.set_internet_priorities({"A": 0})
        self.assertFalse(ok)
        self.assertIn("timeout", msg)

    def test_set_priorities_reports_permissions(self) -> None:
        client, service = self.build()
        assert client.session is not None
        client.session.responses = [FakeResponse(data={}, status_code=403)]
        service.write_log = lambda _m: None  # type: ignore[method-assign]
        self.assertEqual(
            service.set_internet_priorities({"A": 0}),
            (False, "Недостаточно прав для изменения подключений."),
        )

    def test_set_priorities_reports_rci_error(self) -> None:
        client, service = self.build()
        assert client.session is not None
        client.session.responses = [FakeResponse(data={"error": "denied"})]
        service.write_log = lambda _m: None  # type: ignore[method-assign]
        ok, msg = service.set_internet_priorities({"A": 0})
        self.assertFalse(ok)
        self.assertIn("denied", msg)

    def test_verify_reports_mismatch(self) -> None:
        client, service = self.build()
        client.config = {"A": {"ip": {"global": {"order": 2}}}}
        ok, msg = service.verify_global_interface_orders({"A": 0})
        self.assertFalse(ok)
        self.assertIn("ожидалось 0", msg)


class ClientFacadeTests(unittest.TestCase):
    def test_client_constructs_priority_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(username="admin", password="secret")
        self.assertIsInstance(client.priority_service, PriorityService)

    def test_client_set_facade_delegates(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(username="admin", password="secret")
        captured = []

        def fake(value: dict[str, int]) -> tuple[bool, str]:
            captured.append(value)
            return True, "ok"

        client.priority_service.set_internet_priorities = fake  # type: ignore[method-assign]
        self.assertEqual(client.set_internet_priorities({"A": 0}), (True, "ok"))
        self.assertEqual(captured, [{"A": 0}])

    def test_static_facades_match_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        config = {"A": {"ip": {"global": {"order": 0}}}}
        self.assertEqual(
            KeeneticClient.current_global_orders(config),
            PriorityService.current_global_orders(config),
        )
        self.assertEqual(
            KeeneticClient.connection_role_order("LTE"),
            PriorityService.connection_role_order("LTE"),
        )


class SourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def test_refresh_and_services_route_directly(self) -> None:
        services = self.root / "core/services"
        result_builder = (
            services / "connection_result_builder_service.py"
        ).read_text(encoding="utf-8")
        wired = "\n".join(
            (services / name).read_text(encoding="utf-8")
            for name in (
                "wired_internet_service.py",
                "wired_ipoe_service.py",
                "wired_pppoe_service.py",
                "wired_pptp_service.py",
                "wired_l2tp_service.py",
                "cable_protocol_change_service.py",
                "dedicated_wan_service.py",
            )
        )
        wireless = (self.root / "core/services/wireless_isp_service.py").read_text(encoding="utf-8")
        action_dispatcher = (
            self.root / "core/router_action_dispatcher.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "self.client.priority_service.merge_configured_global_connections",
            result_builder,
        )
        self.assertIn(
            "self.client.priority_service.sort_connections",
            result_builder,
        )
        self.assertIn("self.client.priority_service.set_internet_priorities", wired)
        self.assertIn("self.client.priority_service.set_internet_priorities", wireless)
        self.assertIn(
            "client.priority_service.set_internet_priorities",
            action_dispatcher,
        )

    def test_large_set_operation_is_physically_in_service(self) -> None:
        service = (self.root / "core/services/priority_service.py").read_text(encoding="utf-8")
        client = (self.root / "core/keenetic_client.py").read_text(encoding="utf-8")
        self.assertIn("def build_batch_commands", service)
        self.assertIn("self.client.execute_rci_batch", service)
        self.assertNotIn("SEND priorities=", client)


if __name__ == "__main__":
    unittest.main()
