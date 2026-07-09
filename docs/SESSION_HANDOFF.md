# Session Handoff — Kalshi Agent

Last updated 2026-07-09 as a safety-net summary of a long build session, in
case the conversation context fills up and a new chat needs to pick up cold.
If you're a fresh Claude session reading this: read this file, then
`docs/Kalshi_Agent_Build_Spec.md` (the original plan) and its companion lit
review for full background. Also check Claude's own memory system
(`project_kalshi_agent_scope.md` in the memory directory, if available) for
the same information plus more granular dated notes.

## What this project is

An automated Kalshi trading agent that trades evidence-backed prediction-
market inefficiencies (favorite-longshot bias, primarily), built from a
literature review + build spec the user supplied. User wants **low-to-medium
risk**, PAPER-mode-first, human-armed-LIVE-later. User is a first-time agent
owner who wants plain-English explanations and does not want to be asked
clarifying questions when working autonomously — make the reasonable call
and keep going. They want the agent to trade and monitor itself
autonomously; they watch via the dashboard.

## Current status (2026-07-09): the agent is BUILT and RUNNING

**The full loop is live**: data refresh → S2 signal → decision → PAPER
execution → exit monitoring, running continuously as a real Windows process
(`kalshi_agent.orchestrator`), started via a detached process (not tied to
any Claude session — check `Get-CimInstance Win32_Process | Where CommandLine
-like "*orchestrator*"` to see if it's still alive; restart via double-
clicking `scripts/run_agent.bat` if not). `TRADING_ENABLED=true` in `.env`.
Monitor via `scripts/run_dashboard.bat` or `uv run streamlit run
src/kalshi_agent/dashboard/app.py`.

**It is PAPER-only and cannot become LIVE by accident.** No order-placement
endpoint exists anywhere in `KalshiClient` — only GET methods. `live_armed`
defaults false. Going live is a deliberate future feature, not a flag flip.

**Honest caveat already told to the user**: given current conservative risk
settings (`min_net_edge_dollars=0.04`, matching their stated low-to-medium
risk preference) and real backtesting, the agent may place **zero or very
few trades** right now — not a bug, see "Important finding" below.

109 tests passing. ~484MB of the 500MB data cap used (bounded — see the
unbounded-growth fix below).

## Folder layout

```
docs/             the two source documents + this handoff
secrets/          kalshi_private_key.pem + the original Kalshi_API.env.txt (gitignored)
scripts/          run_agent.bat (launches the full orchestrator — this is what should run
                  continuously), run_poller.bat (data-only, legacy/manual use),
                  run_dashboard.bat, run_backtest.py
src/kalshi_agent/
  orchestrator.py Ties everything together into the continuous agent loop (run_agent).
                  Composable pieces (run_trading_cycle, run_exit_cycle, run_agent_cycle)
                  are unit tested; the infinite loop itself isn't (by nature).
  data/           Kalshi REST client (auth.py, client.py), rate limiter, poller.py
                  (sync_latest_prices = bounded/repeating-safe; sync_markets_and_snapshot =
                  append-only, one-time/manual historical-corpus building ONLY — do not
                  put it in a repeating loop, see below), SQLAlchemy models, store.py
  risk/           fees.py (Decimal-based), sizing.py (binary-contract Kelly), guardrails.py
  strategy/       calibration.py, signals.py (FavoriteLongshotSignal, WeatherSignal),
                  decision.py (horizon-aware sizing), backtest.py, exit.py (alpha-realization
                  exit), horizon.py, data_loader.py, external/ (weather.py, econ.py)
  execution/      adapter.py (interface), paper.py (PAPER-only, by design)
  ledger/         audit.py, portfolio.py
  dashboard/      Streamlit app.py + data.py (win rate, positions, data status)
tests/            one file per src module, ~109 tests
```

Run tests: `uv run pytest` · Dashboard: `scripts/run_dashboard.bat` ·
Backtest: `uv run python scripts/run_backtest.py` · Agent: `scripts/run_agent.bat`

## Important finding: S2 backtest is currently a HONEST ZERO, not a bug

Two real methodology bugs were found and fixed by testing against real data:

1. **Correlated observations.** Many markets in the target categories are
   different price-threshold variants of the *same* underlying event (e.g. a
   dozen "BTC above $X" markets a few hours apart, tracking one price path).
   Fixed with `deduplicate_by_event` in `strategy/backtest.py`.
2. **Too-small calibration buckets.** `calibration_min_samples=20` let an
   8/20 = 40% training "win rate" through as noise. Raised default to 200.

