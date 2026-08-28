from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import requests

from core.rci import DiagnosticCollector
from core.services.errors import (
    KeeneticAuthenticationError,
    KeeneticConnectionError,
)
from core.services.rci_transport import RciTransport


class FakeResponse(requests.Response):
    def __init__(
        self,
        status_code: int,
        data: Any = None,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self.headers.update(headers or {})
        self.url = "https://router.example/rci/show/system"
        self.encoding = "utf-8"
        self._content = (
            json.dumps(data).encode("utf-8")
            if data is not None
            else text.encode("utf-8")
        )


class FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.closed = False

    def request(self, **_kwargs: Any) -> requests.Response:
        if not self.responses:
            raise AssertionError("Для FakeSession не задан ответ")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class RciTransportTests(unittest.TestCase):
    def make_transport(
        self,
        root: Path,
        *,
        diagnostics: DiagnosticCollector | None = None,
    ) -> RciTransport:
        return RciTransport(
            username="admin",
            password="secret",
            router_id=7,
            diagnostics=diagnostics,
            history_root=root,
        )

    def test_build_candidates_preserves_expected_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            self.assertEqual(
                transport.build_candidates(
                    local_ip="192.168.1.1",
                    keendns="router.keenetic.pro",
                    connection_mode="auto",
                ),
                [
                    ("http://192.168.1.1", "Локальная сеть"),
                    ("https://router.keenetic.pro", "KeenDNS"),
                ],
            )

    def test_connection_modes_enforce_transport_schemes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))

            self.assertEqual(
                transport.build_candidates(
                    local_ip="https://192.168.1.1",
                    keendns="router.keenetic.pro",
                    connection_mode="local",
                ),
                [("http://192.168.1.1", "Локальная сеть")],
            )
            self.assertEqual(
                transport.build_candidates(
                    local_ip="192.168.1.1",
                    keendns="http://router.keenetic.pro",
                    connection_mode="keendns",
                ),
                [
                    (
                        "https://router.keenetic.pro",
                        "KeenDNS",
                    )
                ],
            )

    def test_plan_and_candidate_are_recorded_before_session_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            candidates = [
                ("https://router.keenetic.pro", "KeenDNS")
            ]
            transport.record_connection_plan(
                local_ip="",
                keendns="router.keenetic.pro",
                connection_mode="auto",
                candidates=candidates,
            )

            def fail_session() -> Any:
                raise requests.ConnectionError("session creation failed")

            transport.create_session = fail_session  # type: ignore[method-assign]
            with self.assertRaises(requests.ConnectionError):
                transport.connect_candidate(
                    base_url=candidates[0][0],
                    connection_type=candidates[0][1],
                )

            phases = [
                item.get("phase")
                for item in transport.diagnostics.connection_attempts
            ]
            self.assertIn("candidate_start", phases)
            self.assertIn("candidate_result", phases)
            history_text = transport.history_path.read_text(
                encoding="utf-8"
            )
            self.assertIn("router.keenetic.pro", history_text)

    def test_keendns_503_is_recorded_and_retried(self) -> None:
        diagnostics = DiagnosticCollector()
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(
                Path(directory), diagnostics=diagnostics
            )
            transport.session = FakeSession(
                [
                    FakeResponse(503, text="unavailable"),
                    FakeResponse(200, {"ok": True}),
                ]
            )
            transport.base_url = "https://router.keenetic.pro"
            transport.connection_type = "KeenDNS"
            transport.verify_ssl = True
            transport.connected = True
            transport.reauthenticate = lambda: True  # type: ignore[method-assign]
            transport._retry_backoff = lambda _attempt: None  # type: ignore[method-assign]

            response = transport.request(
                method="GET",
                path="/rci/show/system",
                endpoint="show/system",
                required=True,
                timeout=1.0,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                item.get("failure_kind") == "keendns_http_503"
                for item in diagnostics.connection_attempts
            )
        )
        states = [
            item.get("new_state")
            for item in diagnostics.connection_attempts
            if item.get("phase") == "state_transition"
        ]
        self.assertIn("unstable", states)
        self.assertIn("retrying", states)
        self.assertIn("connected", states)

    def test_connect_candidate_authenticates_and_owns_session(self) -> None:
        fake_session = FakeSession(
            [
                FakeResponse(
                    401,
                    headers={
                        "X-NDM-Realm": "Keenetic",
                        "X-NDM-Challenge": "challenge",
                    },
                ),
                FakeResponse(200, {"ok": True}),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            transport.create_session = lambda: fake_session  # type: ignore[method-assign]
            transport.connect_candidate(
                base_url="https://router.keenetic.pro",
                connection_type="KeenDNS",
            )

        self.assertIs(transport.session, fake_session)
        self.assertTrue(transport.connected)
        self.assertEqual(transport.connection_type, "KeenDNS")

    def test_failure_classification_uses_wrapped_causes(self) -> None:
        timeout = requests.Timeout("timeout")
        wrapped_timeout = KeeneticConnectionError("wrapped timeout")
        wrapped_timeout.__cause__ = timeout
        dns = requests.ConnectionError(
            "Failed to resolve host: getaddrinfo failed"
        )
        wrapped_dns = KeeneticConnectionError("wrapped dns")
        wrapped_dns.__cause__ = dns

        self.assertEqual(
            RciTransport.classify_failure(
                error=wrapped_timeout,
                status_code=None,
                connection_type="KeenDNS",
                message="error",
            ),
            "timeout",
        )
        self.assertEqual(
            RciTransport.classify_failure(
                error=wrapped_dns,
                status_code=None,
                connection_type="KeenDNS",
                message="error",
            ),
            "dns_error",
        )
        self.assertEqual(
            RciTransport.classify_failure(
                error=KeeneticAuthenticationError("bad password"),
                status_code=None,
                connection_type="KeenDNS",
                message="auth",
            ),
            "authentication",
        )

    def test_explicit_monitor_states_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            transport.record_connection_plan(
                local_ip="192.168.1.1",
                keendns="router.keenetic.pro",
                connection_mode="auto",
                candidates=[],
            )
            transport.record_monitor_state("unstable", "first failure")
            transport.record_monitor_state("retrying", "second failure")
            transport.record_monitor_state("offline", "third failure")
            states = [
                item.get("new_state")
                for item in transport.diagnostics.connection_attempts
                if item.get("phase") == "state_transition"
            ]
        self.assertEqual(
            states[-4:],
            ["connecting", "unstable", "retrying", "offline"],
        )

    def test_mutating_post_is_not_retried_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            session = FakeSession(
                [
                    requests.Timeout("timeout"),
                    FakeResponse(200, {"unexpected": True}),
                ]
            )
            transport.session = session
            transport.base_url = "https://router.keenetic.pro"
            transport.connection_type = "KeenDNS"
            transport.connected = True

            with self.assertRaises(KeeneticConnectionError):
                transport.request(
                    method="POST",
                    path="/rci/",
                    endpoint="batch:test",
                    required=True,
                    timeout=1.0,
                    json_payload=[{"save": {}}],
                    retry_transient=False,
                )

        self.assertEqual(len(session.responses), 1)

    def test_optional_request_can_return_authorization_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            transport.session = FakeSession([FakeResponse(403)])
            transport.base_url = "http://192.168.1.1"
            transport.connection_type = "Локальная сеть"
            transport.connected = True
            transport.reauthenticate = lambda: False  # type: ignore[method-assign]

            response = transport.request(
                method="POST",
                path="/rci/interface/down",
                endpoint="interface/down",
                required=False,
                timeout=1.0,
                return_error_response=True,
                retry_transient=False,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 403)

    def test_optional_get_404_is_cached_without_unstable_state(self) -> None:
        diagnostics = DiagnosticCollector()
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(
                Path(directory), diagnostics=diagnostics
            )
            session = FakeSession([FakeResponse(404)])
            transport.session = session
            transport.base_url = "http://192.168.1.1"
            transport.connection_type = "Локальная сеть"
            transport.connected = True
            transport._state = "connected"

            first = transport.get_json(
                endpoint="show/ip/hotspot",
                required=False,
            )
            second = transport.get_json(
                endpoint="show/ip/hotspot",
                required=False,
            )

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertEqual(session.responses, [])
        self.assertTrue(
            transport.endpoint_is_known_unsupported(
                "show/ip/hotspot"
            )
        )
        self.assertEqual(transport.consecutive_failures, 0)
        self.assertEqual(transport.connection_state(), "connected")
        self.assertEqual(
            [item["type"] for item in diagnostics.events],
            ["unsupported_endpoint"],
        )
        self.assertFalse(
            any(
                item.get("new_state") == "unstable"
                for item in diagnostics.connection_attempts
            )
        )
        transport.close(clear_target=True)
        self.assertFalse(
            transport.endpoint_is_known_unsupported(
                "show/ip/hotspot"
            )
        )

    def test_required_get_404_remains_a_connection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = self.make_transport(Path(directory))
            transport.session = FakeSession([FakeResponse(404)])
            transport.base_url = "http://192.168.1.1"
            transport.connection_type = "Локальная сеть"
            transport.connected = True
            transport._state = "connected"

            with self.assertRaises(KeeneticConnectionError):
                transport.get_json(
                    endpoint="show/system",
                    required=True,
                )

        self.assertEqual(transport.consecutive_failures, 1)
        self.assertEqual(transport.connection_state(), "unstable")

    def test_password_hash_is_stable(self) -> None:
        value = RciTransport.create_password_hash(
            username="admin",
            password="password",
            realm="Keenetic",
            challenge="challenge",
        )
        self.assertEqual(len(value), 64)
        self.assertEqual(
            value,
            RciTransport.create_password_hash(
                username="admin",
                password="password",
                realm="Keenetic",
                challenge="challenge",
            ),
        )


if __name__ == "__main__":
    unittest.main()
