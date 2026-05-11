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
    news_imap_folder: str = "Folders/News"
    news_telegram_topic_id: int | None = None
    # Proton Bridge "split" mode gives each address its own IMAP user; set this
    # to news@<domain> in that case. Empty falls back to imap_user (combined mode).
    news_imap_user: str = ""
    # News daily master agent (in-daemon scheduler under launchd).
    # See ADR-005 in 01-Projects/Automation/development/decisions/.
    news_daily_master_enabled: bool = False
    news_daily_master_local_time: str = "06:00"
    news_daily_master_backfill_days: int = 3
    news_daily_master_model: str = "claude-opus-4-7"
    # IANA tz (e.g. "Europe/London"). Empty falls back to system localtime,
    # then UTC. Set explicitly when running on hosts without /etc/localtime
    # or to override for testing.
    news_daily_master_tz: str = ""
    news_daily_telegram_topic_id: int | None = None
    # News personal digest — chained after the daily master when enabled.
    news_personal_digest_enabled: bool = False
    news_personal_digest_model: str = "claude-opus-4-7"
    news_personal_digest_feedback_window_days: int = 7
    news_personal_digest_min_items: int = 4
    news_personal_digest_max_items: int = 12
    # Hacker News source (daily HTTP poll of Firebase HN API).
    # See 01-Projects/Automation/development/designs/2026-05-01-news-source-hacker-news-design.md.
    hacker_news_enabled: bool = False
    hacker_news_local_time: str = "05:30"
    hacker_news_min_points: int = 100
    hacker_news_max_items: int = 25
    hacker_news_topstories_pool: int = 50
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
        news_imap_folder = os.environ.get("NEWS_IMAP_FOLDER", "Folders/News")
        raw_news_topic = os.environ.get("NEWS_TELEGRAM_TOPIC_ID")
        news_telegram_topic_id: int | None = (
            int(raw_news_topic) if raw_news_topic else None
        )
        news_imap_user = os.environ.get("NEWS_IMAP_USER", "")

        news_daily_master_enabled = (
            os.environ.get("NEWS_DAILY_MASTER_ENABLED", "false").lower() == "true"
        )
        news_daily_master_local_time = os.environ.get(
            "NEWS_DAILY_MASTER_LOCAL_TIME", "06:00",
        )
        # Validate format HH:MM with hour 0-23, minute 0-59.
        if news_daily_master_enabled:
            try:
                from datetime import time as _time
                _h, _m = news_daily_master_local_time.split(":", 1)
                _time(int(_h), int(_m))
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"NEWS_DAILY_MASTER_LOCAL_TIME must be HH:MM in 24h "
                    f"format, got {news_daily_master_local_time!r}: {exc}",
                ) from exc
        news_daily_master_backfill_days = typed_env(
            "NEWS_DAILY_MASTER_BACKFILL_DAYS", "3", int,
        )
        news_daily_master_model = os.environ.get(
            "NEWS_DAILY_MASTER_MODEL", "claude-opus-4-7",
        )
        news_daily_master_tz = os.environ.get("NEWS_DAILY_MASTER_TZ", "")
        raw_news_daily_topic = os.environ.get("NEWS_DAILY_TELEGRAM_TOPIC_ID")
        news_daily_telegram_topic_id: int | None = (
            int(raw_news_daily_topic) if raw_news_daily_topic else None
        )

        news_personal_digest_enabled = (
            os.environ.get("NEWS_PERSONAL_DIGEST_ENABLED", "false").lower() == "true"
        )
        news_personal_digest_model = os.environ.get(
            "NEWS_PERSONAL_DIGEST_MODEL", "claude-opus-4-7",
        )
        news_personal_digest_feedback_window_days = typed_env(
            "NEWS_PERSONAL_DIGEST_FEEDBACK_WINDOW_DAYS", "7", int,
        )
        news_personal_digest_min_items = typed_env(
            "NEWS_PERSONAL_DIGEST_MIN_ITEMS", "4", int,
        )
        news_personal_digest_max_items = typed_env(
            "NEWS_PERSONAL_DIGEST_MAX_ITEMS", "12", int,
        )

        hacker_news_enabled = (
            os.environ.get("HACKER_NEWS_ENABLED", "false").lower() == "true"
        )
        hacker_news_local_time = os.environ.get(
            "HACKER_NEWS_LOCAL_TIME", "05:30",
        )
        if hacker_news_enabled:
            try:
                from datetime import time as _time
                _h, _m = hacker_news_local_time.split(":", 1)
                _time(int(_h), int(_m))
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"HACKER_NEWS_LOCAL_TIME must be HH:MM in 24h format, "
                    f"got {hacker_news_local_time!r}: {exc}",
                ) from exc
        hacker_news_min_points = typed_env(
            "HACKER_NEWS_MIN_POINTS", "100", int,
        )
        hacker_news_max_items = typed_env(
            "HACKER_NEWS_MAX_ITEMS", "25", int,
        )
        hacker_news_topstories_pool = typed_env(
            "HACKER_NEWS_TOPSTORIES_POOL", "50", int,
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
            news_imap_user=news_imap_user,
            news_daily_master_enabled=news_daily_master_enabled,
            news_daily_master_local_time=news_daily_master_local_time,
            news_daily_master_backfill_days=news_daily_master_backfill_days,
            news_daily_master_model=news_daily_master_model,
            news_daily_master_tz=news_daily_master_tz,
            news_daily_telegram_topic_id=news_daily_telegram_topic_id,
            news_personal_digest_enabled=news_personal_digest_enabled,
            news_personal_digest_model=news_personal_digest_model,
            news_personal_digest_feedback_window_days=news_personal_digest_feedback_window_days,
            news_personal_digest_min_items=news_personal_digest_min_items,
            news_personal_digest_max_items=news_personal_digest_max_items,
            hacker_news_enabled=hacker_news_enabled,
            hacker_news_local_time=hacker_news_local_time,
            hacker_news_min_points=hacker_news_min_points,
            hacker_news_max_items=hacker_news_max_items,
            hacker_news_topstories_pool=hacker_news_topstories_pool,
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
        if self.news_personal_digest_min_items < 1:
            raise ValueError(
                f"news_personal_digest_min_items must be >= 1, "
                f"got {self.news_personal_digest_min_items}"
            )
        if self.news_personal_digest_max_items < self.news_personal_digest_min_items:
            raise ValueError(
                f"news_personal_digest_max_items "
                f"({self.news_personal_digest_max_items}) must be >= "
                f"news_personal_digest_min_items "
                f"({self.news_personal_digest_min_items})"
            )
        if self.news_personal_digest_feedback_window_days < 1:
            raise ValueError(
                f"news_personal_digest_feedback_window_days must be >= 1, "
                f"got {self.news_personal_digest_feedback_window_days}"
            )
