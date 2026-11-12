from __future__ import annotations

import unittest
from typing import Any

from core.services.errors import KeeneticConnectionError
from core.services.operation_result import OperationState
from core.services.router_update_service import RouterUpdateService


class FakeResponse:
    def __init__(self, payload: Any, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class FakeTransport:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> Any:
        self.requests.append(dict(kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self) -> None:
        self.timeout = 3
        self.version_data = {
            "release": "5.01.C.1.0-0",
            "title": "5.1.1",
        }
        self.transport = FakeTransport()
        self.query_result: tuple[bool, Any, str] = (True, [], "")
        self.query_results: list[tuple[bool, Any, str]] = []
        self.query_calls: list[tuple[Any, float]] = []
        self.batch_result = (True, "")
        self.batch_calls: list[tuple[list[dict[str, Any]], str]] = []

    def extract_version(self, data: dict[str, Any]) -> str:
        return str(data.get("release") or "")

    def post_rci_query(self, payload: Any, *, timeout: float) -> tuple[bool, Any, str]:
        self.query_calls.append((payload, timeout))
        if self.query_results:
            return self.query_results.pop(0)
        return self.query_result

    def execute_rci_batch(
        self,
        commands: list[dict[str, Any]],
        *,
        operation_name: str,
    ) -> tuple[bool, str]:
        self.batch_calls.append((commands, operation_name))
        return self.batch_result

    @staticmethod
    def extract_batch_rci_errors(payload: Any) -> list[str]:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("error"):
                    return [str(item["error"])]
        return []


class RouterUpdateServiceTests(unittest.TestCase):
    @staticmethod
    def update_payload(*, valid: str = "yes") -> list[dict[str, Any]]:
        return [
            {
                "parse": {
                    "firmware": {"version": "5.01.C.1.0-0"},
                    "sandbox": "stable",
                    "local": {"sandbox": "preview"},
                }
            },
            {
                "parse": {
                    "release": "5.02.A.1.0-0",
                    "sandbox": "preview",
                    "valid": valid,
                }
            },
        ]

    def test_check_uses_official_read_only_commands_and_normalizes_result(self) -> None:
        client = FakeClient()
        client.query_result = (True, self.update_payload(), "")

        success, message, info = RouterUpdateService(client).check_for_updates()

        self.assertTrue(success, message)
        self.assertEqual(info["channel"], "preview")
        self.assertEqual(info["installed_version"], "5.01.C.1.0-0")
        self.assertEqual(info["available_version"], "5.02.A.1.0-0")
        self.assertTrue(info["update_available"])
        self.assertEqual(
            client.query_calls[0][0],
            {"parse": "components check-update force"},
        )

    def test_check_waits_for_router_and_reads_dynamic_channels(self) -> None:
        client = FakeClient()
        client.query_results = [
            (
                True,
                {
                    "parse": {
                        "sandboxes": [
                            {"version": "4.03.C.9.0-0", "name": "lts-4.3"},
                            {"version": "5.01.C.4.0-1", "name": "stable"},
                            {"version": "5.02.A.7.0-0", "name": "draft"},
                        ],
                        "sandbox": "",
                        "release": "",
                        "update-available": True,
                        "checking": True,
                    }
                },
                "",
            ),
            (
                True,
                {
                    "parse": {
                        "sandboxes": [
                            {"version": "4.03.C.9.0-0", "name": "lts-4.3"},
                            {"version": "5.01.C.4.0-1", "name": "stable"},
                            {"version": "5.02.A.7.0-0", "name": "draft"},
                        ],
                        "sandbox": "stable",
                        "release": "5.01.C.4.0-1",
                        "title": "5.1.4",
                        "update-available": True,
                        "checking": False,
                    }
                },
                "",
            ),
        ]

        success, message, info = RouterUpdateService(
            client,
            sleep=lambda _seconds: None,
        ).check_for_updates()

        self.assertTrue(success, message)
        self.assertEqual(info["installed_version"], "5.1.1")
        self.assertEqual(info["available_version"], "5.1.4")
        self.assertEqual(info["channel"], "stable")
        self.assertTrue(info["update_available"])
        self.assertEqual(len(client.query_calls), 2)
        self.assertEqual(
            client.query_calls[1][0],
            {"parse": "components check-update"},
        )
        self.assertIn(
            "lts-4.3",
            {item["name"] for item in info["channels"]},
        )

    def test_equal_release_is_reported_as_current(self) -> None:
        payload = self.update_payload(valid="no")
        payload[1]["parse"]["release"] = "5.01.C.1.0-0"

        info = RouterUpdateService.normalize_status(
            payload,
            current_version="5.01.C.1.0-0",
        )

        self.assertFalse(info.update_available)
        self.assertIn("актуальная", info.message)

    def test_unsupported_command_is_explicit(self) -> None:
        client = FakeClient()
        client.query_result = (
            False,
            None,
            'Core::Configurator: not found: "components/check-update"',
        )

        success, message, info = RouterUpdateService(client).check_for_updates()

        self.assertFalse(success)
        self.assertFalse(info["supported"])
        self.assertIn("не поддерживается", message)

    def test_channel_change_is_validated_and_saved_in_one_batch(self) -> None:
        client = FakeClient()

        result = RouterUpdateService(client).set_channel("preview")

        self.assertEqual(result.state, OperationState.CONFIRMED)
        commands, operation_name = client.batch_calls[0]
        self.assertEqual(
            commands[0],
            {"parse": "components auto-update channel preview"},
        )
        self.assertEqual(
            commands[1],
            {"system": {"configuration": {"save": {}}}},
        )
        self.assertEqual(operation_name, "Смена канала обновления")

    def test_install_stages_selected_channel_then_commits_without_retry(self) -> None:
        client = FakeClient()
        client.transport = FakeTransport(
            [
                FakeResponse(
                    [
                        {
                            "parse": {
                                "firmware": {"version": "5.02.A.1.0-0"}
                            }
                        }
                    ]
                ),
                FakeResponse([{"parse": {"prompt": "(config)"}}]),
            ]
        )

        result = RouterUpdateService(client).install_update("stable")

        self.assertEqual(result.state, OperationState.UNKNOWN)
        self.assertTrue(result.accepted)
        self.assertTrue(result.details["reboot_expected"])
        self.assertEqual(
            client.transport.requests[0]["path"],
            "/rci/components/list",
        )
        self.assertEqual(
            client.transport.requests[0]["json_payload"],
            {"sandbox": "stable"},
        )
        self.assertEqual(
            client.transport.requests[1]["json_payload"],
            [{"parse": "components commit"}],
        )
        self.assertFalse(client.transport.requests[1]["retry_transient"])
        self.assertFalse(client.transport.requests[1]["allow_reauthentication"])

    def test_install_waits_until_async_component_list_is_ready(self) -> None:
        client = FakeClient()
        client.transport = FakeTransport(
            [
                FakeResponse({"continued": True}),
                FakeResponse({"continued": True}),
                FakeResponse(
                    {
                        "firmware": {"version": "5.01.C.4.0-1"},
                        "sandbox": "stable",
                        "component": {"base": {"queued": True}},
                    }
                ),
                FakeResponse([{"parse": {"prompt": "(config)"}}]),
            ]
        )

        result = RouterUpdateService(
            client,
            sleep=lambda _seconds: None,
        ).install_update("stable")

        self.assertEqual(result.state, OperationState.UNKNOWN)
        self.assertEqual(
            [request["method"] for request in client.transport.requests],
            ["POST", "GET", "GET", "POST"],
        )
        self.assertEqual(
            [request["path"] for request in client.transport.requests],
            [
                "/rci/components/list",
                "/rci/components/list",
                "/rci/components/list",
                "/rci/",
            ],
        )
        self.assertEqual(result.details["available_version"], "5.01.C.4.0-1")

    def test_lost_commit_response_is_expected_reboot_not_hard_failure(self) -> None:
        client = FakeClient()
        client.transport = FakeTransport(
            [
                FakeResponse([{"parse": {"firmware": {"version": "5.2"}}}]),
                KeeneticConnectionError("connection closed"),
            ]
        )

        result = RouterUpdateService(client).install_update("draft")

        self.assertEqual(result.state, OperationState.UNKNOWN)
        self.assertTrue(result.accepted)
        self.assertTrue(result.details["response_lost"])


if __name__ == "__main__":
    unittest.main()
