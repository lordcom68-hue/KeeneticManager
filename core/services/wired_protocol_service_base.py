from __future__ import annotations

from typing import Any


class WiredProtocolServiceBase:
    """Общие безопасные операции проводных протоколов."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _deletion_priorities(
        self,
        interface_config: dict[str, Any],
        interface_id: str,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        old_priorities = (
            self.client.priority_service.current_global_orders(
                interface_config
            )
        )
        ordered_remaining = [
            current_id
            for current_id, _order in sorted(
                old_priorities.items(),
                key=lambda item: item[1],
            )
            if current_id != interface_id
        ]
        remaining_priorities = {
            current_id: order
            for order, current_id in enumerate(ordered_remaining)
        }
        pre_delete_priorities = dict(remaining_priorities)
        pre_delete_priorities[interface_id] = len(remaining_priorities)
        return (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        )

    def _forget_interfaces(self, *interface_ids: str) -> None:
        for interface_id in interface_ids:
            self.client._manually_disabled_interfaces.discard(interface_id)
            self.client.traffic_service.reset(interface_id)
            self.client.ping_check_service.runtime_cache.pop(
                interface_id,
                None,
            )

    def _protocol_method(
        self,
        method_pattern: str,
        protocol_slug: str,
    ) -> Any:
        return getattr(
            self.client,
            method_pattern.format(protocol=protocol_slug),
        )

    def _invoke_protocol_interface_method(
        self,
        method_pattern: str,
        protocol_slug: str,
        interface_id: str,
        **kwargs: Any,
    ) -> Any:
        kwargs[f"{protocol_slug}_id"] = interface_id
        return self._protocol_method(
            method_pattern,
            protocol_slug,
        )(**kwargs)

    def _delete_wired_protocol(
        self,
        interface_id: str,
        *,
        protocol_name: str,
        protocol_slug: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.client.session is None or self.client.base_url is None:
            return False, "Нет активной сессии с роутером.", {}

        name = str(interface_id or "").strip()

        if not name:
            return False, f"Не указан интерфейс {protocol_name}.", {}

        current_runtime = self.client.get_rci_data(
            endpoint="show/interface",
            required=False,
        )
        current_config = self.client.get_rci_data(
            endpoint="show/sc/interface",
            required=False,
        )

        if not isinstance(current_config, dict):
            return (
                False,
                "Не удалось прочитать конфигурацию интерфейсов.",
                {},
            )

        context, reason = self._protocol_method(
            "inspect_wired_{protocol}_deletion",
            protocol_slug,
        )(
            interface_id=name,
            interface_config=current_config,
            interface_runtime=current_runtime,
        )

        if not context:
            return (
                False,
                reason
                or f"Это {protocol_name} нельзя безопасно удалить автоматически.",
                {},
            )

        transport_id = str(context["transport_id"])
        (
            old_priorities,
            remaining_priorities,
            pre_delete_priorities,
        ) = self._deletion_priorities(current_config, name)

        protocol_arg = self.client.quote_cli_argument(name)
        transport_arg = self.client.quote_cli_argument(transport_id)
        port_arg = self.client.quote_cli_argument(str(context["port_id"]))
        ping_profile = str(
            context.get("ping_profile") or ""
        ).strip()
        manager_profile = ping_profile.startswith("_KM_")

        prepare_commands: list[dict[str, Any]] = [
            {"parse": f"interface {protocol_arg} no up"},
            {"parse": f"interface {transport_arg} no up"},
        ]

        if manager_profile:
            prepare_commands.append(
                {
                    "parse": (
                        f"interface {protocol_arg} "
                        "no ping-check profile"
                    )
                }
            )

        prepare_commands.extend(
            (
                {"parse": f"interface {port_arg} no role"},
                {
                    "parse": (
                        f"interface {port_arg} switchport mode access"
                    )
                },
                {
                    "parse": (
                        f"interface {port_arg} switchport access vlan "
                        f"{context['home_vlan_id']}"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            )
        )

        prepared, prepare_message = self.client.execute_rci_batch(
            prepare_commands,
            operation_name=f"Не удалось подготовить {protocol_name} к удалению",
        )

        if not prepared:
            rollback_success, rollback_message = (
                self._protocol_method(
                    "rollback_wired_{protocol}_deletion",
                    protocol_slug,
                )(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{prepare_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        if pre_delete_priorities:
            success, message = self.client.priority_service.set_internet_priorities(
                pre_delete_priorities
            )

            if not success:
                rollback_success, rollback_message = (
                    self._protocol_method(
                    "rollback_wired_{protocol}_deletion",
                    protocol_slug,
                )(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    "Не удалось перестроить приоритеты: "
                    f"{message}. Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, verify_message = (
            self._invoke_protocol_interface_method(
                "verify_wired_{protocol}_delete_preparation",
                protocol_slug,
                name,
                                transport_id=transport_id,
                port_id=str(context["port_id"]),
                home_vlan_id=int(context["home_vlan_id"]),
                manager_profile_detached=manager_profile,
            )
        )

        if not verified:
            rollback_success, rollback_message = (
                self._protocol_method(
                    "rollback_wired_{protocol}_deletion",
                    protocol_slug,
                )(
                    context=context,
                    old_priorities=old_priorities,
                )
            )
            return (
                False,
                f"{verify_message} Откат: {rollback_message}",
                {"rollback_success": rollback_success},
            )

        deleted, delete_message = self.client.execute_rci_batch(
            [
                {"parse": f"no interface {protocol_arg}"},
                {"parse": f"no interface {transport_arg}"},
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name=f"Не удалось удалить {protocol_name} и транспорт",
        )

        if not deleted:
            after_delete = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            protocol_exists = (
                isinstance(after_delete, dict)
                and name in after_delete
            )
            transport_exists = (
                isinstance(after_delete, dict)
                and transport_id in after_delete
            )

            if protocol_exists and transport_exists:
                rollback_success, rollback_message = (
                    self._protocol_method(
                    "rollback_wired_{protocol}_deletion",
                    protocol_slug,
                )(
                        context=context,
                        old_priorities=old_priorities,
                    )
                )
                return (
                    False,
                    f"{delete_message} Откат: {rollback_message}",
                    {"rollback_success": rollback_success},
                )

        verified, final_message = self._invoke_protocol_interface_method(
            "verify_deleted_wired_{protocol}",
            protocol_slug,
            name,
                        transport_id=transport_id,
            port_id=str(context["port_id"]),
            home_vlan_id=int(context["home_vlan_id"]),
            remaining_priorities=remaining_priorities,
        )

        if not verified:
            final_config = self.client.get_rci_data(
                endpoint="show/sc/interface",
                required=False,
            )
            return (
                False,
                final_message,
                {
                    "rollback_success": False,
                    "interface_removed": (
                        isinstance(final_config, dict)
                        and name not in final_config
                    ),
                    "transport_removed": (
                        isinstance(final_config, dict)
                        and transport_id not in final_config
                    ),
                    "port_restored": True,
                },
            )

        cleanup_warning = ""

        if manager_profile:
            profile_config = self.client.get_rci_data(
                endpoint="show/sc/ping-check",
                required=False,
            )

            if self.client.ping_check_service.find_ping_check_profile(
                profile_config,
                ping_profile,
            ):
                profile_deleted, profile_message = (
                    self.client.execute_rci_batch(
                        [
                            {
                                "parse": (
                                    "no ping-check profile "
                                    + self.client.quote_cli_argument(
                                        ping_profile
                                    )
                                )
                            },
                            {
                                "system": {
                                    "configuration": {"save": {}}
                                }
                            },
                        ],
                        operation_name=(
                            "Не удалось удалить профиль Ping Check"
                        ),
                    )
                )

                if not profile_deleted:
                    cleanup_warning = profile_message

        self._forget_interfaces(name, transport_id)

        message = (
            f"{protocol_name} «{context['description']}» и транспорт "
            f"{transport_id} удалены. Порт {context['port_label']} "
            "возвращён в домашнюю сеть "
            f"(VLAN {context['home_vlan_id']})."
        )

        if cleanup_warning:
            message += (
                " Подключение удалено, но служебный профиль "
                "Ping Check не очищен: " + cleanup_warning
            )

        return (
            True,
            message,
            {
                "interface_id": name,
                "transport_interface_id": transport_id,
                "port_id": context["port_id"],
                "port_label": context["port_label"],
                "home_vlan_id": context["home_vlan_id"],
                "cleanup_warning": cleanup_warning,
            },
        )
