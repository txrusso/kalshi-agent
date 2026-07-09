# Kalshi Trading Agent — Build Spec / PRD

**Companion to:** `Prediction_Markets_Kalshi_Literature_Review.docx`
**Purpose:** Hand this to Claude Code as the build plan. It maps the literature's evidence-backed edges to concrete, prioritized software modules, and defines the data, cost model, risk guardrails, and testing harness needed before any real capital is deployed.
**Date:** July 8, 2026
**Status:** Draft v1 — verify all API/fee details against live Kalshi docs before coding (links at bottom).

---

## 1. Goal & non-goals

**Goal.** An automated agent that identifies and trades evidence-backed inefficiencies on Kalshi, sized and gated so that expected edge survives fees and spreads, with a human approving the leap from paper trading to live capital.

**Non-goals (v1).**
- Not a high-frequency latency-arb system. We do not try to win the microsecond race; ideas requiring sub-second execution are deferred.
- Not a black-box LLM that trades its own raw probabilities (see the KalshiBench result — LLMs are overconfident).
- Not a cross-chain/DeFi system. Polymarket is used only as a *read-only signal source* in v2+, not a settlement venue.

**Guiding principles (from the literature).**
1. **Fee-aware or it isn't real.** Every signal's tradable threshold = gross edge − fees − half-spread − slippage. Mispricings that look profitable gross are routinely negative net (PredictIt/IEM arbitrage evidence).
2. **Be a Maker, not a Taker.** Kalshi's own transaction data shows informed makers earn small positive returns while takers systematically lose (~−20% avg pre-fee). Post resting orders; avoid crossing the spread unless the edge clearly justifies the taker fee.
3. **Trade where the bias lives.** Inefficiency concentrates in extreme-priced contracts, long-dated horizons, and thin/newly-listed markets. Start there.
4. **Paper-trade first, always.** Confidence labels in the review reflect *evidence strength*, not proven P&L. Nothing goes live without a passing backtest + forward paper-trading period.
5. **Human-in-the-loop for go-live and kill.** The agent proposes and (once approved) executes within hard limits; a human owns the master enable switch and capital allocation.

---

## 2. High-level architecture

```
                ┌─────────────────────────────────────────────┐
                │                 Orchestrator                 │
                │  (scheduler, state machine, enable switch)   │
                └───────┬───────────────────────────┬─────────┘
                        │                           │
        ┌───────────────▼──────────┐     ┌──────────▼──────────────┐
        │      Data Layer          │     │     Strategy Layer       │
        │  - Kalshi REST poller    │     │  - Signal modules (S1..) │
        │  - Kalshi WS streams     │────▶│  - Calibration engine    │
        │  - Historical store (DB) │     │  - Fair-value estimator  │
        │  - (v2) Polymarket read  │     │  - Edge = fair − price   │
        └──────────────────────────┘     └──────────┬──────────────┘
                        ▲                           │ signals + confidence
                        │                ┌──────────▼──────────────┐
                        │                │   Decision & Sizing      │
        ┌───────────────┴──────────┐     │  - Fee/cost model        │
        │   Risk & Guardrails      │◀───▶│  - Kelly-fraction sizing │
        │  - position/exposure caps│     │  - net-edge threshold    │
        │  - kill switch, circuit  │     └──────────┬──────────────┘
        └──────────────────────────┘                │ orders
                        ▲                ┌──────────▼──────────────┐
                        └────────────────│   Execution Layer        │
                                         │  - maker-first placement │
                                         │  - order mgmt / amend     │
                                         │  - PAPER vs LIVE adapter  │
                                         └──────────┬──────────────┘
                                                    │
                                         ┌──────────▼──────────────┐
                                         │  Ledger / P&L / Logging  │
                                         │  + backtest replay       │
                                         └─────────────────────────┘
```

Keep the **Execution Layer behind a `PAPER | LIVE` adapter interface** from day one so the same strategy code runs against a simulator or the real API by flipping one flag.

---

## 3. Data layer

