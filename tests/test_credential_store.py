from __future__ import annotations

import unittest

from core.credential_store import (
    CredentialNotFoundError,
    CredentialStoreUnavailableError,
    RouterCredentialStore,
)


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(
        self,
        service: str,
        account: str,
        password: str,
    ) -> None:
        self.values[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


class RouterCredentialStoreTests(unittest.TestCase):
    def test_reference_is_random_and_has_no_secret_data(self) -> None:
        store = RouterCredentialStore(backend=FakeBackend())
        first = store.new_reference()
        second = store.new_reference()

        self.assertTrue(first.startswith("router-"))
        self.assertNotEqual(first, second)
        self.assertNotIn("password", first.lower())

    def test_round_trip(self) -> None:
        backend = FakeBackend()
        store = RouterCredentialStore(backend=backend)
        reference = store.new_reference()

        store.set_password(reference, "secret")
        self.assertEqual(
            store.get_password(reference, required=True),
            "secret",
        )
        store.delete_password(reference)
        self.assertIsNone(store.get_password(reference))

    def test_required_missing_secret_raises_clear_error(self) -> None:
        store = RouterCredentialStore(backend=FakeBackend())

        with self.assertRaises(CredentialNotFoundError):
            store.get_password(
                store.new_reference(),
                required=True,
            )

    def test_legacy_service_is_read_and_copied_to_stable_service(self) -> None:
        backend = FakeBackend()
        reference = "router-existing"
        backend.set_password("Old.Router.Admin", reference, "secret")
        store = RouterCredentialStore(
            backend=backend,
            service_name="Stable.Router.Admin",
            fallback_services=("Old.Router.Admin",),
        )

        self.assertEqual(store.get_password(reference), "secret")
        self.assertEqual(
            backend.get_password("Stable.Router.Admin", reference),
            "secret",
        )

    def test_missing_backend_is_rejected(self) -> None:
        store = RouterCredentialStore(backend=None)
        store.backend = None

        with self.assertRaises(CredentialStoreUnavailableError):
            store.ensure_available()


if __name__ == "__main__":
    unittest.main()
