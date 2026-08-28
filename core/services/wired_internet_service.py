from __future__ import annotations

from typing import Any

from core.services.wired_connection_dispatcher import (
    WiredConnectionDispatcher,
)
from core.services.dedicated_wan_service import DedicatedWanService
from core.services.wired_ipoe_service import WiredIpoeService
from core.services.cable_protocol_change_service import (
    CableProtocolChangeService,
)
from core.services.wired_protocol_service_base import (
    WiredProtocolServiceBase,
)
from core.services.wired_pppoe_service import WiredPppoeService
from core.services.wired_pptp_service import WiredPptpService
from core.services.wired_l2tp_service import WiredL2tpService


class WiredInternetService:
    """
    Совместимый фасад управления проводными интернет-подключениями.

    Все операции выполняются профильными сервисами; этот класс
    сохраняет единый публичный API для интерфейса и KeeneticClient.
    Низкоуровневые помощники пока доступны через KeeneticClient.
    """

    SUPPORTED_PROTOCOLS = WiredConnectionDispatcher.SUPPORTED_PROTOCOLS

    def __init__(self, client: Any) -> None:
        self.client = client
        self.dispatcher = WiredConnectionDispatcher(self)
        self.dedicated_wan_service = DedicatedWanService(client)
        self.wired_ipoe_service = WiredIpoeService(client)
        self.cable_protocol_change_service = (
            CableProtocolChangeService(client)
        )
        self._protocol_helpers = WiredProtocolServiceBase(client)
        self.pppoe_service = WiredPppoeService(client)
        self.pptp_service = WiredPptpService(client)
        self.l2tp_service = WiredL2tpService(client)

    def _deletion_priorities(
        self,
        interface_config: dict[str, Any],
        interface_id: str,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """Совместимый фасад общих помощников удаления."""
        return self._protocol_helpers._deletion_priorities(
            interface_config,
            interface_id,
        )

    def _forget_interfaces(self, *interface_ids: str) -> None:
        """Совместимый фасад очистки состояния интерфейсов."""
        self._protocol_helpers._forget_interfaces(*interface_ids)

    @staticmethod
    def protocol_type(settings: dict[str, Any]) -> str:
        return WiredConnectionDispatcher.protocol_type(settings)

    @classmethod
    def connection_type(
        cls,
        settings: dict[str, Any],
    ) -> str:
        del cls
        return WiredConnectionDispatcher.connection_type(settings)

    def create_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        return self.dispatcher.create(settings)

    @staticmethod
    def cable_settings_for_interface(
        settings: dict[str, Any],
        *,
        description: str,
    ) -> dict[str, Any]:
        return WiredConnectionDispatcher.interface_settings(
            settings,
            description=description,
        )

    def create_dedicated_cable_internet(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад DedicatedWanService."""
        return (
            self.dedicated_wan_service
            .create_dedicated_cable_internet(settings)
        )

    def delete_dedicated_wan(
        self,
        *,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад DedicatedWanService."""
        return self.dedicated_wan_service.delete_dedicated_wan(
            interface_id=interface_id,
        )

    def create_wired_pppoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredPppoeService."""
        return self.pppoe_service.create_wired_pppoe(settings)

    def create_wired_pptp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredPptpService."""
        return self.pptp_service.create_wired_pptp(settings)

    def create_wired_l2tp(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredL2tpService."""
        return self.l2tp_service.create_wired_l2tp(settings)

    def create_wired_ipoe(
        self,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredIpoeService."""
        return self.wired_ipoe_service.create_wired_ipoe(settings)

    def change_cable_internet_protocol(
        self,
        *,
        interface_id: str,
        settings: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад CableProtocolChangeService."""
        return (
            self.cable_protocol_change_service
            .change_cable_internet_protocol(
                interface_id=interface_id,
                settings=settings,
            )
        )

    def delete_wired_pppoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredPppoeService."""
        return self.pppoe_service.delete_wired_pppoe(interface_id)

    def delete_wired_pptp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredPptpService."""
        return self.pptp_service.delete_wired_pptp(interface_id)

    def delete_wired_l2tp(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredL2tpService."""
        return self.l2tp_service.delete_wired_l2tp(interface_id)

    def delete_wired_ipoe(
        self,
        interface_id: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Совместимый фасад WiredIpoeService."""
        return self.wired_ipoe_service.delete_wired_ipoe(interface_id)
