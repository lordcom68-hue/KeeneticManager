from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationState(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class OperationResult:
    """Typed outcome for router mutations and their verification."""

    state: OperationState
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.state is OperationState.CONFIRMED

    @property
    def accepted(self) -> bool:
        return self.state in {
            OperationState.CONFIRMED,
            OperationState.UNKNOWN,
        }

    def as_legacy_tuple(self) -> tuple[bool, str]:
        """Preserve existing UI signals while services migrate gradually."""
        return self.accepted, self.message

    @classmethod
    def confirmed_result(
        cls,
        message: str,
        **details: Any,
    ) -> OperationResult:
        return cls(OperationState.CONFIRMED, message, details)

    @classmethod
    def rejected_result(
        cls,
        message: str,
        **details: Any,
    ) -> OperationResult:
        return cls(OperationState.REJECTED, message, details)

    @classmethod
    def unknown_result(
        cls,
        message: str,
        **details: Any,
    ) -> OperationResult:
        return cls(OperationState.UNKNOWN, message, details)

    @classmethod
    def partial_result(
        cls,
        message: str,
        **details: Any,
    ) -> OperationResult:
        return cls(OperationState.PARTIAL, message, details)

    @classmethod
    def rolled_back_result(
        cls,
        message: str,
        **details: Any,
    ) -> OperationResult:
        return cls(OperationState.ROLLED_BACK, message, details)
