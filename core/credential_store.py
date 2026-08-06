from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from core.app_profile import (
    LEGACY_ROUTER_CREDENTIAL_SERVICES,
    ROUTER_CREDENTIAL_SERVICE,
)

try:
    import keyring
    from keyring import errors as keyring_errors
except ImportError:  # pragma: no cover - обрабатывается понятной ошибкой.
    keyring = None
    keyring_errors = None


class CredentialStoreError(RuntimeError):
    """Базовая ошибка безопасного хранилища учётных данных."""


class CredentialStoreUnavailableError(CredentialStoreError):
    """Системное хранилище недоступно или keyring не установлен."""


class CredentialNotFoundError(CredentialStoreError):
    """В базе есть ссылка, но соответствующий секрет отсутствует."""


class CredentialMigrationError(CredentialStoreError):
    """Не удалось перенести пароль из старой SQLite-базы."""


@dataclass(frozen=True)
class CredentialLocation:
    service: str
    account: str


class RouterCredentialStore:
    """
    Хранит пароли администраторов роутеров через пакет keyring.

    В Windows пакет keyring использует Windows Credential Manager.
    SQLite получает только случайную ссылку account и никогда не хранит
    сам пароль после миграции на формат 0.27.0.
    """

    SERVICE_NAME = ROUTER_CREDENTIAL_SERVICE
    ACCOUNT_PREFIX = "router-"

    def __init__(
        self,
        *,
        backend: Any = None,
        service_name: str | None = None,
        fallback_services: tuple[str, ...] | None = None,
    ) -> None:
        self.backend = backend if backend is not None else keyring
        self.service_name = str(
            service_name or self.SERVICE_NAME
        )
        self.fallback_services = tuple(
            service
            for service in (
                fallback_services
                if fallback_services is not None
                else LEGACY_ROUTER_CREDENTIAL_SERVICES
            )
            if service and service != self.service_name
        )

    def new_reference(self) -> str:
        return f"{self.ACCOUNT_PREFIX}{uuid4().hex}"

    def location(self, reference: str) -> CredentialLocation:
        value = str(reference or "").strip()

        if not value:
            raise ValueError(
                "Ссылка на учётные данные не может быть пустой."
            )

        return CredentialLocation(
            service=self.service_name,
            account=value,
        )

    def ensure_available(self) -> None:
        if self.backend is None:
            raise CredentialStoreUnavailableError(
                "Пакет keyring не установлен. Установите зависимости "
                "проекта и повторите запуск."
            )

        get_backend = getattr(
            self.backend,
            "get_keyring",
            None,
        )

        if not callable(get_backend):
            return

        try:
            selected = get_backend()
        except Exception as error:
            raise CredentialStoreUnavailableError(
                "Не удалось открыть системное хранилище учётных данных."
            ) from error

        try:
            priority = getattr(selected, "priority", 1)
        except Exception as error:
            raise CredentialStoreUnavailableError(
                "Для keyring не найдено рабочее системное хранилище."
            ) from error

        try:
            usable = float(priority) > 0
        except (TypeError, ValueError):
            usable = bool(priority)

        if not usable:
            raise CredentialStoreUnavailableError(
                "Для keyring не найдено рабочее системное хранилище."
            )

    def get_password(
        self,
        reference: str,
        *,
        required: bool = False,
    ) -> str | None:
        self.ensure_available()
        location = self.location(reference)

        try:
            value = self.backend.get_password(
                location.service,
                location.account,
            )
        except Exception as error:
            raise self._translate_error(
                "Не удалось прочитать пароль из Windows Credential Manager.",
                error,
            ) from error

        if value is None:
            value = self._read_legacy_password(location.account)

        if value is None and required:
            raise CredentialNotFoundError(
                "Сохранённый пароль роутера не найден в Windows "
                "Credential Manager. Откройте параметры роутера и "
                "задайте пароль повторно."
            )

        return str(value) if value is not None else None

    def _read_legacy_password(self, account: str) -> str | None:
        for service in self.fallback_services:
            try:
                value = self.backend.get_password(service, account)
            except Exception:
                continue
            if value is None:
                continue

            secret = str(value)
            try:
                self.backend.set_password(
                    self.service_name,
                    account,
                    secret,
                )
            except Exception:
                # Старый пароль всё равно можно использовать в этом запуске.
                pass
            return secret
        return None

    def set_password(
        self,
        reference: str,
        password: str,
    ) -> None:
        self.ensure_available()
        location = self.location(reference)
        secret = str(password)

        try:
            self.backend.set_password(
                location.service,
                location.account,
                secret,
            )
        except Exception as error:
            raise self._translate_error(
                "Не удалось сохранить пароль в Windows Credential Manager.",
                error,
            ) from error

    def delete_password(
        self,
        reference: str,
        *,
        missing_ok: bool = True,
    ) -> None:
        self.ensure_available()
        location = self.location(reference)

        try:
            self.backend.delete_password(
                location.service,
                location.account,
            )
        except Exception as error:
            if missing_ok and self._is_missing_password_error(error):
                return

            raise self._translate_error(
                "Не удалось удалить пароль из Windows Credential Manager.",
                error,
            ) from error

    def _translate_error(
        self,
        message: str,
        error: Exception,
    ) -> CredentialStoreError:
        if keyring_errors is not None and isinstance(
            error,
            getattr(keyring_errors, "NoKeyringError", ()),
        ):
            return CredentialStoreUnavailableError(message)

        return CredentialStoreError(message)

    @staticmethod
    def _is_missing_password_error(error: Exception) -> bool:
        if keyring_errors is None:
            return False

        password_delete_error = getattr(
            keyring_errors,
            "PasswordDeleteError",
            None,
        )

        return bool(
            password_delete_error is not None
            and isinstance(error, password_delete_error)
        )
