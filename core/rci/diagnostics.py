from __future__ import annotations

import copy
import json
import platform
import re
import sys
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


REDACTED = "[REDACTED]"

_SECRET_KEY_PARTS = {
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "private-key",
    "private_key",
    "preshared-key",
    "preshared_key",
    "psk",
    "auth",
    "authorization",
    "cookie",
    "session",
    "challenge",
    "credential",
}

_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN[ \t]+"
    r"(?:RSA |EC |OPENSSH |DSA )?"
    r"(?:PRIVATE KEY|ENCRYPTED PRIVATE KEY|CERTIFICATE)"
    r"-----.*?"
    r"-----END[ \t]+"
    r"(?:RSA |EC |OPENSSH |DSA )?"
    r"(?:PRIVATE KEY|ENCRYPTED PRIVATE KEY|CERTIFICATE)"
    r"-----",
    re.IGNORECASE | re.DOTALL,
)

_OPENVPN_BLOCK_RE = re.compile(
    r"<(?P<tag>"
    r"key|cert|ca|tls-auth|tls-crypt|"
    r"tls-crypt-v2|pkcs12|secret"
    r")>"
    r".*?"
    r"</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)

_INLINE_SECRET_RE = re.compile(
    r"(?im)^(\s*"
    r"(?:password|passwd|passphrase|secret|token|"
    r"auth-user-pass|http-proxy-user-pass|"
    r"private-key|preshared-key|psk)"
    r"\s*(?:=|:|\s)\s*)"
    r"(.+?)\s*$"
)

_URI_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)"
    r"(?P<user>[^/\s:@]+):"
    r"(?P<password>[^@\s/]+)@",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _safe_name(value: str) -> str:
    name = re.sub(
        r"[^a-zA-Z0-9_.-]+",
        "_",
        str(value or "").strip().strip("/"),
    )
    return name or "snapshot"


def _unique_archive_name(
    value: str,
    used_names: set[str],
) -> str:
    """Return a stable ZIP member name without duplicate entries."""
    normalized = str(value or "").replace("\\", "/").lstrip("/")
    candidate = normalized or "file"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    path = PurePosixPath(candidate)
    parent = "" if str(path.parent) == "." else f"{path.parent}/"
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    index = 2
    while True:
        alternative = f"{parent}{stem}_{index}{suffix}"
        if alternative not in used_names:
            used_names.add(alternative)
            return alternative
        index += 1


def sanitize_text(value: str) -> str:
    text = str(value)

    text = _PEM_BLOCK_RE.sub(
        "-----BEGIN REDACTED-----\n"
        f"{REDACTED}\n"
        "-----END REDACTED-----",
        text,
    )

    def replace_openvpn(
        match: re.Match[str],
    ) -> str:
        tag = match.group("tag")
        return (
            f"<{tag}>\n"
            f"{REDACTED}\n"
            f"</{tag}>"
        )

    text = _OPENVPN_BLOCK_RE.sub(
        replace_openvpn,
        text,
    )

    text = _INLINE_SECRET_RE.sub(
        lambda match: (
            f"{match.group(1)}{REDACTED}"
        ),
        text,
    )

    text = _URI_CREDENTIAL_RE.sub(
        lambda match: (
            f"{match.group('scheme')}"
            f"{match.group('user')}:"
            f"{REDACTED}@"
        ),
        text,
    )

    return text


def _key_is_sensitive(key: Any) -> bool:
    normalized = (
        str(key or "")
        .strip()
        .lower()
        .replace("_", "-")
    )

    if normalized in _SECRET_KEY_PARTS:
        return True

    return any(
        part in normalized
        for part in (
            "password",
            "passwd",
            "passphrase",
            "private-key",
            "preshared-key",
            "access-token",
            "refresh-token",
            "client-secret",
            "shared-secret",
        )
    )


def sanitize_value(
    value: Any,
    *,
    parent_key: str = "",
) -> Any:
    if _key_is_sensitive(parent_key):
        return REDACTED

    if isinstance(value, dict):
        return {
            str(key): sanitize_value(
                nested,
                parent_key=str(key),
            )
            for key, nested in value.items()
        }

    if isinstance(value, list):
        return [
            sanitize_value(
                nested,
                parent_key=parent_key,
            )
            for nested in value
        ]

    if isinstance(value, tuple):
        return [
            sanitize_value(
                nested,
                parent_key=parent_key,
            )
            for nested in value
        ]

    if isinstance(value, set):
        return [
            sanitize_value(
                nested,
                parent_key=parent_key,
            )
            for nested in sorted(
                value,
                key=lambda item: str(item),
            )
        ]

    if isinstance(value, str):
        return sanitize_text(value)

    return value