### 3.1 Kalshi API (verify against live docs)
- **REST base:** `https://api.elections.kalshi.com/trade-api/v2`
- **WebSocket:** `wss://api.elections.kalshi.com/trade-api/ws/v2`
- **Auth:** API-key + per-request **RSA-PSS signature** (SHA-256, MGF1-SHA256, salt = 32 bytes). Sign `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, excludes query string); send `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE` headers. Store the private key in a secret manager / env var, never in the repo.
- **Public discovery (no auth):** `/series`, `/events`, `/markets`, per-market and batched `/orderbook`.
- **Authed portfolio:** `/portfolio/balance`, `/positions`, `/settlements`, `/orders`, `/fills`, and the v2 order surface `/portfolio/events/orders` (create / amend / decrease / cancel / batched).
- **WS channels:** public `ticker`, `trade`, `market_lifecycle_v2`; private `orderbook_delta`, `fill`, `market_positions`, `user_orders`. Use `orderbook_delta` for live book state; reconcile against periodic REST snapshots.
- **Rate limits:** tiered; Basic tier ≈ 20 read / 10 write per second (2026). Build a token-bucket limiter and back off on 429s.

### 3.2 What to store (build the research asset)
Persist everything to a local DB (Postgres or SQLite→Postgres). This historical store *is* the backtesting corpus and closes several research gaps noted in the review.
- Per-market metadata: series, event, ticker, open/close/expiration timestamps, resolution rules (store the raw rule text — critical for arbitrage filtering).
- Time series: top-of-book bid/ask, mid, last, volume, open interest — snapshot at a fixed cadence (e.g., 1 min) plus event-driven WS updates.
- Full order-book depth snapshots at lower cadence for liquidity/price-impact modeling.
- Your own fills, orders, positions, and realized P&L.
- Resolution outcomes (join to markets for calibration curves).

### 3.3 (v2+) Polymarket read-only
For lead-lag and cross-venue signals, ingest Polymarket prices for *semantically matched* markets. **Do not assume equivalence** — store both platforms' resolution rules and require a validated match before treating a gap as arbitrage.

---

## 4. Strategy modules (prioritized backlog)

Each strategy is a module implementing a common interface:

```python
class Signal:
    def fair_value(self, market, book, features) -> float | None  # probability in [0,1]
    def confidence(self, ...) -> float                            # 0..1, scales sizing
    # edge = fair_value - market_price; trade only if net edge > threshold
