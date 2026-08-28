from __future__ import annotations

import unittest
from typing import Any

from core.keenetic_client import KeeneticClient
from core.rci import RCI_ENDPOINTS, RouterCapabilities
from core.services.usb_service import UsbService


class FakeResponse:
    def __init__(self, data: Any) -> None:
        self.data = data

    def json(self) -> Any:
        if isinstance(self.data, ValueError):
            raise self.data
        return self.data


class FakeTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> FakeResponse | None:
        self.calls.append(dict(kwargs))
        value = self.responses.pop(0)
        return value if value is None else FakeResponse(value)


class FakeClient:
    def __init__(self, version_data: dict[str, Any], responses: list[Any]) -> None:
        self.version_data = version_data
        self.capabilities = RouterCapabilities()
        self.timeout = 3.0
        self.transport = FakeTransport(responses)
        self.data_calls: list[tuple[str, bool]] = []

    def get_rci_data(self, *, endpoint: str, required: bool) -> dict[str, Any]:
        self.data_calls.append((endpoint, required))
        return {"endpoint": endpoint}

    @staticmethod
    def extract_rci_error(data: dict[str, Any]) -> str:
        return str(data.get("error") or "")


class UsbServiceTests(unittest.TestCase):
    def test_unsupported_router_is_not_probed(self) -> None:
        client = FakeClient(
            {"ndw": {"components": "mws", "features": "wireless"}},
            [],
        )
        service = UsbService(client)

        self.assertEqual(service.get_data(), {})
        self.assertEqual(service.rci_endpoint, "")
        self.assertEqual(client.transport.calls, [])
        self.assertEqual(client.data_calls, [])

    def test_supported_endpoint_is_detected_once_and_cached(self) -> None:
        client = FakeClient(
            {"ndw": {"components": "usb,storage"}},
            [{"port": []}],
        )
        service = UsbService(client)

        first = service.get_data()
        second = service.get_data()

        endpoint = RCI_ENDPOINTS["usb"].path
        self.assertEqual(service.rci_endpoint, endpoint)
        self.assertEqual(first, {"endpoint": endpoint})
        self.assertEqual(second, first)
        self.assertEqual(len(client.transport.calls), 1)
        self.assertEqual(
            client.data_calls,
            [(endpoint, False), (endpoint, False)],
        )

    def test_optional_probe_rejects_unsupported_and_invalid_payloads(self) -> None:
        client = FakeClient(
            {},
            [
                {"error": "not found"},
                ValueError("invalid json"),
                ["not", "a", "mapping"],
            ],
        )
        service = UsbService(client)

        for endpoint in ("show/usb", "show/storage", "show/other"):
            self.assertEqual(
                service.probe_optional_rci_endpoint(endpoint),
                (False, {}),
            )

    def test_legacy_client_facade_and_endpoint_alias_are_preserved(self) -> None:
        client = KeeneticClient("admin", "secret")
        client._usb_rci_endpoint = RCI_ENDPOINTS["storage"].path

        self.assertEqual(
            client.usb_service.rci_endpoint,
            RCI_ENDPOINTS["storage"].path,
        )
        self.assertEqual(
            client.split_capability_tokens("USB, storage"),
            {"usb", "storage"},
        )

        client.close()
        self.assertIsNone(client._usb_rci_endpoint)
