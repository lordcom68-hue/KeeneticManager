from __future__ import annotations

import unittest
from typing import Any

import requests

from core.services.interface_service import (
    InterfaceService,
    InterfaceSnapshot,
)
from core.services.operation_result import OperationState


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        data: Any = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._data = data
        self.ok = 200 <= status_code < 300

    def json(self) -> Any:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    def __init__(self) -> None:
        self.post_responses: list[Any] = []
        self.get_responses: list[Any] = []
        self.calls: list[tuple[str, str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: Any,
        timeout: float,
        verify: bool,
        allow_redirects: bool,
    ) -> FakeResponse:
        self.calls.append(("post", url, json))
        value = self.post_responses.pop(0)

        if isinstance(value, Exception):
            raise value

        return value

    def get(
        self,
        url: str,
        *,
        timeout: float,
        verify: bool,
        allow_redirects: bool,
    ) -> FakeResponse:
        self.calls.append(("get", url, None))
        value = self.get_responses.pop(0)

        if isinstance(value, Exception):
            raise value

        return value


class FakeTransport:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.last_error: str | None = None

    def request(self, **kwargs: Any) -> FakeResponse | None:
        method = str(kwargs["method"]).lower()
        path = str(kwargs["path"])
        payload = kwargs.get("json_payload")
        try:
            if method == "post":
                response = self.session.post(
                    "https://router.example" + path,
                    json=payload,
                    timeout=float(kwargs["timeout"]),
                    verify=False,
                    allow_redirects=True,
                )
            else:
                response = self.session.get(
                    "https://router.example" + path,
                    timeout=float(kwargs["timeout"]),
                    verify=False,
                    allow_redirects=True,
                )
        except requests.RequestException as error:
            self.last_error = str(error)
            return None
        if not response.ok and not kwargs.get("return_error_response"):
            return None
        return response

class FakeClient:
    def __init__(self) -> None:
        self.session: FakeSession | None = FakeSession()
        self.transport = FakeTransport(self.session)
        self.base_url = "https://router.example"
        self.timeout = 8.0
        self.verify_ssl = False
        self._manually_disabled_interfaces: set[str] = set()
        self.rci_data: dict[str, Any] = {}
        self.rci_calls: list[tuple[str, bool]] = []

    def get_rci_data(
        self,
        *,
        endpoint: str,
        required: bool,
    ) -> Any:
        self.rci_calls.append((endpoint, required))
        return self.rci_data.get(endpoint, {})


class InterfaceServiceTests(unittest.TestCase):
    def build_service(
        self,
    ) -> tuple[FakeClient, InterfaceService]:
        client = FakeClient()
        service = InterfaceService(
            client,
            sleep=lambda seconds: None,
        )
        return client, service

    def test_read_snapshot_uses_two_canonical_endpoints(self) -> None:
        client, service = self.build_service()
        client.rci_data = {
            "show/interface": {"Ethernet0": {"state": "up"}},
            "show/sc/interface": {"Ethernet0": {"role": ["inet"]}},
        }

        snapshot = service.read_snapshot()

        self.assertIsInstance(snapshot, InterfaceSnapshot)
        self.assertEqual(
            snapshot.runtime["Ethernet0"]["state"],
            "up",
        )
        self.assertEqual(
            snapshot.config["Ethernet0"]["role"],
            ["inet"],
        )
        self.assertEqual(
            client.rci_calls,
            [
                ("show/interface", False),
                ("show/sc/interface", False),
            ],
        )

    def test_non_dictionary_rci_responses_become_empty_maps(self) -> None:
        client, service = self.build_service()
        client.rci_data = {
            "show/interface": [],
            "show/sc/interface": None,
        }

        snapshot = service.read_snapshot()

        self.assertEqual(snapshot.runtime, {})
        self.assertEqual(snapshot.config, {})

    def test_interface_enabled_matches_legacy_contract(self) -> None:
        self.assertFalse(
            InterfaceService.interface_is_enabled(
                {
                    "state": "down",
                    "summary": {
                        "layer": {
                            "conf": "disabled",
                        }
                    },
                }
            )
        )
        self.assertTrue(
            InterfaceService.interface_is_enabled(
                {
                    "state": "up",
                    "summary": {
                        "layer": {
                            "conf": "running",
                        }
                    },
                }
            )
        )

    def test_interface_up_requires_link_connected_and_state(self) -> None:
        self.assertTrue(
            InterfaceService.interface_is_up(
                {
                    "link": "up",
                    "connected": "yes",
                    "state": "up",
                    "summary": {
                        "layer": {
                            "link": "running",
                        }
                    },
                }
            )
        )
        self.assertFalse(
            InterfaceService.interface_is_up(
                {
                    "link": "up",
                    "connected": "no",
                    "state": "up",
                }
            )
        )

    def test_truthy_contract(self) -> None:
        for value in (
            True,
            1,
            1.5,
            "yes",
            "TRUE",
            "on",
            "enabled",
        ):
            self.assertTrue(
                InterfaceService.truthy(value),
                value,
            )

        for value in (
            False,
            0,
            "",
            "no",
            None,
        ):
            self.assertFalse(
                InterfaceService.truthy(value),
                value,
            )

    def test_nested_rci_error_is_extracted(self) -> None:
        self.assertEqual(
            InterfaceService.extract_rci_error(
                {
                    "result": {
                        "warning": "denied",
                    }
                }
            ),
            "denied",
        )

    def test_enable_interface_accepts_textual_success(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            FakeResponse(
                text="interface is up",
                data={"message": "interface is up"},
            )
        ]

        success, message = service.set_enabled(
            "Wireguard0",
            True,
        )

        self.assertTrue(success)
        self.assertEqual(
            message,
            "Интерфейс включён.",
        )
        self.assertNotIn(
            "Wireguard0",
            client._manually_disabled_interfaces,
        )
        self.assertEqual(
            client.session.calls[0][1],
            (
                "https://router.example/"
                "rci/interface/up"
            ),
        )

    def test_disable_interface_tracks_manual_state(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            FakeResponse(
                text="interface is down",
                data={},
            )
        ]

        success, message = service.set_enabled(
            "WifiMaster0/AccessPoint0",
            False,
        )

        self.assertTrue(success)
        self.assertEqual(
            message,
            "Интерфейс выключен.",
        )
        self.assertIn(
            "WifiMaster0/AccessPoint0",
            client._manually_disabled_interfaces,
        )

    def test_connection_loss_after_down_is_treated_as_sent(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            requests.ConnectionError("closed")
        ]

        success, message = service.set_enabled(
            "Ethernet0",
            False,
        )

        self.assertTrue(success)
        self.assertIn(
            "состояние будет проверено",
            message,
        )
        self.assertIn(
            "Ethernet0",
            client._manually_disabled_interfaces,
        )

    def test_connection_loss_has_typed_unknown_state(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            requests.ConnectionError("closed")
        ]

        result = service.set_enabled_result("Ethernet0", False)

        self.assertEqual(result.state, OperationState.UNKNOWN)
        self.assertTrue(result.accepted)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.details["interface_id"], "Ethernet0")

    def test_enable_network_error_is_reported(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            requests.Timeout("timeout")
        ]

        success, message = service.set_enabled(
            "Ethernet0",
            True,
        )

        self.assertFalse(success)
        self.assertIn(
            "не подтвердил выполнение",
            message,
        )

    def test_permissions_error_is_reported(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            FakeResponse(
                status_code=403,
                text="forbidden",
                data={},
            )
        ]

        success, message = service.set_enabled(
            "Ethernet0",
            True,
        )

        self.assertFalse(success)
        self.assertIn(
            "Недостаточно прав",
            message,
        )

    def test_verify_disabled_accepts_missing_runtime_item(self) -> None:
        client, service = self.build_service()
        client.rci_data["show/interface"] = {}

        verified, state = service.verify_admin_state(
            "Gone0",
            False,
        )

        self.assertTrue(verified)
        self.assertEqual(
            state,
            "интерфейс отсутствует",
        )

    def test_normalize_statistics_walks_nested_payload(self) -> None:
        normalized = InterfaceService.normalize_statistics(
            {
                "result": [
                    {
                        "stat": {
                            "RxBytes": 120,
                            "tx-bps": 45,
                        }
                    }
                ]
            }
        )

        self.assertEqual(
            normalized["rxbytes"],
            120,
        )
        self.assertEqual(
            normalized["tx-bps"],
            45,
        )

    def test_statistics_falls_back_to_second_request_shape(self) -> None:
        client, service = self.build_service()
        assert client.session is not None
        client.session.post_responses = [
            FakeResponse(
                status_code=404,
                text="not found",
                data={},
            ),
            FakeResponse(
                data={
                    "result": {
                        "rxbytes": 10,
                        "txbytes": 20,
                    }
                },
            ),
        ]

        result = service.get_statistics(
            "Ethernet0/1"
        )

        self.assertEqual(
            result["result"]["rxbytes"],
            10,
        )
        self.assertEqual(
            len(client.session.calls),
            2,
        )

    def test_reconnect_runs_down_then_up(self) -> None:
        client, service = self.build_service()
        calls: list[tuple[str, bool]] = []

        def fake_set_enabled(
            interface_id: str,
            enabled: bool,
        ) -> tuple[bool, str]:
            calls.append(
                (interface_id, enabled)
            )
            return True, "ok"

        service.set_enabled = fake_set_enabled  # type: ignore[method-assign]

        success, message = service.reconnect(
            "PPPoE0"
        )

        self.assertTrue(success)
        self.assertEqual(
            message,
            "Интернет-подключение переподключено.",
        )
        self.assertEqual(
            calls,
            [
                ("PPPoE0", False),
                ("PPPoE0", True),
            ],
        )


