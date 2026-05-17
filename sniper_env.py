"""
Load environment: copy_bot secrets first (single source of truth), then sniper-local .env overrides.

See plan: COPY_BOT_ENV_PATH / POLYMARKET_ENV_FILE, or default ../polymarket_copy_bot_v2/.env then env.txt.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def default_copy_bot_dir() -> Path:
    """Sibling folder polymarket_copy_bot_v2 next to polymarket_sniper_bot."""
    here = Path(__file__).resolve().parent
    return (here.parent / "polymarket_copy_bot_v2").resolve()


def load_sniper_env() -> None:
    """
    1) Optional explicit file: COPY_BOT_ENV_PATH or POLYMARKET_ENV_FILE.
    2) Else first existing among COPY_BOT_DIR/.env, COPY_BOT_DIR/env.txt (same order as polymarket_wallet_live.py).
    3) Then polymarket_sniper_bot/.env with override=True for sniper-only knobs (DRY_RUN, PRICE_THRESHOLD, …).
    """
    copy_dir = Path(os.environ.get("COPY_BOT_DIR", str(default_copy_bot_dir()))).expanduser().resolve()
    explicit = (os.environ.get("COPY_BOT_ENV_PATH") or os.environ.get("POLYMARKET_ENV_FILE") or "").strip()
    loaded_copy = False
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            load_dotenv(p, override=False)
            loaded_copy = True
    if not loaded_copy:
        for name in (".env", "env.txt"):
            p = copy_dir / name
            if p.is_file():
                load_dotenv(p, override=False)
                loaded_copy = True
                break
    local = Path(__file__).resolve().parent / ".env"
    if local.is_file():
        load_dotenv(local, override=True)
