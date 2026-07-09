from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    kalshi_api_key_id: str
    kalshi_private_key_path: Path
    kalshi_env: Literal["demo", "production"] = "demo"

    mode: Literal["PAPER", "LIVE"] = "PAPER"

    # Master enable switch (build spec §6) — nothing places an order, PAPER or
    # LIVE, unless this is explicitly true. LIVE additionally requires
    # live_armed, a second, separate flag — two switches to flip, not one.
    trading_enabled: bool = False
    live_armed: bool = False

    # Risk limits (build spec §6), expressed as fractions of current account
    # balance so they scale sensibly regardless of bankroll size. Defaults are
    # deliberately conservative per the user's stated low-to-medium risk
    # preference (2026-07-08) — well under the spec's illustrative examples.
    kelly_fraction: float = 0.125  # 1/8-Kelly, below the spec's ¼-Kelly example
    min_net_edge_dollars: float = 0.04  # per contract, above the spec's 2-3c starting point
    per_market_max_fraction: float = 0.10
    aggregate_max_fraction: float = 0.50
    correlated_event_max_fraction: float = 0.20
    daily_loss_limit_fraction: float = 0.05
    max_contracts_per_order: int = 500

    # Independent of the real account's actual balance (currently $20) so
    # paper trading can be exercised at a realistic scale before any real
    # capital is involved (build spec §7.3 "forward paper trading").
    paper_starting_balance: float = 1000.0

    database_url: str = "sqlite:///./data/kalshi_agent.db"

    # Basic tier ≈ 20 read / 10 write per second (build spec §3.1) — verify against
    # current docs.kalshi.com before relying on these for a higher-tier account.
    read_rate_limit: float = 20
    write_rate_limit: float = 10

    poll_interval_seconds: int = 60

    # Kalshi categories with a direct evidence base in the lit review (favorite-
    # longshot bias is systematic across categories, but Politics/Elections and
    # the Financials/Economics macro contracts — KXFED/KXCPI/KXRECSSNBER — are
    # what the Kalshi-specific papers actually studied). Sports is 80%+ of
    # Kalshi's volume but is an explicit research gap (build spec §10) AND the
    # category whose ~230k granular per-game markets blew the local DB past 1GB
    # in one test run — excluded from data collection until it has its own
    # favorite-longshot/closing-line study backing it (build spec idea #18).
    target_categories: tuple[str, ...] = ("Politics", "Elections", "Financials", "Economics", "Crypto")

    # Hard cap on the local SQLite file (bytes). The poller checks this each
    # cycle and refuses to track new markets once exceeded — existing tracked
    # markets keep getting price snapshots, but no new tickers are added.
    max_db_size_bytes: int = 500_000_000

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
