from __future__ import annotations

from .auth import DkimFail, DkimPass, DkimResult, verify_dkim
from .bridge import BridgeConfig, ImapBridge
from .errors import (
    EmailIngestError,
    ImapAuthError,
    ImapConnectionError,
    MalformedEmailError,
)
from .listener import ImapIdleListener, OnNewEmail, OnPersistentFailure
from .mime import Attachment, ParsedEmail, parse_email
from .state import EmailIngestStateDB, EmailIngestStatusTracker

__all__ = [
    "Attachment",
    "BridgeConfig",
    "DkimFail",
    "DkimPass",
    "DkimResult",
    "EmailIngestError",
    "EmailIngestStateDB",
    "EmailIngestStatusTracker",
    "ImapAuthError",
    "ImapBridge",
    "ImapConnectionError",
    "ImapIdleListener",
    "MalformedEmailError",
    "OnNewEmail",
    "OnPersistentFailure",
    "ParsedEmail",
    "parse_email",
    "verify_dkim",
]
