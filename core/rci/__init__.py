"""RCI infrastructure for Keenetic Manager."""

from .capabilities import RouterCapabilities
from .schema import RCI_ENDPOINTS, EndpointDefinition
from .diagnostics import DiagnosticCollector

__all__ = [
    "DiagnosticCollector",
    "EndpointDefinition",
    "RCI_ENDPOINTS",
    "RouterCapabilities",
]
