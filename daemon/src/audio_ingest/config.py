from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from telegram_interface import BotConfig

log = logging.getLogger(__name__)

# Backward-compat alias: notifications.py / extraction.py used TelegramConfig,
# which had the same fields (bot_token, chat_id) as BotConfig.
TelegramConfig = BotConfig

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class DaemonConfig:
    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # PKM
    pkm_vault_path: Path = Path(".")
    # Email ingestion (Proton Bridge → IMAP IDLE)
    email_ingest_enabled: bool = False
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    imap_user: str = ""
    imap_password: str = ""
    email_ingest_state_db_path: Path = Path("./email_ingest_state.db")
    vault_attachments_subdir: str = "99-Attachments/plaud"
    # News ingestion: separate IMAP IDLE connection to the same Bridge,
    # parallel `news_ingest_events` table, `uidnext:news` settings key.
    news_ingest_enabled: bool = False
    news_imap_folder: str = "News"
    news_telegram_topic_id: int | None = None
    # Proton Mail Bridge on localhost requires STARTTLS upgrade and uses a
    # self-signed cert; defaults match that canonical deployment.
    imap_use_starttls: bool = True
    imap_ssl_verify: bool = False
    # DKIM verification trusts Authentication-Results headers written by this
    # authserv-id (the MTA's identifier). Matches Proton Mail Bridge's default.
    dkim_trusted_authserv_id: str = "mail.protonmail.ch"
    dkim_required_domain: str = "plaud.ai"
    # Session capture: append a devlog entry after each successful extraction.
    # OFF by default. Adds a second SDK pass per recording -- collect dogfood
    # cost data before keeping this on long-term.
    enable_session_capture: bool = False
    # Agent runtime safety
    agent_inactivity_timeout_s: float = 600.0
    max_concurrent_dispatch: int = 4

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> DaemonConfig:
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        def require(key: str) -> str:
            val = os.environ.get(key)
            if not val:
                raise ValueError(f"Missing required env var: {key}")
            return val

        def typed_env(key: str, default: str, type_fn: type) -> int | float:
            raw = os.environ.get(key, default)
            try:
                return type_fn(raw)
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Invalid value for {key}={raw!r}: {e}"
                ) from e

        email_ingest_enabled = os.environ.get("EMAIL_INGEST_ENABLED", "false").lower() == "true"
        imap_host = os.environ.get("IMAP_HOST", "127.0.0.1")
        imap_ssl_verify = os.environ.get("IMAP_SSL_VERIFY", "false").lower() == "true"
        enable_session_capture = os.environ.get("ENABLE_SESSION_CAPTURE", "false").lower() == "true"

        # The insecure default (ssl_verify=False) is only safe because traffic
        # stays on loopback. Reject remote hosts unless the operator
        # explicitly opts into cert verification.
        if not imap_ssl_verify and imap_host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"IMAP_SSL_VERIFY=false is only permitted for loopback hosts, "
                f"got IMAP_HOST={imap_host!r}. Set IMAP_SSL_VERIFY=true.",
            )

        agent_inactivity_timeout_s = typed_env(
            "AGENT_INACTIVITY_TIMEOUT_S", "600", float,
        )
        max_concurrent_dispatch = typed_env(
            "MAX_CONCURRENT_DISPATCH", "4", int,
        )

        news_ingest_enabled = os.environ.get(
            "NEWS_INGEST_ENABLED", "false",
        ).lower() == "true"
        news_imap_folder = os.environ.get("NEWS_IMAP_FOLDER", "News")
        raw_news_topic = os.environ.get("NEWS_TELEGRAM_TOPIC_ID")
        news_telegram_topic_id: int | None = (
            int(raw_news_topic) if raw_news_topic else None
        )

        return cls(
            telegram_bot_token=require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=require("TELEGRAM_CHAT_ID"),
            pkm_vault_path=Path(require("PKM_VAULT_PATH")),
            email_ingest_enabled=email_ingest_enabled,
            imap_host=imap_host,
            imap_port=typed_env("IMAP_PORT", "1143", int),
            imap_user=os.environ.get("IMAP_USER", ""),
            imap_password=os.environ.get("IMAP_PASSWORD", ""),
            email_ingest_state_db_path=Path(
                os.environ.get("EMAIL_INGEST_STATE_DB_PATH", "./email_ingest_state.db"),
            ),
            vault_attachments_subdir=os.environ.get(
                "VAULT_ATTACHMENTS_SUBDIR", "99-Attachments/plaud",
            ),
            imap_use_starttls=os.environ.get("IMAP_USE_STARTTLS", "true").lower() == "true",
            imap_ssl_verify=imap_ssl_verify,
            dkim_trusted_authserv_id=os.environ.get(
                "DKIM_TRUSTED_AUTHSERV_ID", "mail.protonmail.ch",
            ),
            dkim_required_domain=os.environ.get("DKIM_REQUIRED_DOMAIN", "plaud.ai"),
            enable_session_capture=enable_session_capture,
            agent_inactivity_timeout_s=agent_inactivity_timeout_s,
            max_concurrent_dispatch=max_concurrent_dispatch,
            news_ingest_enabled=news_ingest_enabled,
            news_imap_folder=news_imap_folder,
            news_telegram_topic_id=news_telegram_topic_id,
        )

    def __post_init__(self) -> None:
        if self.agent_inactivity_timeout_s <= 0:
            raise ValueError(
                f"agent_inactivity_timeout_s must be positive, "
                f"got {self.agent_inactivity_timeout_s}"
            )
        if self.max_concurrent_dispatch < 1:
            raise ValueError(
                f"max_concurrent_dispatch must be >= 1, "
                f"got {self.max_concurrent_dispatch}"
            )