class DiagnosticCollector:
    """
    Собирает ограниченный журнал RCI и создаёт безопасный ZIP.

    Все снимки, события, метаданные и текстовые журналы проходят
    повторную очистку непосредственно перед записью в архив.
    """

    MAX_EVENTS = 500
    MAX_SNAPSHOTS = 64
    MAX_LOG_BYTES = 2_000_000
    MAX_CONNECTION_ATTEMPTS = 1000

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.snapshots: dict[str, Any] = {}
        self.connection_attempts: list[dict[str, Any]] = []

        # Успешные RCI-запросы происходят десятки раз за один
        # цикл мониторинга. В 0.19.3 каждый из них занимал место
        # в events.json и вытеснял полезные ошибки за считанные
        # минуты. Теперь успехи хранятся в компактной статистике,
        # а событийный журнал предназначен для ошибок и важных
        # операций.
        self.rci_success_total = 0
        self.rci_success_by_endpoint: dict[str, int] = {}
        self.first_rci_success_at: str | None = None
        self.last_rci_success_at: str | None = None
        self.last_rci_success_endpoint: str | None = None

    def clear(self) -> None:
        self.events.clear()
        self.snapshots.clear()
        self.connection_attempts.clear()
        self.rci_success_total = 0
        self.rci_success_by_endpoint.clear()
        self.first_rci_success_at = None
        self.last_rci_success_at = None
        self.last_rci_success_endpoint = None

    def record_snapshot(
        self,
        endpoint: str,
        data: Any,
    ) -> None:
        key = str(endpoint or "snapshot").strip()

        self.snapshots[key] = copy.deepcopy(data)

        while len(self.snapshots) > self.MAX_SNAPSHOTS:
            oldest = next(iter(self.snapshots))
            self.snapshots.pop(oldest, None)

    def record_rci_success(
        self,
        endpoint: str,
    ) -> None:
        """
        Учитывает успешный RCI-запрос без добавления шумного
        события в events.json.
        """
        normalized_endpoint = str(
            endpoint or "unknown"
        ).strip() or "unknown"
        timestamp = _now_iso()

        self.rci_success_total += 1
        self.rci_success_by_endpoint[normalized_endpoint] = (
            self.rci_success_by_endpoint.get(
                normalized_endpoint,
                0,
            )
            + 1
        )

        if self.first_rci_success_at is None:
            self.first_rci_success_at = timestamp

        self.last_rci_success_at = timestamp
        self.last_rci_success_endpoint = normalized_endpoint

    def statistics_dict(self) -> dict[str, Any]:
        event_type_counts: dict[str, int] = {}

        for event in self.events:
            event_type = str(
                event.get("type") or "unknown"
            )
            event_type_counts[event_type] = (
                event_type_counts.get(event_type, 0)
                + 1
            )

        return {
            "rci_success_total": self.rci_success_total,
            "rci_success_by_endpoint": dict(
                sorted(
                    self.rci_success_by_endpoint.items()
                )
            ),
            "first_rci_success_at": self.first_rci_success_at,
            "last_rci_success_at": self.last_rci_success_at,
            "last_rci_success_endpoint": (
                self.last_rci_success_endpoint
            ),
            "event_type_counts": event_type_counts,
            "connection_attempt_count": len(
                self.connection_attempts
            ),
        }

    def record_event(
        self,
        event_type: str,
        message: str,
        *,
        endpoint: str | None = None,
        details: Any = None,
    ) -> None:
        event: dict[str, Any] = {
            "timestamp": _now_iso(),
            "type": str(event_type),
            "message": str(message),
        }

        if endpoint:
            event["endpoint"] = str(endpoint)

        if details is not None:
            event["details"] = copy.deepcopy(details)

        self.events.append(event)

        if len(self.events) > self.MAX_EVENTS:
            del self.events[
                : len(self.events) - self.MAX_EVENTS
            ]

    def record_exception(
        self,
        error: BaseException,
        *,
        endpoint: str | None = None,
        message: str | None = None,
    ) -> None:
        self.record_event(
            "exception",
            message or str(error),
            endpoint=endpoint,
            details={
                "exception_type": (
                    type(error).__name__
                ),
                "exception": str(error),
            },
        )

    def record_connection_attempt(
        self,
        attempt: dict[str, Any],
    ) -> None:
        """Сохраняет ограниченную историю сетевых попыток."""
        self.connection_attempts.append(
            copy.deepcopy(attempt)
        )

        if (
            len(self.connection_attempts)
            > self.MAX_CONNECTION_ATTEMPTS
        ):
            del self.connection_attempts[
                : len(self.connection_attempts)
                - self.MAX_CONNECTION_ATTEMPTS
            ]

    @staticmethod
    def _capabilities_dict(
        capabilities: Any,
    ) -> dict[str, Any]:
        if capabilities is None:
            return {}

        if is_dataclass(capabilities):
            return asdict(capabilities)

        if isinstance(capabilities, dict):
            return dict(capabilities)

        to_dict = getattr(
            capabilities,
            "to_dict",
            None,
        )

        if callable(to_dict):
            result = to_dict()

            if isinstance(result, dict):
                return result

        result: dict[str, Any] = {}

        for name in (
            "usb",
            "storage",
            "wireguard",
            "openvpn",
            "zerotier",
            "mobile",
            "smb",
        ):
            if hasattr(capabilities, name):
                result[name] = bool(
                    getattr(capabilities, name)
                )

        return result

    @staticmethod
    def _resolve_archive_path(
        destination: str | Path,
    ) -> Path:
        path = Path(destination).expanduser()

        if (
            path.suffix.lower() == ".zip"
            and not path.is_dir()
        ):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            return path

        path.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = (
            datetime.now()
            .astimezone()
            .strftime("%Y-%m-%d_%H-%M-%S")
        )

        return (
            path
            / f"keenetic_diagnostic_{timestamp}.zip"
        )

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        safe = sanitize_value(value)

        return json.dumps(
            safe,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")

    def create_archive(
        self,
        destination: str | Path,
        *,
        metadata: dict[str, Any] | None = None,
        capabilities: Any = None,
        application_logs: list[
            str | Path
        ] | None = None,
        extra_json_files: dict[str, Any] | None = None,
    ) -> Path:
        archive_path = self._resolve_archive_path(
            destination
        )

        safe_metadata = sanitize_value(
            dict(metadata or {})
        )
        safe_capabilities = sanitize_value(
            self._capabilities_dict(
                capabilities
            )
        )

        statistics = sanitize_value(
            self.statistics_dict()
        )

        manifest = {
            "created_at": _now_iso(),
            "python": sys.version,
            "platform": platform.platform(),
            "metadata": safe_metadata,
            "capabilities": safe_capabilities,
            "snapshot_count": len(
                self.snapshots
            ),
            "event_count": len(
                self.events
            ),
            "connection_attempt_count": len(
                self.connection_attempts
            ),
            "statistics": statistics,
            "security": {
                "redaction_version": 2,
                "vpn_embedded_secrets_removed": True,
            },
        }

        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            used_names: set[str] = set()

            def write_json(filename: str, value: Any) -> None:
                archive.writestr(
                    _unique_archive_name(filename, used_names),
                    self._json_bytes(value),
                )

            write_json("manifest.json", manifest)
            write_json("events.json", self.events)
            write_json("statistics.json", statistics)
            write_json(
                "connection_attempts.json",
                self.connection_attempts,
            )

            for filename, value in (
                extra_json_files or {}
            ).items():
                safe_filename = str(filename or "").strip()
                if not safe_filename:
                    continue
                if not safe_filename.lower().endswith(".json"):
                    safe_filename += ".json"
                write_json(
                    safe_filename.replace("\\", "/"),
                    value,
                )

            for endpoint, snapshot in (
                self.snapshots.items()
            ):
                filename = (
                    _safe_name(endpoint)
                    .replace("/", "_")
                    .replace("\\", "_")
                )

                write_json(
                    f"snapshots/{filename}.json",
                    snapshot,
                )

            for log_value in application_logs or []:
                log_path = Path(log_value)

                if (
                    not log_path.is_file()
                    or log_path.stat().st_size <= 0
                ):
                    continue

                with log_path.open(
                    "rb"
                ) as stream:
                    raw = stream.read(
                        self.MAX_LOG_BYTES
                    )

                text = raw.decode(
                    "utf-8",
                    errors="replace",
                )
                safe_text = sanitize_text(text)

                archive.writestr(
                    _unique_archive_name(
                        "logs/" + _safe_name(log_path.name),
                        used_names,
                    ),
                    safe_text.encode("utf-8"),
                )

        return archive_path
