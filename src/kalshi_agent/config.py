from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    kalshi_api_key_id: str
    kalshi_private_key_path: Path
    kalshi_env: Literal["demo", "production"] = "demo"

    # Real-world-data signals (added 2026-07-09 at user request). NWS weather
    # data is free/keyless. FRED (economic indicators) needs a free key from
    # https://fred.stlouisfed.org/docs/api/api_key.html — unset by default,
    # and anything depending on it should degrade gracefully rather than error.
    fred_api_key: str | None = None

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

    # S2 favorite-longshot calibration (build spec Phase 1 / ranked idea #5).
    # min_samples=20 was tested against real settled-market data 2026-07-09
    # and produced a bucket that looked like a 40% win rate on 8/20 samples,
    # then went 0-for-32 out of sample — pure small-sample noise, made worse
    # by residual correlation between nearby-but-distinct events (e.g. many
    # BTC-threshold markets a few hours apart still track one price trend).
    # Raised to 200 so only buckets with real statistical power are trusted;
    # in practice that means the well-populated extreme-price buckets, which
    # is exactly where the literature says the bias concentrates anyway.
    calibration_bucket_width: float = 0.05
    calibration_min_samples: int = 200

    # Time-horizon preference (user directive 2026-07-09): favor shorter-
    # dated contracts. Markets closing within short_term_horizon_days trade
    # at the normal min_net_edge_dollars bar; longer-dated ones must clear
    # long_term_edge_multiplier x that bar to be taken at all — a soft bias,
    # not a hard cutoff (see strategy/horizon.py, strategy/decision.py).
    short_term_horizon_days: float = 7.0
    long_term_edge_multiplier: float = 2.0

    # Alpha-realization exit for long-term positions (user directive
    # 2026-07-09): once a long-term position's current price has captured
    # this fraction of the edge estimated at entry, close it rather than
    # holding to resolution — frees capital and avoids weeks/months of tail
    # resolution risk. Short-term positions are simply held to expiry.
    # See strategy/exit.py.
    alpha_realized_exit_fraction: float = 0.7

    # Independent of the real account's actual balance (currently $20) so
    # paper trading can be exercised at a realistic scale before any real
    # capital is involved (build spec §7.3 "forward paper trading").
    paper_starting_balance: float = 1000.0

    database_url: str = "sqlite:///./data/kalshi_agent.db"

    # Build spec §3.1 cited Basic tier as ≈20 read/sec, but a real per-series
    # settled-market backfill 2026-07-08 got 429'd on nearly every request at
    # that rate — actual limit is evidently lower (or burstier) than
    # documented. Backed off to 10 to stop wasting round-trips on retries;
    # re-verify against current docs.kalshi.com if this still 429s a lot.
    read_rate_limit: float = 10
    write_rate_limit: float = 10

    # A full open-market sweep across ~22k tracked markets took ~700s
    # (~12 min) in real testing 2026-07-09 at the 10/sec rate limit — a 60s
    # interval would mean cycles overlap/never catch up. Set comfortably
    # above the observed sweep time.
    poll_interval_seconds: int = 1800

    # Kalshi categories with a direct evidence base in the lit review (favorite-
    # longshot bias is systematic across categories, but Politics/Elections and
    # the Financials/Economics macro contracts — KXFED/KXCPI/KXRECSSNBER — are
    # what the Kalshi-specific papers actually studied). Sports is 80%+ of
    # Kalshi's volume but is an explicit research gap (build spec §10) AND the
    # category whose ~230k granular per-game markets blew the local DB past 1GB
    # in one test run — excluded from data collection until it has its own
    # favorite-longshot/closing-line study backing it (build spec idea #18).
    # "Climate and Weather" added 2026-07-09 to start collecting real
    # weather-market data — needed to verify the WeatherSignal ticker-parsing
    # layer (strategy/external/weather.py) against actual Kalshi examples,
    # since none existed in the local DB before this change.
    target_categories: tuple[str, ...] = ("Politics", "Elections", "Financials", "Economics", "Crypto", "Climate and Weather")

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