**With both fixes, the real backtest against real data produces zero
trades.** Root cause (verified): the two buckets with enough samples to
trust (0-5¢, 95-100¢) have a max possible edge, after fees, of ~3-4¢ — never
quite clears the conservative 4¢ threshold. **This is the safety threshold
working as intended, not a failure.** Do not claim S2 is "profitable"
without re-verifying. Options if revisiting: narrower `calibration_
bucket_width` (e.g. 0.01), more data over time, or an explicit user decision
to lower `min_net_edge_dollars` (their risk call, not one to make solo).

## Two critical bugs found and fixed *before* enabling continuous operation

Both were caught by actually running things against real data/production,
not by synthetic tests:

1. **Unbounded DB growth.** The original size-cap check only stopped adding
   *new* markets — it never stopped appending a new `PriceSnapshot` row for
   *already-tracked* markets every cycle. Running that in a loop would have
   blown the 500MB cap within days (already at ~472MB from the one-time
   historical backfill, almost no headroom left). Fixed with a new
   `LatestPrice` model (one row per ticker, upserted — bounded regardless of
   cycle count) and `sync_latest_prices` (uses it instead of appending).
   `run_poller` and `orchestrator.py` both use the bounded version now.
   `strategy/exit.py` reads current prices from `LatestPrice`, not
   `PriceSnapshot`, for the same reason. **Never call
   `sync_markets_and_snapshot` in a repeating loop — it's append-only, for
   one-time corpus building only.**
2. **SQLite lock contention.** With the agent (writer) and dashboard
   (reader) now running as separate concurrent processes, SQLite's default
   mode would throw "database is locked" the moment they overlapped. Fixed
   by enabling `PRAGMA journal_mode=WAL` + `busy_timeout=5000` in
   `data/store.py`'s `make_engine`. Verified live — dashboard reads cleanly
   while the agent writes.

## Known unresolved issue: Scheduled Task auto-restart is flaky

The `KalshiAgentPoller` Windows Scheduled Task (fires on network reconnect)
was repointed to launch `scripts/run_agent.bat` (full orchestrator, not just
data collection). One bug was found and fixed (schtasks.exe mis-parsing the
spaced path "Kalshi Agent" — rebuilt the task with `Register-ScheduledTask`
PowerShell cmdlets instead of raw `schtasks.exe /TR`). **But even after that
fix, a task-scheduler-launched run silently died a few seconds in** (lock
file orphaned, log stopped after 2 lines, no process alive) — while the
exact same batch file worked fine launched directly. Root cause not found;
likely a Task Scheduler execution-context quirk (PATH/session/permissions
differences). **Current workaround**: the agent was started directly via a
detached `Process.Start` from this session, which survives independent of
the Claude session but won't auto-restart on network reconnect/reboot the
way the scheduled task is supposed to. If revisiting, check Event Viewer →
Task Scheduler operational log for the actual failure — wasn't accessible
remotely from this session.

## Other known TODOs / not-yet-done

- Weather ticker parser (Kalshi ticker → city/lat-lon/strike/date) — needs
  real `Climate and Weather` category market examples (now being collected,
  added to `target_categories` this session) to build/verify against.
- Econ (FRED) signal is infrastructure-only; turning it into a real trading
  edge needs a news-underreaction/drift design (build spec S4), not a
  simple current-value lookup — scheduled releases get priced in within
  seconds, so a lagging data pull doesn't beat the market alone.
- No live order-placement capability exists anywhere in the codebase, on
  purpose. Do not add it without an explicit, separate user go-ahead.

## Key facts worth not re-discovering the hard way

- Kalshi demo and production have **separate API key stores** — this key is
  production-only. Production base URL:
  `https://api.elections.kalshi.com/trade-api/v2`.
- Kalshi's actual rate limit is lower than docs suggest (~20/sec documented,
  real 429s at that rate) — client defaults to 10/sec.
- `/markets` has no bulk category filter, only single `series_ticker`. Open-
  market sweeps work broadly; settled-market history does not (dominated by
  high-frequency esports/crypto-threshold resolutions) — must query
  per-series for settled data.
- Market price/volume fields come back as dollar-strings (`"0.4500"`), not
  integer cents; no `series_ticker` field on the market object itself
  (derive it from the ticker prefix before the first hyphen).
- Fee formula (verified against two independent sources, re-confirm against
  live docs before real capital): taker = round_up($0.07 × C × P × (1−P)),
  maker = 25% of that. Must use `Decimal`, not `float` — float rounding
  produced asymmetric fees at symmetric prices in testing.
- A full open-market sweep across ~23k tracked markets takes roughly 5-12
  minutes at the current rate limit — `poll_interval_seconds` (1800s
  default) must stay comfortably above this or cycles will overlap.
