from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouterActionRequest:
    """Typed command passed from GUI request methods to the monitor thread."""

    action_type: str
    payload: object = None
