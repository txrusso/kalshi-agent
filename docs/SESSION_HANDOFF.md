# Session Handoff — Kalshi Agent

Written 2026-07-09 as a safety-net summary of a long build session, in case
the conversation context fills up and a new chat needs to pick up cold. If
you're a fresh Claude session reading this: read this file, then
`docs/Kalshi_Agent_Build_Spec.md` (the original plan) and
`Kalshi_Agent_Build_Spec.md`'s companion lit review for full background. Also
check Claude's own memory system (`project_kalshi_agent_scope.md` in the
memory directory, if available) for the same information plus more granular
dated notes.

## What this project is

An automated Kalshi trading agent that trades evidence-backed prediction-
market inefficiencies (favorite-longshot bias, primarily), built from a
literature review + build spec the user supplied. User wants **low-to-medium
risk**, PAPER-mode-first, human-armed-LIVE-later. User is a non-technical-ish
owner who wants plain-English explanations and does not want to be asked
clarifying questions when working autonomously — make the reasonable call
and keep going.

## Current build status (2026-07-09, 3 commits in)

**Phase 0 (data layer) and Phase 1 (risk guardrails + PAPER execution + fee
model): done and tested.** ~100 tests passing. Real Kalshi credentials are
live and working (production account, RSA-PSS signing verified, $20 real
balance). Real market data collected: 110,187 markets, 87,843 resolved,
~472MB (under the 500MB cap).

**Phase 2 (S2 strategy + backtest): implemented, but the honest result is
currently ZERO trades / no demonstrated edge** — see "Important finding"
below. Do not claim S2 is profitable without re-verifying.

**New this session (user directives, not from the original build spec):**
- Horizon-aware trading (favor short-term) + alpha-realization early exit
  for long-term positions — implemented, tested, **not wired into a runner**.
- Weather signal (NOAA/NWS, real, keyless, verified live) — the forecast
  math is real and tested; the Kalshi-ticker→city parsing layer is **not
  built** (no real weather-market examples existed yet to verify against).
- Econ signal infrastructure (FRED API client) — real client code, tested
  via mocks, but **no live API key available** to verify against, and
  deliberately not turned into a trading signal (see reasoning below).
- Dashboard win rate — implemented, will show "n/a" until real paper trades
  exist and resolve (none placed yet).

## Folder layout

```
docs/            the two source documents + this handoff
secrets/          kalshi_private_key.pem + the original Kalshi_API.env.txt (gitignored)
scripts/          run_poller.bat (Windows Scheduled Task target), run_backtest.py
src/kalshi_agent/
  data/           Kalshi REST client (auth.py, client.py), rate limiter, poller.py (open-market
                  sweep + per-series settled backfill), SQLAlchemy models, store.py
  risk/           fees.py (Decimal-based, matches Kalshi's published formula), sizing.py
                  (correct binary-contract Kelly), guardrails.py (caps, circuit breaker,
                  master switch)
  strategy/       calibration.py, signals.py (FavoriteLongshotSignal, WeatherSignal),
                  decision.py (signal -> sized OrderRequest, horizon-aware), backtest.py,
                  exit.py (alpha-realization exit), horizon.py, data_loader.py,
                  external/ (weather.py, econ.py)
  execution/      adapter.py (interface), paper.py (PAPER-only; NO live order-placement
                  exists anywhere in the codebase, on purpose)
  ledger/         audit.py (event log), portfolio.py (positions/cash balance from fill history)
  dashboard/      Streamlit app.py + data.py (business logic kept separate for testability)
tests/            one file per src module, ~100 tests
```

Run tests: `uv run pytest` · Dashboard: `uv run streamlit run src/kalshi_agent/dashboard/app.py`
· Backtest: `uv run python scripts/run_backtest.py`

## Important finding: S2 backtest is currently a HONEST ZERO, not a bug

