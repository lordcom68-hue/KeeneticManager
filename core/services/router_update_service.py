from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import time
from typing import Any

from core.services.errors import KeeneticError
from core.services.operation_result import OperationResult


@dataclass(frozen=True, slots=True)
class RouterUpdateInfo:
    """Normalized KeeneticOS update state returned to the GUI."""

    supported: bool
    channel: str
    installed_version: str
    available_version: str
    update_available: bool
    message: str
    checking: bool = False
    channels: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RouterUpdateService:
    """Checks and starts official component-based KeeneticOS updates."""

    CHANNEL_LABELS = {
        "stable": "Основной",
        "preview": "Предварительный",
        "beta": "Предварительный",
        "draft": "Тестовый",
        "archive": "Архив",
    }
    UNSUPPORTED_MARKERS = (
        "not found",
        "unknown command",
        "unsupported",
        "not supported",
    )

    UPDATE_CHECK_MAX_POLLS = 20
    UPDATE_CHECK_POLL_SECONDS = 0.5
    COMPONENT_LIST_MAX_POLLS = 120
    COMPONENT_LIST_POLL_SECONDS = 0.5

    def __init__(
        self,
        client: Any,
        *,
        sleep: Any = time.sleep,
    ) -> None:
        self.client = client
        self._sleep = sleep

    def check_for_updates(self) -> tuple[bool, str, dict[str, Any]]:
        current_version = self._current_version()
        success, payload, error = self.client.post_rci_query(
            {"parse": "components check-update force"},
            timeout=60,
        )
        if not success:
            unsupported = self._means_unsupported(error)
            message = (
                "Управление обновлениями не поддерживается этой версией KeeneticOS."
                if unsupported
                else f"Не удалось проверить обновления: {error}"
            )
            info = RouterUpdateInfo(
                supported=not unsupported,
                channel="",
                installed_version=current_version,
                available_version="",
                update_available=False,
                message=message,
            )
            return False, message, info.to_dict()

        self._record_update_snapshot(payload)
        for _attempt in range(self.UPDATE_CHECK_MAX_POLLS):
            info = self.normalize_status(
                payload,
                current_version=current_version,
            )
            if not info.checking:
                return True, info.message, info.to_dict()

            self._sleep(self.UPDATE_CHECK_POLL_SECONDS)
            poll_success, poll_payload, poll_error = self.client.post_rci_query(
                {"parse": "components check-update"},
                timeout=20,
            )
            if not poll_success:
                message = f"Проверка обновлений не завершена: {poll_error}"
                pending = RouterUpdateInfo(
                    supported=True,
                    channel=info.channel,
                    installed_version=info.installed_version,
                    available_version=info.available_version,
                    update_available=False,
                    message=message,
                    checking=True,
                    channels=info.channels,
                )
                return False, message, pending.to_dict()
            payload = poll_payload
            self._record_update_snapshot(payload)

        info = self.normalize_status(payload, current_version=current_version)
        message = (
            "Роутер продолжает проверять сервер обновлений. "
            "Повторите проверку через несколько секунд."
        )
        pending = RouterUpdateInfo(
            supported=info.supported,
            channel=info.channel,
            installed_version=info.installed_version,
            available_version=info.available_version,
            update_available=False,
            message=message,
            checking=True,
            channels=info.channels,
        )
        return False, message, pending.to_dict()

    def set_channel(self, channel: str) -> OperationResult:
        normalized = self.normalize_channel(channel)
        if not normalized:
            return OperationResult.rejected_result(
                "Выбран неизвестный канал обновления.",
                channel=str(channel or ""),
            )

        success, message = self.client.execute_rci_batch(
            [
                {
                    "parse": (
                        "components auto-update channel "
                        f"{normalized}"
                    )
                },
                {"system": {"configuration": {"save": {}}}},
            ],
            operation_name="Смена канала обновления",
        )
        if not success:
            return OperationResult.rejected_result(
                message or "Роутер не сохранил канал обновления.",
                channel=normalized,
            )

        label = self.channel_label(normalized)
        return OperationResult.confirmed_result(
            f"Канал обновления изменён: {label}.",
            channel=normalized,
            channel_label=label,
        )

    def install_update(self, channel: str) -> OperationResult:
        normalized = self.normalize_channel(channel)
        if not normalized:
            return OperationResult.rejected_result(
                "Перед установкой выберите поддерживаемый канал обновления.",
                channel=str(channel or ""),
            )

        try:
            stage_response = self.client.transport.request(
                method="POST",
                path="/rci/components/list",
                endpoint="components/list",
                required=True,
                timeout=max(float(self.client.timeout), 30.0),
                json_payload={"sandbox": normalized},
                allow_reauthentication=True,
                retry_transient=False,
            )
        except KeeneticError as error:
            return OperationResult.rejected_result(
                f"Не удалось подготовить обновление: {error}",
                channel=normalized,
                stage="list",
            )

        if stage_response is None:
            return OperationResult.rejected_result(
                "Роутер не вернул результат подготовки обновления.",
                channel=normalized,
                stage="list",
            )

        try:
            stage_payload: Any = stage_response.json()
        except ValueError:
            return OperationResult.rejected_result(
                "Роутер вернул неизвестный ответ при подготовке обновления.",
                channel=normalized,
                stage="list",
            )

        for _attempt in range(self.COMPONENT_LIST_MAX_POLLS):
            if not self._component_list_continued(stage_payload):
                break
            self._sleep(self.COMPONENT_LIST_POLL_SECONDS)
            try:
                poll_response = self.client.transport.request(
                    method="GET",
                    path="/rci/components/list",
                    endpoint="components/list",
                    required=True,
                    timeout=max(float(self.client.timeout), 30.0),
                    allow_reauthentication=True,
                    retry_transient=True,
                )
            except KeeneticError as error:
                return OperationResult.rejected_result(
                    f"Не удалось дождаться списка компонентов: {error}",
                    channel=normalized,
                    stage="list_wait",
                )
            if poll_response is None:
                return OperationResult.rejected_result(
                    "Роутер не вернул готовый список компонентов.",
                    channel=normalized,
                    stage="list_wait",
                )
            try:
                stage_payload = poll_response.json()
            except ValueError:
                return OperationResult.rejected_result(
                    "Роутер вернул неизвестный ответ при ожидании "
                    "списка компонентов.",
                    channel=normalized,
                    stage="list_wait",
                )
        else:
            return OperationResult.rejected_result(
                "Роутер слишком долго формирует список компонентов. "
                "Повторите установку позже.",
                channel=normalized,
                stage="list_wait",
            )

        stage_errors = self.client.extract_batch_rci_errors(stage_payload)
        if stage_errors:
            return OperationResult.rejected_result(
                "Не удалось подготовить обновление: " + "; ".join(stage_errors),
                channel=normalized,
                stage="list",
            )

        staged_version = self._firmware_version(self._parse_blocks(stage_payload))
        try:
            commit_response = self.client.transport.request(
                method="POST",
                path="/rci/",
                endpoint="components/commit",
                required=True,
                timeout=max(float(self.client.timeout), 45.0),
                json_payload=[{"parse": "components commit"}],
                allow_reauthentication=False,
                retry_transient=False,
            )
        except KeeneticError:
            return OperationResult.unknown_result(
                "Команда установки отправлена. Роутер мог уже начать обновление; "
                "ожидается перезагрузка и повторное подключение.",
                channel=normalized,
                available_version=staged_version,
                reboot_expected=True,
                response_lost=True,
            )

        if commit_response is None:
            return OperationResult.unknown_result(
                "Команда установки отправлена без итогового ответа. "
                "Ожидается перезагрузка роутера.",
                channel=normalized,
                available_version=staged_version,
                reboot_expected=True,
                response_lost=True,
            )

        try:
            commit_payload: Any = commit_response.json()
        except ValueError:
            commit_payload = commit_response.text

        commit_errors = self.client.extract_batch_rci_errors(commit_payload)
        if commit_errors:
            return OperationResult.rejected_result(
                "Установка обновления отклонена: " + "; ".join(commit_errors),
                channel=normalized,
                available_version=staged_version,
                stage="commit",
            )

        return OperationResult.unknown_result(
            "Установка KeeneticOS запущена. Не выключайте питание; "
            "приложение автоматически дождётся перезагрузки роутера.",
            channel=normalized,
            available_version=staged_version,
            reboot_expected=True,
            response_lost=False,
        )

    @classmethod
    def normalize_status(
        cls,
        payload: Any,
        *,
        current_version: str = "",
    ) -> RouterUpdateInfo:
        blocks = cls._parse_blocks(payload)
        show_block = blocks[0] if blocks else {}
        check_block = blocks[1] if len(blocks) > 1 else show_block

        channel = (
            cls._local_channel(show_block)
            or cls._channel(check_block)
            or cls._channel(show_block)
        )
        installed = cls._firmware_version([show_block]) or str(
            current_version or ""
        ).strip()
        available = cls._display_version(check_block)
        valid = cls._truthy(cls._find_value(check_block, ("valid",)))
        explicit_available = cls._find_value(
            check_block,
            ("update-available", "update_available"),
        )
        checking = cls._truthy(cls._find_value(check_block, ("checking",)))
        if explicit_available is not None:
            update_available = cls._truthy(explicit_available)
        else:
            update_available = bool(
                available
                and (
                    valid
                    or not installed
                    or cls._version_key(available) != cls._version_key(installed)
                )
            )
        channels = cls._available_channels(check_block)

        if checking:
            message = "Роутер проверяет сервер обновлений..."
        elif update_available and available:
            message = f"Доступна KeeneticOS {available}."
        elif update_available:
            message = "Роутер сообщил о доступном обновлении KeeneticOS."
        elif available and installed:
            message = f"Установлена актуальная KeeneticOS {installed}."
        elif installed:
            message = (
                f"Установлена KeeneticOS {installed}; сервер обновлений "
                "не сообщил новую версию."
            )
        else:
            message = "Состояние обновления получено, но версия не определена."

        return RouterUpdateInfo(
            supported=True,
            channel=channel,
            installed_version=installed,
            available_version=available,
            update_available=update_available,
            message=message,
            checking=checking,
            channels=channels,
        )

    def _current_version(self) -> str:
        version_data = getattr(self.client, "version_data", {})
        title = (
            version_data.get("title")
            if isinstance(version_data, dict)
            else None
        )
        if title:
            return str(title).strip()
        extractor = getattr(self.client, "extract_version", None)
        if callable(extractor):
            return str(extractor(version_data) or "")
        return ""

    @classmethod
    def normalize_channel(cls, channel: Any) -> str:
        normalized = str(channel or "").strip().lower()
        if re.fullmatch(
            r"(?:stable|preview|beta|draft|archive|lts-\d+(?:\.\d+)*)",
            normalized,
        ):
            return normalized
        return ""

    @classmethod
    def channel_label(cls, channel: str) -> str:
        normalized = cls.normalize_channel(channel)
        if normalized.startswith("lts-"):
            return f"LTS {normalized.removeprefix('lts-')}"
        return cls.CHANNEL_LABELS.get(normalized, normalized or "Не определён")

    @classmethod
    def _means_unsupported(cls, message: Any) -> bool:
        text = str(message or "").casefold()
        return any(marker in text for marker in cls.UNSUPPORTED_MARKERS)

    @staticmethod
    def _parse_blocks(payload: Any) -> list[dict[str, Any]]:
        candidates = payload if isinstance(payload, list) else [payload]
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            parsed = candidate.get("parse", candidate)
            if isinstance(parsed, dict):
                result.append(parsed)
        return result

    @classmethod
    def _local_channel(cls, data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        local = data.get("local")
        if isinstance(local, dict):
            return cls.normalize_channel(local.get("sandbox"))
        return ""

    @classmethod
    def _channel(cls, data: Any) -> str:
        return cls.normalize_channel(cls._find_value(data, ("sandbox", "channel")))

    @classmethod
    def _display_version(cls, data: Any) -> str:
        value: Any = None
        if isinstance(data, dict):
            value = data.get("title") or data.get("release")
        if value is None:
            value = cls._find_value(data, ("title",))
        if value is None:
            value = cls._find_value(data, ("release",))
        return str(value or "").strip()

    @classmethod
    def _available_channels(cls, data: Any) -> tuple[dict[str, str], ...]:
        raw = cls._find_value(data, ("sandboxes",))
        if not isinstance(raw, list):
            return ()
        channels: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = cls.normalize_channel(item.get("name"))
            if not name:
                continue
            channels.append(
                {
                    "name": name,
                    "label": cls.channel_label(name),
                    "version": str(item.get("version") or "").strip(),
                }
            )
        return tuple(channels)

    @classmethod
    def _firmware_version(cls, blocks: list[dict[str, Any]]) -> str:
        for block in blocks:
            firmware = cls._find_value(block, ("firmware",))
            if isinstance(firmware, dict):
                value = firmware.get("version") or firmware.get("release")
                if value:
                    return str(value).strip()
        return ""

    @classmethod
    def _find_value(cls, data: Any, keys: tuple[str, ...]) -> Any:
        normalized = {key.casefold() for key in keys}
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).casefold() in normalized:
                    return value
            for value in data.values():
                found = cls._find_value(value, keys)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = cls._find_value(item, keys)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() in {
            "1",
            "yes",
            "true",
            "on",
            "enabled",
        }

    @staticmethod
    def _version_key(value: str) -> str:
        return str(value or "").strip().casefold().lstrip("v")

    def _record_update_snapshot(self, payload: Any) -> None:
        diagnostics = getattr(self.client, "diagnostics", None)
        record_snapshot = getattr(diagnostics, "record_snapshot", None)
        if callable(record_snapshot):
            record_snapshot("components/check-update", payload)

    @classmethod
    def _component_list_continued(cls, payload: Any) -> bool:
        return cls._truthy(cls._find_value(payload, ("continued",)))
