"""External contact connector implementations.

Exports stay lazy so operational ``python -m contact_ops.connectors.*`` entry
points can establish their own secret-safe error boundary before importing the
application Settings/database graph. Direct submodule imports remain unchanged.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contact_ops.connectors.base import Connector, ConnectorRunResult
    from contact_ops.connectors.gmail import GmailConnector
    from contact_ops.connectors.icloud import ICloudConnector
    from contact_ops.connectors.m365 import M365Connector

__all__ = [
    "Connector",
    "ConnectorRunResult",
    "GmailConnector",
    "ICloudConnector",
    "M365Connector",
]

_EXPORTS = {
    "Connector": ("contact_ops.connectors.base", "Connector"),
    "ConnectorRunResult": ("contact_ops.connectors.base", "ConnectorRunResult"),
    "GmailConnector": ("contact_ops.connectors.gmail", "GmailConnector"),
    "ICloudConnector": ("contact_ops.connectors.icloud", "ICloudConnector"),
    "M365Connector": ("contact_ops.connectors.m365", "M365Connector"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
