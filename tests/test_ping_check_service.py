from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from core.services.ping_check_service import (
    PingCheckService,
    PingCheckSnapshot,
)


class FakeClient:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.calls: list[tuple[str, bool]] = []

    def get_rci_data(self, *, endpoint: str, required: bool) -> Any:
        self.calls.append((endpoint, required))
        return self.data.get(endpoint, {})

    @staticmethod
    def get_interface_cli_name(interface_id: str) -> str:
        return interface_id

    @staticmethod
    def quote_cli_argument(value: Any) -> str:
        text = str(value)
        return '"' + text.replace('"', '\\"') + '"'


class PingCheckServiceTests(unittest.TestCase):
    def build(self) -> tuple[FakeClient, PingCheckService]:
        client = FakeClient()
        return client, PingCheckService(client)

    def test_snapshot_reads_two_endpoints(self) -> None:
        client, service = self.build()
        client.data = {
            'show/sc/ping-check': {'default': {'mode': 'tls'}},
            'show/ping-check': {'default': {'interface': {}}},
        }
        snapshot = service.read_snapshot()
        self.assertIsInstance(snapshot, PingCheckSnapshot)
        self.assertIn('default', snapshot.config)
        self.assertIn('default', snapshot.status)
        self.assertEqual(client.calls, [
            ('show/sc/ping-check', False),
            ('show/ping-check', False),
        ])

    def test_profile_name_is_stable_and_interface_specific(self) -> None:
        first = PingCheckService.ping_check_profile_name('Ethernet0')
        self.assertEqual(first, PingCheckService.ping_check_profile_name('Ethernet0'))
        self.assertTrue(first.startswith('_KM_'))
        self.assertNotEqual(first, PingCheckService.ping_check_profile_name('Ethernet1'))

    def test_find_nested_profile(self) -> None:
        data = {'result': [{'profile': 'custom', 'mode': 'icmp'}]}
        self.assertEqual(
            PingCheckService.find_ping_check_profile(data, 'custom')['mode'],
            'icmp',
        )

    def test_scalar_and_hosts_accept_nested_shapes(self) -> None:
        details = {
            'mode': {'value': 'tls'},
            'host': [{'address': 'a.example'}, {'host': 'b.example'}],
        }
        self.assertEqual(PingCheckService.ping_check_scalar(details, ('mode',), ''), 'tls')
        self.assertEqual(PingCheckService.ping_check_hosts(details), ['a.example', 'b.example'])

    def test_automatic_profile_detection(self) -> None:
        details = {
            'mode': 'tls',
            'port': 443,
            'host': ['google.com', 'facebook.com', 'yahoo.com'],
        }
        self.assertTrue(PingCheckService.is_automatic_ping_check_profile(details))

    def test_runtime_status_normalization(self) -> None:
        self.assertEqual(PingCheckService.normalize_ping_check_runtime_status('success'), 'pass')
        self.assertEqual(PingCheckService.normalize_ping_check_runtime_status('offline'), 'fail')
        self.assertEqual(PingCheckService.normalize_ping_check_runtime_status('checking'), 'pending')
        self.assertEqual(PingCheckService.normalize_ping_check_runtime_status('weird'), 'unknown')

    def test_disabled_runtime_is_explicit(self) -> None:
        runtime = PingCheckService.extract_ping_check_runtime({}, '', 'Ethernet0', False)
        self.assertEqual(runtime['status'], 'disabled')
        self.assertEqual(runtime['success_count'], 0)

    def test_runtime_extracts_counts_and_addresses(self) -> None:
        status = {
            'custom': {
                'interface': {
                    'Ethernet0': {
                        'status': 'pass',
                        'successcount': 7,
                        'failcount': 2,
                        'ignore-fail': True,
                        'ipcache': [{'addresses': ['1.1.1.1', '8.8.8.8']}],
                    }
                }
            }
        }
        runtime = PingCheckService.extract_ping_check_runtime(status, 'custom', 'Ethernet0', True)
        self.assertEqual(runtime['status'], 'pass')
        self.assertEqual(runtime['success_count'], 7)
        self.assertEqual(runtime['fail_count'], 2)
        self.assertTrue(runtime['ignore_fail'])
        self.assertEqual(runtime['addresses'], ['1.1.1.1', '8.8.8.8'])

    def test_extract_disabled_settings(self) -> None:
        settings = PingCheckService.extract_ping_check_settings({}, {}, {})
        self.assertFalse(settings['enabled'])
        self.assertEqual(settings['mode'], 'disabled')

    def test_extract_manual_settings(self) -> None:
        config_item = {'ping-check': {'profile': 'custom'}}
        config = {'custom': {'mode': 'connect', 'host': 'example.org', 'port': 443, 'update-interval': 15, 'max-fails': 4}}
        settings = PingCheckService.extract_ping_check_settings(config_item, config, {})
        self.assertTrue(settings['enabled'])
        self.assertEqual(settings['mode'], 'connect')
        self.assertEqual(settings['host'], 'example.org')
        self.assertEqual(settings['port'], 443)
        self.assertEqual(settings['interval'], 15)
        self.assertEqual(settings['max_fails'], 4)

    def test_build_disabled_commands(self) -> None:
        _client, service = self.build()
        commands, profile = service.build_ping_check_commands('Ethernet0', {'mode': 'disabled'}, '_KM_old')
        self.assertEqual(profile, '')
        self.assertIn('no ping-check profile', commands[0])
        self.assertEqual(len(commands), 2)

    def test_build_automatic_commands(self) -> None:
        _client, service = self.build()
        commands, profile = service.build_ping_check_commands('Ethernet0', {'mode': 'automatic'}, '')
        self.assertEqual(profile, 'default')
        self.assertIn('ping-check profile default', commands[0])

    def test_build_icmp_commands(self) -> None:
        _client, service = self.build()
        commands, profile = service.build_ping_check_commands(
            'Ethernet0',
            {'mode': 'icmp', 'host': '1.1.1.1', 'interval': 10, 'max_fails': 3},
            '',
        )
        self.assertTrue(profile.startswith('_KM_'))
        joined = '\n'.join(commands)
        self.assertIn('mode icmp', joined)
        self.assertIn('no port', joined)
        self.assertIn('host "1.1.1.1"', joined)

    def test_build_connect_commands_include_port(self) -> None:
        _client, service = self.build()
        commands, _profile = service.build_ping_check_commands(
            'Ethernet0',
            {'mode': 'connect', 'host': 'example.org', 'port': 443, 'interval': 20, 'max_fails': 5},
            '',
        )
        self.assertIn('port 443', '\n'.join(commands))

    def test_invalid_mode_raises_domain_error_not_name_error(self) -> None:
        from core.services.errors import KeeneticError
        _client, service = self.build()
        with self.assertRaises(KeeneticError):
            service.build_ping_check_commands('Ethernet0', {'mode': 'invalid'}, '')

    def test_assigned_profile_shapes(self) -> None:
        self.assertEqual(PingCheckService.assigned_ping_profile({'ping-check': {'profile': 'a'}}), 'a')
        self.assertEqual(PingCheckService.assigned_ping_profile({'ping-check': 'b'}), 'b')
        self.assertEqual(PingCheckService.assigned_ping_profile({}), '')

    def test_enrich_connection_adds_settings_runtime_and_history(self) -> None:
        _client, service = self.build()
        connection = {'interface_id': 'Ethernet0', 'status': 'up', 'role': 'primary'}
        config_item = {'up': True, 'ping-check': {'profile': 'custom'}}
        config_data = {'custom': {'mode': 'icmp', 'host': '1.1.1.1', 'update-interval': 10, 'max-fails': 3}}
        status_data = {'custom': {'interface': {'Ethernet0': {'status': 'pass', 'successcount': 1, 'failcount': 0}}}}
        service.enrich_connection(
            connection=connection,
            interface_id='Ethernet0',
            interface_item={'state': 'up', 'link': 'up', 'connected': 'yes'},
            config_item=config_item,
            config_data=config_data,
            status_data=status_data,
        )
        self.assertTrue(connection['ping_check_enabled'])
        self.assertEqual(connection['ping_check_runtime_status'], 'pass')
        self.assertEqual(connection['ping_check_success_count'], 1)
        self.assertIn('ping_check_state_reason', connection)
        self.assertIn('Ethernet0', service.runtime_cache)


