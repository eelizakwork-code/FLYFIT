import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    asana_pat: str
    allowed_telegram_user_ids: frozenset[int]
    asana_workspace_gid: str | None


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part))
    return frozenset(ids)


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    pat = os.environ.get("ASANA_PAT", "").strip()
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    ws = os.environ.get("ASANA_WORKSPACE_GID", "").strip() or None

    if not token:
        raise RuntimeError("В .env нет TELEGRAM_BOT_TOKEN")
    if not pat:
        raise RuntimeError("В .env нет ASANA_PAT")
    if not allowed_raw:
        raise RuntimeError("В .env нет TELEGRAM_ALLOWED_USER_IDS (хотя бы один id)")

    return Settings(
        telegram_bot_token=token,
        asana_pat=pat,
        allowed_telegram_user_ids=_parse_allowed_ids(allowed_raw),
        asana_workspace_gid=ws,
    )
