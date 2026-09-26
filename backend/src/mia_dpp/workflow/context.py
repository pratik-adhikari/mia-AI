"""Compatibility alias for the architecture-neutral runtime service container.

New code must import :class:`mia_dpp.runtime.services.ServiceContainer` directly.
This alias is retained temporarily so external callers do not break during the
incremental orchestration refactor.
"""

from mia_dpp.runtime.services import ServiceContainer

MiaContext = ServiceContainer

__all__ = ["MiaContext"]