class ClientFacadeTests(unittest.TestCase):
    def test_client_constructs_service_and_cache_alias(self) -> None:
        from core.keenetic_client import KeeneticClient
        client = KeeneticClient(username='admin', password='secret')
        self.assertIsInstance(client.ping_check_service, PingCheckService)
        self.assertIs(client._ping_check_runtime_cache, client.ping_check_service.runtime_cache)

    def test_build_facade_delegates(self) -> None:
        from core.keenetic_client import KeeneticClient
        client = KeeneticClient(username='admin', password='secret')
        calls: list[tuple[str, dict[str, Any], str]] = []
        def fake(interface_id: str, settings: dict[str, Any], current_profile: str):
            calls.append((interface_id, settings, current_profile))
            return ['ok'], 'profile'
        client.ping_check_service.build_ping_check_commands = fake  # type: ignore[method-assign]
        self.assertEqual(client.build_ping_check_commands('Ethernet0', {'mode': 'icmp'}, ''), (['ok'], 'profile'))
        self.assertEqual(calls[0][0], 'Ethernet0')


class SourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    @staticmethod
    def method_span(path: Path, class_name: str, method_name: str) -> int:
        module = ast.parse(path.read_text(encoding='utf-8'))
        class_node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == class_name)
        method = next(n for n in class_node.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
        return int(method.end_lineno) - int(method.lineno) + 1

    def test_large_history_and_command_methods_are_physically_moved(self) -> None:
        service = self.root / 'core' / 'services' / 'ping_check_service.py'
        client = self.root / 'core' / 'keenetic_client.py'
        self.assertGreater(self.method_span(service, 'PingCheckService', 'update_ping_check_runtime_history'), 120)
        self.assertLess(self.method_span(client, 'KeeneticClient', 'update_ping_check_runtime_history'), 8)
        self.assertGreater(self.method_span(service, 'PingCheckService', 'build_ping_check_commands'), 140)
        self.assertLess(self.method_span(client, 'KeeneticClient', 'build_ping_check_commands'), 8)

    def test_refresh_and_enrichment_call_service_directly(self) -> None:
        source = (self.root / 'core' / 'keenetic_client.py').read_text(encoding='utf-8')
        self.assertIn('self.ping_check_service.read_snapshot()', source)
        self.assertIn('self.ping_check_service.enrich_connection(', source)

    def test_wired_service_uses_ping_service_directly(self) -> None:
        source = (self.root / 'core' / 'services' / 'wired_internet_service.py').read_text(encoding='utf-8')
        self.assertIn('self.client.ping_check_service.find_ping_check_profile', source)
        self.assertIn('self.client.ping_check_service.runtime_cache', source)
        self.assertNotIn('self.client._ping_check_runtime_cache', source)


if __name__ == '__main__':
    unittest.main()
