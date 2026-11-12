from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.connection_worker import RouterMonitor
from core.router_action_models import RouterActionRequest
from core.services.operation_result import OperationResult


class RouterActionDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = RouterMonitor({"id": 41})

    def test_public_requests_enqueue_typed_action_model(self) -> None:
        self.monitor.request_interface_state("GigabitEthernet0/2", False)

        action = self.monitor._actions.get_nowait()

        self.assertIsInstance(action, RouterActionRequest)
        self.assertEqual(action.action_type, "interface_state")
        self.assertEqual(action.payload, ("GigabitEthernet0/2", False))

    def test_wired_delete_actions_preserve_service_and_signal_contract(self) -> None:
        calls: list[tuple[str, str]] = []
        emitted: list[tuple[Any, ...]] = []

        class WiredService:
            def __getattr__(self, name: str):
                protocol = name.removeprefix("delete_wired_")

                def delete(*, interface_id: str):
                    calls.append((protocol, interface_id))
                    return True, f"{protocol} deleted", {"protocol": protocol}

                return delete

        client = SimpleNamespace(wired_internet_service=WiredService())
        self.monitor.connection_delete_completed.connect(
            lambda *args: emitted.append(args)
        )

        for protocol in ("ipoe", "pppoe", "pptp", "l2tp"):
            refresh = self.monitor.process_action(
                client=client,  # type: ignore[arg-type]
                action_type=f"delete_wired_{protocol}",
                payload=f"{protocol.upper()}0",
            )
            self.assertTrue(refresh)

        self.assertEqual(
            calls,
            [
                ("ipoe", "IPOE0"),
                ("pppoe", "PPPOE0"),
                ("pptp", "PPTP0"),
                ("l2tp", "L2TP0"),
            ],
        )
        self.assertEqual([item[0] for item in emitted], [41] * 4)
        self.assertEqual(
            [item[1] for item in emitted],
            ["IPOE0", "PPPOE0", "PPTP0", "L2TP0"],
        )
        self.assertTrue(all(item[2] for item in emitted))

    def test_interface_state_records_and_emits_original_contract(self) -> None:
        history: list[tuple[str, str, dict[str, Any]]] = []
        emitted: list[tuple[Any, ...]] = []
        client = SimpleNamespace(
            set_interface_enabled=lambda **kwargs: (
                kwargs == {
                    "interface_id": "GigabitEthernet0/2",
                    "enabled": False,
                },
                "Порт выключен.",
            ),
            operation_history_service=SimpleNamespace(
                record=lambda event_type, message, details: history.append(
                    (event_type, message, details)
                )
            ),
        )
        self.monitor.interface_action_completed.connect(
            lambda *args: emitted.append(args)
        )

        refresh = self.monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="interface_state",
            payload=("GigabitEthernet0/2", False),
        )

        self.assertTrue(refresh)
        self.assertEqual(emitted, [(41, "GigabitEthernet0/2", True, "Порт выключен.")])
        self.assertEqual(history[0][0], "interface_state")
        self.assertEqual(history[0][2]["requested_enabled"], False)

    def test_diagnostic_action_does_not_request_monitor_refresh(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []
        emitted: list[tuple[Any, ...]] = []

        def create_archive(
            destination: str,
            *,
            application_settings: dict[str, Any],
        ) -> tuple[bool, str]:
            calls.append((destination, application_settings))
            return True, "Архив создан."

        client = SimpleNamespace(create_diagnostic_archive=create_archive)
        self.monitor.diagnostic_action_completed.connect(
            lambda *args: emitted.append(args)
        )

        refresh = self.monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="diagnostic_archive",
            payload={
                "destination": "D:/diagnostic.zip",
                "application_settings": {"developer_mode": True},
            },
        )

        self.assertFalse(refresh)
        self.assertEqual(
            calls,
            [("D:/diagnostic.zip", {"developer_mode": True})],
        )
        self.assertEqual(emitted, [(41, "create", True, "Архив создан.")])

    def test_router_update_install_enters_expected_reboot_handoff(self) -> None:
        emitted: list[tuple[Any, ...]] = []
        close_calls: list[bool] = []
        client = SimpleNamespace(
            router_update_service=SimpleNamespace(
                install_update=lambda channel: OperationResult.unknown_result(
                    "Установка запущена.",
                    channel=channel,
                    reboot_expected=True,
                )
            ),
            operation_history_service=SimpleNamespace(
                record=lambda *_args, **_kwargs: None
            ),
            close=lambda: close_calls.append(True),
        )
        self.monitor.router_update_completed.connect(
            lambda *args: emitted.append(args)
        )

        refresh = self.monitor.process_action(
            client=client,  # type: ignore[arg-type]
            action_type="router_update_install",
            payload="stable",
        )

        self.assertFalse(refresh)
        self.assertEqual(emitted[0][:4], (41, "install", True, "Установка запущена."))
        self.assertEqual(self.monitor.expected_handoff_mode(), "auto")
        self.assertEqual(close_calls, [True])

    def test_unknown_action_remains_explicit_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Неизвестное действие"):
            self.monitor.process_action(
                client=SimpleNamespace(),  # type: ignore[arg-type]
                action_type="unknown-action",
                payload=None,
            )

    def test_monitor_delegates_to_focused_action_handlers(self) -> None:
        root = Path(__file__).resolve().parents[1]

        def class_methods(path: Path, class_name: str) -> dict[str, ast.FunctionDef]:
            module = ast.parse(path.read_text(encoding="utf-8"))
            class_node = next(
                node
                for node in module.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            return {
                node.name: node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef)
            }

        monitor_methods = class_methods(
            root / "core" / "connection_worker.py",
            "RouterMonitor",
        )
        dispatcher_methods = class_methods(
            root / "core" / "router_action_dispatcher.py",
            "RouterActionDispatcher",
        )

        process_action = monitor_methods["process_action"]
        dispatch = dispatcher_methods["dispatch"]
        action_handlers = {
            name: method
            for name, method in dispatcher_methods.items()
            if name.startswith("_handle_")
        }

        self.assertLessEqual(process_action.end_lineno - process_action.lineno + 1, 15)
        self.assertLessEqual(dispatch.end_lineno - dispatch.lineno + 1, 20)
        self.assertEqual(len(action_handlers), 28)
        self.assertLessEqual(
            max(method.end_lineno - method.lineno + 1 for method in action_handlers.values()),
            75,
        )


if __name__ == "__main__":
    unittest.main()
