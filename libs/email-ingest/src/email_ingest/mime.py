from __future__ import annotations

from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage

from .errors import MalformedEmailError


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes
    content_disposition: str = "attachment"


@dataclass(frozen=True)
class ParsedEmail:
    # `headers` is a last-wins convenience view. Use `get_all(name)` when
    # a header may appear multiple times (e.g. Authentication-Results).
    headers: dict[str, str]
    all_headers: tuple[tuple[str, str], ...]
    message_id: str
    text_body: str | None
    html_body: str | None
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    raw: bytes = b""

    def get_all(self, name: str) -> list[str]:
        name_lower = name.lower()
        return [v for k, v in self.all_headers if k.lower() == name_lower]


def parse_email(raw: bytes) -> ParsedEmail:
    try:
        msg: EmailMessage = message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    except Exception as exc:
        raise MalformedEmailError(f"could not parse email: {exc}") from exc

    all_headers = tuple((str(k), str(v)) for k, v in msg.items())
    headers = {k: v for k, v in all_headers}
    message_id = str(msg["Message-ID"] or "").strip("<>").strip()

    text_body: str | None = None
    html_body: str | None = None
    attachments: list[Attachment] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or "inline").lower()
        filename = part.get_filename()

        if filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(Attachment(
                filename=filename,
                content_type=content_type,
                content=payload,
                content_disposition=disposition,
            ))
        elif content_type == "text/plain" and text_body is None:
            text_body = part.get_content()
        elif content_type == "text/html" and html_body is None:
            html_body = part.get_content()

    return ParsedEmail(
        headers=headers,
        all_headers=all_headers,
        message_id=message_id,
        text_body=text_body,
        html_body=html_body,
        attachments=tuple(attachments),
        raw=raw,
    )