class KeeneticClientInterfaceFacadeTests(unittest.TestCase):
    def test_client_constructs_interface_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(
            username="admin",
            password="secret",
        )

        self.assertIsInstance(
            client.interface_service,
            InterfaceService,
        )

    def test_static_facades_match_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        item = {
            "state": "down",
            "summary": {
                "layer": {
                    "conf": "disabled",
                }
            },
        }

        self.assertEqual(
            KeeneticClient.interface_is_enabled(item),
            InterfaceService.interface_is_enabled(item),
        )
        self.assertEqual(
            KeeneticClient.truthy("yes"),
            InterfaceService.truthy("yes"),
        )

    def test_action_facade_delegates_to_service(self) -> None:
        from core.keenetic_client import KeeneticClient

        client = KeeneticClient(
            username="admin",
            password="secret",
        )
        calls: list[tuple[str, bool]] = []

        def fake_set_enabled(
            *,
            interface_id: str,
            enabled: bool,
        ) -> tuple[bool, str]:
            calls.append(
                (interface_id, enabled)
            )
            return True, "delegated"

        client.interface_service.set_enabled = (  # type: ignore[method-assign]
            fake_set_enabled
        )

        self.assertEqual(
            client.set_interface_enabled(
                "Ethernet0",
                False,
            ),
            (True, "delegated"),
        )
        self.assertEqual(
            calls,
            [("Ethernet0", False)],
        )


if __name__ == "__main__":
    unittest.main()
