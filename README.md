# Kalshi Agent

Automated trading agent targeting evidence-backed Kalshi prediction-market inefficiencies.

- Evidence base: [docs/Prediction_Markets_Kalshi_Literature_Review.docx](docs/Prediction_Markets_Kalshi_Literature_Review.docx)
- Build plan: [docs/Kalshi_Agent_Build_Spec.md](docs/Kalshi_Agent_Build_Spec.md)

## Setup

```
uv sync
```

Copy `.env.example` to `.env` and fill in your Kalshi API key ID and private key path
(the private key PEM file itself goes in `secrets/`, which is gitignored — never commit
key material or `.env`).

## Layout

```
src/kalshi_agent/
  data/        Kalshi REST client, auth signing, rate limiting, the market poller/backfill, DB models
  risk/        fee model, Kelly sizing, guardrails (caps, circuit breaker, master switch)
  strategy/    calibration curve, signals, decision (signal -> sized order), backtest harness
  execution/   PAPER execution adapter (no LIVE order-placement exists yet, by design)
  ledger/      audit log, portfolio/position tracking from the fill history
  dashboard/   Streamlit app (account, positions, data status) + its data-fetching logic
scripts/       run_poller.bat (Windows scheduled task target), run_backtest.py
docs/          the two source documents above
tests/         one test file per src module
```

Run the dashboard: `uv run streamlit run src/kalshi_agent/dashboard/app.py`
Run the backtest: `uv run python scripts/run_backtest.py`
Run tests: `uv run pytest`

## Status

Phase 0 (data layer) and Phase 1 (risk guardrails + PAPER execution + fee model) are done
and tested. Real Kalshi market/price history and resolved-outcome data are collected
locally (capped at 500MB). The S2 favorite-longshot calibration signal is implemented and
backtested against real data — see docs for the phased roadmap. No LIVE order-placement
capability exists anywhere in the codebase; that's deferred to an explicitly human-armed
later phase.
