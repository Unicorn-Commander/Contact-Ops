"""External contact connector implementations."""

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
