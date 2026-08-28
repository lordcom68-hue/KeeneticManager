from __future__ import annotations

import unittest
from unittest.mock import patch

from core.services.dedicated_wan_lifecycle_service import (
    DedicatedWanLifecycleService,
)


class FakeDedicatedWanClient:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.batches: list[list[dict[str, object]]] = []
        self.commands: list[str] = []
        self.priorities: list[dict[str, int]] = []

    def get_rci_data(self, **_kwargs: object) -> dict[str, object]:
        return self.config

    @staticmethod
    def collect_included_interfaces(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @staticmethod
    def truthy(value: object) -> bool:
        return value in {True, "yes", "true"}

    @staticmethod
    def quote_cli_argument(value: object) -> str:
        return str(value)

    def execute_rci_batch(
        self,
        commands: list[dict[str, object]],
        **_kwargs: object,
    ) -> tuple[bool, str]:
        self.batches.append(commands)
        return True, "ok"

    def execute_parse_command(self, command: str) -> tuple[bool, str]:
        self.commands.append(command)
        return True, "ok"

    def set_internet_priorities(
        self,
        priorities: dict[str, int],
    ) -> tuple[bool, str]:
        self.priorities.append(priorities)
        return True, "ok"


class DedicatedWanLifecycleServiceTests(unittest.TestCase):
    @patch("core.services.dedicated_wan_lifecycle_service.time.sleep")
    def test_verify_active_and_deleted_happy_paths(self, _sleep: object) -> None:
        active_client = FakeDedicatedWanClient(
            {
                "Bridge0": {"include": []},
                "GigabitEthernet1": {
                    "ip": {"global": {"order": 1}},
                    "security-level": {"public": True},
                },
            }
        )
        active = DedicatedWanLifecycleService(active_client)
        self.assertTrue(
            active.verify_dedicated_wan_active(
                transport_id="GigabitEthernet1",
                bridge_id="Bridge0",
                expected_order=1,
            )[0]
        )

        deleted_client = FakeDedicatedWanClient(
            {
                "Bridge0": {"include": ["GigabitEthernet1"]},
                "GigabitEthernet1": {"ip": {}, "security-level": "private"},
                "ISP": {"ip": {"global": {"order": 0}}},
            }
        )
        deleted = DedicatedWanLifecycleService(deleted_client)
        self.assertTrue(
            deleted.verify_dedicated_wan_deleted(
                source_id="GigabitEthernet1",
                transport_id="GigabitEthernet1",
                bridge_id="Bridge0",
                remaining_priorities={"ISP": 0},
            )[0]
        )

    def test_rollbacks_restore_bridge_priorities_and_state(self) -> None:
        client = FakeDedicatedWanClient(
            {
                "PPPoE0": {},
                "GigabitEthernet1": {},
            }
        )
        service = DedicatedWanLifecycleService(client)
        success, _message = service.rollback_dedicated_wan_creation(
            bridge_id="Bridge0",
            transport_id="GigabitEthernet1",
            old_priorities={"ISP": 0},
            target_interface_id="PPPoE0",
        )
        self.assertTrue(success)
        self.assertEqual(len(client.batches), 2)
        self.assertEqual(client.priorities[-1], {"ISP": 0})

        success, _message = service.rollback_dedicated_wan_deletion(
            context={
                "interface_id": "GigabitEthernet1",
                "transport_id": "GigabitEthernet1",
                "bridge_id": "Bridge0",
                "was_up": True,
                "ping_profile": "",
            },
            old_priorities={"ISP": 0},
        )
        self.assertTrue(success)
        self.assertIn("interface GigabitEthernet1 up", client.commands)


if __name__ == "__main__":
    unittest.main()
