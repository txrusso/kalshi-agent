from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    kalshi_api_key_id: str
    kalshi_private_key_path: Path
    kalshi_env: Literal["demo", "production"] = "demo"

    mode: Literal["PAPER", "LIVE"] = "PAPER"

    database_url: str = "sqlite:///./data/kalshi_agent.db"

    # Basic tier ≈ 20 read / 10 write per second (build spec §3.1) — verify against
    # current docs.kalshi.com before relying on these for a higher-tier account.
    read_rate_limit: float = 20
    write_rate_limit: float = 10

    poll_interval_seconds: int = 60

    # Verified reachable 2026-07-08. NOTE: demo and production have separate API
    # key stores — a production key gets 401/NOT_FOUND on demo's authed endpoints
    # even though demo's public endpoints respond to anyone.
    @property
    def kalshi_rest_base(self) -> str:
        if self.kalshi_env == "production":
            return "https://api.elections.kalshi.com/trade-api/v2"
        return "https://demo-api.kalshi.co/trade-api/v2"

    @property
    def kalshi_ws_base(self) -> str:
        if self.kalshi_env == "production":
            return "wss://api.elections.kalshi.com/trade-api/ws/v2"
        return "wss://demo-api.kalshi.co/trade-api/ws/v2"

    @property
    def private_key_pem(self) -> bytes:
        return self.kalshi_private_key_path.read_bytes()


settings = Settings()
