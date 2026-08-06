from __future__ import annotations

import unittest
from unittest.mock import patch

from core.router_status_worker import RouterStatusWorker


class _FakeResponse:
    def __init__(
        self,
        status: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})

    def read(self, _limit: int) -> bytes:
        return b""

    def getheader(self, name: str) -> str | None:
        expected = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == expected:
                return value
        return None


class _FakeConnection:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def request(self, method: str, path: str, **_kwargs) -> None:
        self.requests.append((method, path))

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class RouterStatusWorkerTests(unittest.TestCase):
    def _probe(
        self,
        response: _FakeResponse,
    ) -> tuple[bool, _FakeConnection]:
        connection = _FakeConnection(response)
        worker = RouterStatusWorker(paused=True)
        with patch(
            "core.router_status_worker.http.client.HTTPSConnection",
            return_value=connection,
        ):
            result = worker._check_keendns_http(
                "https",
                "router.keenetic.pro",
                443,
            )
        return result, connection

    def test_auth_challenge_confirms_live_keenetic(self) -> None:
        result, connection = self._probe(
            _FakeResponse(
                401,
                {
                    "X-NDM-Realm": "Keenetic",
                    "X-NDM-Challenge": "challenge",
                },
            )
        )

        self.assertTrue(result)
        self.assertEqual(connection.requests, [("GET", "/auth")])
        self.assertTrue(connection.closed)

    def test_redirect_does_not_confirm_router(self) -> None:
        result, _connection = self._probe(_FakeResponse(302))
        self.assertFalse(result)

    def test_plain_401_without_keenetic_headers_is_offline(self) -> None:
        result, _connection = self._probe(_FakeResponse(401))
        self.assertFalse(result)

    def test_keendns_targets_are_https_only(self) -> None:
        worker = RouterStatusWorker(paused=True)

        self.assertEqual(
            worker._keendns_targets("router.keenetic.pro"),
            [("https", "router.keenetic.pro", 443)],
        )
        self.assertEqual(
            worker._keendns_targets("http://router.keenetic.pro"),
            [("https", "router.keenetic.pro", 443)],
        )
        self.assertEqual(
            worker._keendns_targets(
                "https://router.keenetic.pro:8443"
            ),
            [("https", "router.keenetic.pro", 8443)],
        )


if __name__ == "__main__":
    unittest.main()
