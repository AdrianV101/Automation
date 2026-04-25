from __future__ import annotations


class EmailIngestError(Exception):
    pass


class ImapConnectionError(EmailIngestError):
    pass


class ImapAuthError(EmailIngestError):
    # Auth is not transient — callers should escalate immediately
    # instead of entering the reconnect backoff loop.
    pass


class MalformedEmailError(EmailIngestError):
    # For messages that passed pre-validation but are structurally broken.
    # Do NOT use for messages that are simply not-for-us.
    pass
