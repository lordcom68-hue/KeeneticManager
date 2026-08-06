from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock


_LOGGERS: dict[str, logging.Logger] = {}
_LOGGER_LOCK = Lock()


def verbose_priority_debug_enabled() -> bool:
    value = str(
        os.environ.get(
            "KEENETIC_MANAGER_PRIORITY_DEBUG",
            "",
        )
    ).strip().lower()
    return value in {
        "1",
        "true",
        "yes",
        "on",
    }


def write_priority_debug(
    path: Path,
    message: str,
    *,
    frequent: bool = False,
) -> None:
    """
    Пишет диагностическое событие с ротацией.

    Частые события мониторинга отключены по умолчанию. Для них
    нужно установить KEENETIC_MANAGER_PRIORITY_DEBUG=1.
    События реального изменения приоритетов сохраняются всегда,
    но журнал ограничен двумя мегабайтами и двумя копиями.
    """
    if frequent and not verbose_priority_debug_enabled():
        return

    try:
        resolved = str(path.resolve())

        with _LOGGER_LOCK:
            logger = _LOGGERS.get(resolved)

            if logger is None:
                path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                logger = logging.getLogger(
                    f"keenetic.priority.{abs(hash(resolved))}"
                )
                logger.setLevel(logging.DEBUG)
                logger.propagate = False

                handler = RotatingFileHandler(
                    path,
                    maxBytes=2_000_000,
                    backupCount=2,
                    encoding="utf-8",
                )
                handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(message)s"
                    )
                )
                logger.handlers.clear()
                logger.addHandler(handler)
                _LOGGERS[resolved] = logger

        logger.debug(str(message))
    except Exception:
        # Журналирование не должно влиять на управление роутером.
        pass