Two real methodology bugs were found and fixed by testing against real data
(not synthetic tests, which didn't catch either):

1. **Correlated observations.** Many markets in the target categories are
   different price-threshold variants of the *same* underlying event (e.g. a
   dozen "BTC above $X" markets a few hours apart, tracking one price path).
   Treating each as an independent trial let 15 correlated markets
   masquerade as 15 independent ones and go 0-for-15 out of sample. Fixed
   with `deduplicate_by_event` in `strategy/backtest.py` — keeps one
   observation per `event_ticker`.

2. **Too-small calibration buckets.** `calibration_min_samples=20` let a
   bucket with an 8/20 = 40% training "win rate" through as if it were real;
   it was noise and the true rate is much closer to 0-1% (matching the
   well-populated neighboring buckets). Raised the default to 200.

**With both fixes together, the real backtest against real collected data
produces zero trades.** Root cause (verified, not mysterious): the two
buckets with enough samples to trust (0-5¢ and 95-100¢) have a maximum
possible edge, after Kalshi's fee, of roughly 3-4 cents — which never quite
clears the `min_net_edge_dollars=0.04` conservative threshold set for the
user's stated low-to-medium risk preference. This is the safety threshold
doing its job, not a failure. **Do not tell the user S2 is "working" or
"profitable" without re-verifying this has changed** (e.g. after more data
collection, narrower calibration buckets, or an explicit user decision to
lower the edge threshold).

Next things to try, if picking this back up:
- Narrower `calibration_bucket_width` (e.g. 0.01 instead of 0.05) — the true
  bias likely varies a lot between a 1¢ and a 5¢ longshot; a wide bucket
  blurs that together.
- More data over time (the poller/backfill can be re-run; it's idempotent).
- Explicitly ask the user whether to lower `min_net_edge_dollars` — that's
  their risk-tolerance call, not one to make unilaterally.

## Known TODOs / not-yet-done

- `strategy/exit.py`'s `find_positions_to_exit` is tested but nothing calls
  it periodically — needs a scheduled loop (or wiring into whatever runner
  eventually drives the live decision cycle).
- Weather ticker parser (Kalshi ticker → city/lat-lon/strike/date) — needs
  real `Climate and Weather` category market examples (now being collected
  going forward) to build against.
- Econ signal is infrastructure-only; turning FRED data into a real trading
  edge needs a news-underreaction/drift design (build spec S4), not a
  simple current-value lookup — releases get priced in within seconds.
- No live order-placement capability exists anywhere in the codebase. This
  is intentional per the build spec's human-in-the-loop principle — do not
  add it without an explicit, separate user go-ahead.
- Windows Scheduled Task (`KalshiAgentPoller`) restarts the poller on
  network reconnect, but only while the laptop is awake/logged in
  (deliberately not configured for closed-lid operation — overheating risk
  flagged to and accepted-with-caveat by the user).

## Key facts worth not re-discovering the hard way

- Kalshi demo and production have **separate API key stores** — this key is
  production-only. Production base URL:
  `https://api.elections.kalshi.com/trade-api/v2`.
- Kalshi's actual rate limit is lower than the docs suggest (~20/sec
  documented, real 429s at that rate) — client defaults to 10/sec.
- `/markets` has no bulk category filter, only single `series_ticker`. Open-
  market sweeps work broadly (categories interleaved); settled-market
  history does not (dominated by high-frequency esports/crypto-threshold
  resolutions) — must query per-series for settled data.
- Market price/volume fields come back as dollar-strings (`"0.4500"`), not
  integer cents, and there's no `series_ticker` field on the market object
  itself (derive it from the ticker prefix before the first hyphen).
- Fee formula (verified against two independent sources, re-confirm against
  live docs before real capital): taker = round_up($0.07 × C × P × (1−P)),
  maker = 25% of that. Must use `Decimal`, not `float` — float rounding
  produced asymmetric fees at symmetric prices in testing.
