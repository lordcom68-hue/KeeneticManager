from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PortEngine:
    """
    Сбор физических Ethernet- и USB-портов Keenetic.

    Поддерживает модели, где:
    - отдельный Ethernet-контроллер обслуживает физический WAN;
    - Ethernet-контроллер с признаком Switch обслуживает LAN-порты;
    - физический WAN можно переназначить в домашний сегмент;
    - любой LAN-порт можно назначить интернет-портом.

    Для исключения дублей используются только прямые записи
    верхнего уровня с type == "Port". Вложенные копии из поля
    родительского интерфейса `port` не обрабатываются.
    """

    def __init__(
        self,
        interface_data: Any,
        usb_data: Any = None,
    ) -> None:
        self.interface_data = interface_data
        self.usb_data = usb_data

    def build(self) -> list[dict[str, Any]]:
        ports = self._build_ethernet_ports()
        ports.extend(self._build_usb_ports())

        ports.sort(
            key=self._sort_key
        )
        return ports

    # ---------------------------------------------------------
    # Ethernet
    # ---------------------------------------------------------

    def _build_ethernet_ports(
        self,
    ) -> list[dict[str, Any]]:
        interface_map = self._interface_map()

        parents: dict[str, Mapping[str, Any]] = {
            str(key): value
            for key, value in interface_map.items()
            if isinstance(value, Mapping)
        }

        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for _, raw_item in interface_map.items():
            if not isinstance(
                raw_item,
                Mapping,
            ):
                continue

            item = dict(raw_item)

            if (
                str(
                    item.get("type")
                    or ""
                ).strip().lower()
                != "port"
            ):
                continue

            traits = self._traits(item)

            if "ethernetport" not in traits:
                continue

            port_id = str(
                item.get("id")
                or ""
            ).strip()

            if not port_id:
                continue

            # Прямая запись одного физического порта может
            # встретиться только один раз.
            if port_id.lower() in seen_ids:
                continue

            seen_ids.add(
                port_id.lower()
            )

            parent_id = port_id.split(
                "/",
                1,
            )[0]

            parent = parents.get(
                parent_id,
                {},
            )

            physical_name = (
                self._physical_port_name(
                    item=item,
                    parent=parent,
                    port_id=port_id,
                )
            )

            assignment = (
                self._port_assignment(
                    item=item,
                    parent=parent,
                )
            )

            result.append(
                {
                    "id": port_id,
                    "kind": "ethernet",
                    "name": physical_name,
                    "physical_name": physical_name,
                    "role": assignment,
                    "enabled": self._is_enabled(
                        item
                    ),
                    "connected": self._is_link_up(
                        item
                    ),
                    "speed_mbps": self._positive_number(
                        item.get("speed")
                    ),
                    "duplex": self._clean_text(
                        item.get("duplex")
                    ),
                    "interface_id": port_id,
                    "description": (
                        self._assignment_description(
                            physical_name,
                            assignment,
                        )
                    ),
                    "auto_negotiation": (
                        self._clean_text(
                            item.get(
                                "auto-negotiation"
                            )
                        )
                    ),
                    "flow_control": (
                        self._clean_text(
                            item.get(
                                "flow-control"
                            )
                        )
                    ),
                    "eee": self._clean_text(
                        item.get("eee")
                    ),
                    "last_change": self._positive_number(
                        item.get("last-change")
                    ),
                    "cable_diagnostics": item.get(
                        "cable-diagnostics"
                    ),
                    "link_group": item.get(
                        "link-group"
                    ),
                    "link_state": self._clean_text(
                        item.get("link")
                    ),
                    "public": bool(
                        item.get("public")
                    ),
                    "index": item.get("index"),
                    "label": self._clean_text(
                        item.get("label")
                    ),
                    "parent_id": parent_id,
                }
            )

        return result

    def _interface_map(
        self,
    ) -> dict[str, Any]:
        if not isinstance(
            self.interface_data,
            Mapping,
        ):
            return {}

        nested = self.interface_data.get(
            "interface"
        )

        if isinstance(nested, Mapping):
            return dict(nested)

        return dict(self.interface_data)

    def _physical_port_name(
        self,
        *,
        item: Mapping[str, Any],
        parent: Mapping[str, Any],
        port_id: str,
    ) -> str:
        """
        Определяет маркировку физического разъёма.

        Контроллер с trait Switch содержит физические LAN-порты.
        Отдельный контроллер без Switch содержит физический WAN.
        """
        parent_traits = self._traits(
            parent
        )

        if "switch" not in parent_traits:
            return "WAN"

        label = self._clean_text(
            item.get("label")
            or item.get(
                "interface-name"
            )
        )

        if label and label.isdigit():
            return f"LAN {int(label)}"

        index = item.get("index")

        try:
            return (
                f"LAN {int(index) + 1}"
            )
        except (TypeError, ValueError):
            pass

        suffix = port_id.rsplit(
            "/",
            1,
        )[-1]

        if suffix.isdigit():
            return (
                f"LAN {int(suffix) + 1}"
            )

        return port_id

    def _port_assignment(
        self,
        *,
        item: Mapping[str, Any],
        parent: Mapping[str, Any],
    ) -> str:
        """
        Определяет текущее назначение порта независимо от
        физической маркировки разъёма.
        """
        if self._has_internet_role(item):
            return "WAN"

        if bool(item.get("public")):
            return "WAN"

        # Если родитель включён в Bridge, физический WAN
        # переназначен в домашнюю или гостевую сеть.
        if self._belongs_to_bridge(
            parent
        ):
            return "LAN"

        if self._parent_is_internet(
            parent
        ):
            return "WAN"

        # Порты встроенного коммутатора по умолчанию LAN.
        if "switch" in self._traits(parent):
            return "LAN"

        # Отдельный физический WAN без активной IP-конфигурации
        # всё равно остаётся назначенным как WAN, если имя ISP.
        parent_name = self._clean_text(
            parent.get(
                "interface-name"
            )
        ).lower()

        if parent_name in {
            "isp",
            "wan",
            "internet",
        }:
            return "WAN"

        return "LAN"

    @staticmethod
    def _has_internet_role(
        item: Mapping[str, Any],
    ) -> bool:
        role_data = item.get("role")

        if isinstance(role_data, str):
            return (
                role_data.strip().lower()
                in {
                    "inet",
                    "internet",
                    "wan",
                }
            )

        if isinstance(role_data, list):
            for role_item in role_data:
                if isinstance(
                    role_item,
                    Mapping,
                ):
                    role = str(
                        role_item.get("role")
                        or ""
                    ).strip().lower()
                else:
                    role = str(
                        role_item
                    ).strip().lower()

                if role in {
                    "inet",
                    "internet",
                    "wan",
                }:
                    return True

        return False

    @staticmethod
    def _belongs_to_bridge(
        parent: Mapping[str, Any],
    ) -> bool:
        group = str(
            parent.get("group")
            or ""
        ).strip().lower()

        if group.startswith("bridge"):
            return True

        used_by = parent.get("usedby")

        if isinstance(used_by, list):
            return any(
                str(value)
                .strip()
                .lower()
                .startswith("bridge")
                for value in used_by
            )

        return False

    @staticmethod
    def _parent_is_internet(
        parent: Mapping[str, Any],
    ) -> bool:
        if bool(parent.get("global")):
            return True

        if bool(parent.get("defaultgw")):
            return True

        security = str(
            parent.get(
                "security-level"
            )
            or ""
        ).strip().lower()

        if security == "public":
            return True

        return False

    @staticmethod
    def _assignment_description(
        physical_name: str,
        assignment: str,
    ) -> str:
        if physical_name == assignment:
            return ""

        return (
            f"Назначение: {assignment}"
        )

    # ---------------------------------------------------------
    # USB
    # ---------------------------------------------------------

    def _build_usb_ports(
        self,
    ) -> list[dict[str, Any]]:
        if not isinstance(
            self.usb_data,
            (Mapping, list),
        ):
            return []

        result: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in self._walk_dicts(
            self.usb_data
        ):
            manufacturer = self._clean_text(
                item.get("vendor")
                or item.get("manufacturer")
            )
            product = self._clean_text(
                item.get("product")
                or item.get("model")
                or item.get("name")
            )
            serial = self._clean_text(
                item.get("serial")
            )

            if not any(
                (
                    manufacturer,
                    product,
                    serial,
                )
            ):
                continue

            key = (
                serial.lower()
                or (
                    f"{manufacturer.lower()}:"
                    f"{product.lower()}"
                )
            )

            if key in seen:
                continue

            seen.add(key)

            result.append(
                {
                    "id": (
                        serial
                        or product
                        or "USB"
                    ),
                    "kind": "usb",
                    "name": (
                        product
                        or "USB-устройство"
                    ),
                    "physical_name": "USB",
                    "role": self._usb_role(
                        item
                    ),
                    "enabled": True,
                    "connected": True,
                    "speed_mbps": (
                        self._positive_number(
                            item.get("speed")
                        )
                    ),
                    "duplex": None,
                    "interface_id": "",
                    "manufacturer": manufacturer,
                    "serial": serial,
                    "filesystem": (
                        self._clean_text(
                            item.get(
                                "filesystem"
                            )
                            or item.get("fs")
                        )
                    ),
                    "description": "",
                }
            )

        return result

    @staticmethod
    def _usb_role(
        item: Mapping[str, Any],
    ) -> str:
        blob = " ".join(
            str(value)
            for value in item.values()
        ).lower()

        if "printer" in blob:
            return "USB-принтер"

        if any(
            marker in blob
            for marker in (
                "modem",
                "lte",
                "qmi",
                "ndis",
            )
        ):
            return "USB-модем"

        if any(
            marker in blob
            for marker in (
                "storage",
                "disk",
                "filesystem",
                "mass",
            )
        ):
            return "USB-накопитель"

        return "USB"

    # ---------------------------------------------------------
    # Common helpers
    # ---------------------------------------------------------

    @staticmethod
    def _traits(
        item: Mapping[str, Any],
    ) -> set[str]:
        traits = item.get("traits")

        if not isinstance(traits, list):
            return set()

        return {
            str(value)
            .strip()
            .lower()
            for value in traits
        }

    @staticmethod
    def _is_link_up(
        item: Mapping[str, Any],
    ) -> bool:
        return (
            str(
                item.get("link")
                or ""
            ).strip().lower()
            == "up"
        )

    @staticmethod
    def _is_enabled(
        item: Mapping[str, Any],
    ) -> bool:
        summary = item.get("summary")

        if isinstance(summary, Mapping):
            layer = summary.get("layer")

            if isinstance(layer, Mapping):
                conf = str(
                    layer.get("conf")
                    or ""
                ).strip().lower()

                if conf == "disabled":
                    return False

        return True

    @staticmethod
    def _positive_number(
        value: Any,
    ) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if number <= 0:
            return None

        return number

    @staticmethod
    def _clean_text(
        value: Any,
    ) -> str:
        if value is None:
            return ""

        text = str(value).strip()

        if text in {
            "",
            "-",
            "—",
            "None",
            "null",
        }:
            return ""

        return text

    @staticmethod
    def _walk_dicts(
        value: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        def walk(item: Any) -> None:
            if isinstance(item, Mapping):
                result.append(dict(item))

                for child in item.values():
                    walk(child)

            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return result

    @staticmethod
    def _sort_key(
        port: Mapping[str, Any],
    ) -> tuple[int, int, str]:
        kind = str(
            port.get("kind")
            or ""
        )

        if kind == "usb":
            return (
                2,
                0,
                str(
                    port.get("name")
                    or ""
                ),
            )

        physical_name = str(
            port.get("physical_name")
            or ""
        )

        if physical_name == "WAN":
            return (0, 0, "")

        try:
            number = int(
                physical_name.split()[-1]
            )
        except (ValueError, IndexError):
            number = 999

        return (
            1,
            number,
            physical_name,
        )
