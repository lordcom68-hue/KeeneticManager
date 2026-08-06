from __future__ import annotations


class KeeneticError(Exception):
    """Основная ошибка при работе с Keenetic."""


class KeeneticAuthenticationError(KeeneticError):
    """Ошибка авторизации."""


class KeeneticConnectionError(KeeneticError):
    """Ошибка соединения с роутером."""