```

### Phase 1 — Structural edges (well-established; build first)
- **S1. Maker liquidity provision.** Post resting limit orders inside the spread on liquid-enough contracts; capture maker rebate/lower fee and the maker-vs-taker return gap. Foundational money-maker per Kalshi's own data. *Pairs with everything else — it's the default execution style, not just a standalone strategy.*
- **S2. Favorite-longshot debias.** Fit a realized-frequency-vs-price calibration curve from your historical store. Fade contracts priced below ~15¢ (overpriced longshots) and lean into heavy favorites (mildly underpriced). Recalibrate rolling.
- **S3. Horizon-conditioned calibration.** Extend S2 with time-to-expiration as a feature — bias is largest far from resolution and shrinks near expiry. Overweight the trade far out, tighten as expiry approaches.

### Phase 2 — Conditional / mixed-evidence edges
- **S4. News-underreaction drift.** When a benchmark/public signal jumps, prices adjust only partially on impact; the shortfall predicts short-horizon drift, largest in low-liquidity markets. Trade the drift, but **gate hard by liquidity** so spreads don't eat it.
- **S5. Order-flow imbalance.** Net directional imbalance from large trades predicts subsequent returns. Build from your fill/trade tape.
- **S6. Cross-venue LOP arbitrage (Kalshi↔Polymarket).** Flag co-listed pairs breaching the price identity *after fees*, then **filter by validated resolution-rule equivalence** before acting. Many gaps are structural, not free money.
- **S7. New-listing / thin-market edge.** Inefficiency is largest early in a contract's life; concentrate maker activity on freshly-listed, low-depth markets; scale down as depth builds.

### Phase 3 — Speculative / research-first (validate before capital)
- **S8. Calibrated ML/LLM fair-value estimator.** Retrieval-grounded, multi-agent forecaster (AIA-Forecaster-style) producing a probability, **then a mandatory calibration layer** (isotonic/Platt fit on Kalshi history) before it can size a trade. Trade only where calibrated-p diverges from price beyond the net-edge threshold.
- **S9. Kalshi-macro-as-signal.** Use KXFED/KXCPI/KXRECSSNBER probability changes as features for *other* assets (research spinoff, not core trading).

**Do not start at Phase 3.** Ideas 8–9 are extrapolations past the peer-reviewed evidence; treat them as R&D behind a paper-trading wall.

---

## 5. Decision, sizing & the fee/cost model

### 5.1 Net-edge gate
A trade is only allowed when:
```
net_edge = fair_value_edge − est_fee − half_spread − est_slippage
trade if  net_edge > MIN_NET_EDGE   (e.g. start at 2–3¢ and tune)
```

### 5.2 Fee model (pull exact values from the live fee schedule)
- Kalshi charges **maker** fees (lower) and **taker** fees (higher); fees are roughly a per-contract function of price and are **largest near 50¢**, smallest at the extremes. Implement the *current* published formula/bps schedule — do not hardcode a guess. Maker < taker is the structural reason S1 is prioritized.
- Model settlement and withdrawal costs too. Include them in realized-P&L accounting so backtests aren't optimistic.

### 5.3 Sizing
- Use **fractional Kelly** (e.g., ¼-Kelly) on the calibrated edge, capped by the risk limits in §6. Never full Kelly — calibration error makes it ruinous.
- Scale size by `Signal.confidence` and by available book depth (don't take more than a set fraction of resting size).

---

## 6. Risk management & guardrails (build before Phase 1 strategies)

- **Master enable switch** — single flag/env that disables all live order placement instantly.
- **Mode flag** `PAPER | LIVE` — LIVE requires an explicit, logged human action to arm.
- **Per-market cap** — max contracts / max $ exposure per market.
- **Aggregate cap** — max total capital at risk; max % of bankroll.
- **Correlated-exposure cap** — limit summed exposure across markets resolving on the same underlying event (e.g., all Fed-decision contracts).
- **Loss circuit breakers** — daily and rolling drawdown limits that auto-disable trading and alert.
- **Order sanity checks** — reject orders outside 1–99¢, above size caps, or that would cross your own resting orders.
- **Rate-limit & error handling** — token-bucket limiter, exponential backoff on 429/5xx, reconcile positions via REST after any WS disconnect.
- **Idempotency** — client-side order IDs to avoid duplicate sends on retries.
- **Audit log** — every signal, decision, order, fill, and cancel persisted with timestamps and the reason/edge that triggered it.

---

## 7. Backtesting & paper-trading harness

1. **Backtest replay.** Replay the historical store through the exact strategy → decision → fee → sizing pipeline. Report net (after-fee) P&L, Sharpe, hit rate, max drawdown, turnover, and calibration of the fair-value model.
2. **Realism checks.** Assume you only get filled at prices that actually traded/were resting; charge maker vs taker fees correctly; model partial fills and queue position conservatively.
3. **Forward paper trading.** Run LIVE-data / PAPER-execution for a defined period (e.g., 2–4 weeks) and confirm live results track the backtest before arming real capital.
4. **Calibration monitoring (ongoing).** Continuously compare predicted probabilities to realized outcomes (Brier score, reliability curve). Degrading calibration auto-throttles sizing.

---

## 8. Suggested tech stack
- **Language:** Python (async — `asyncio` + `websockets`/`httpx`).
- **Data:** Postgres (+ TimescaleDB optional) for time series; Parquet for backtest snapshots.
- **Signing/auth:** `cryptography` for RSA-PSS.
- **Config:** pydantic settings; secrets via env / secret manager.
- **Testing:** pytest; a deterministic market simulator for the PAPER adapter.
- **Observability:** structured logging + a simple dashboard (P&L, positions, calibration, circuit-breaker state).

---

## 9. Phased roadmap

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 0 | Data layer + historical store + auth working | Streaming + persisting clean data for N days |
| 1 | Risk guardrails + PAPER execution adapter + fee model | Can place/cancel paper orders within all caps |
| 2 | S1–S3 strategies + backtest harness | Positive **net-of-fee** backtest on stored data |
| 3 | Forward paper trading | Live-paper P&L tracks backtest for the test window |
| 4 | Human-armed LIVE with tiny caps | Small-size live results consistent with paper |
| 5 | S4–S7 conditional edges | Each passes backtest + paper before live |
| 6 | S8–S9 R&D | Behind paper wall; promote only if validated |

---

## 10. Open questions / research gaps to resolve during the build
- **Exact current fee formula** and how it interacts with maker rebates at low volume tiers.
- **Fill realism** for maker orders — queue-position modeling is the biggest backtest risk.
- **Resolution-rule equivalence** detection for cross-venue arb (S6) — likely needs an LLM matcher + human validation.
- **Kalshi sports markets** — undocumented academically; run your own favorite-longshot / closing-line analysis on stored data before trading them.
- **Regime dependence** — several signals (macro, drift) may be regime-specific; monitor for decay.

---

## 11. Reference links (verify — these change)
- Kalshi API docs: https://docs.kalshi.com
- Kalshi getting started (WebSockets): https://docs.kalshi.com/getting_started/quick_start_websockets
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Kalshi fee schedule (PDF): https://kalshi.com/docs/kalshi-fee-schedule.pdf
- Kalshi fee schedule page: https://kalshi.com/fee-schedule
- Kalshi API help hub: https://help.kalshi.com/kalshi-api

**Evidence basis:** every strategy above traces to a paper in `Prediction_Markets_Kalshi_Literature_Review.docx` — see that file's ranked-ideas table (idea numbers map to S-modules) and per-paper "Strategy ideas" fields.

> ⚠️ This agent risks real money. Keep the LIVE arming and capital-allocation decisions with a human, start with the smallest possible size, and treat every strategy as unproven until its own net-of-fee backtest and forward paper-trading pass.
