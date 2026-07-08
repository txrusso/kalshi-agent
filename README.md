# Kalshi Agent

Automated trading agent targeting evidence-backed Kalshi prediction-market inefficiencies.

- Evidence base: [Prediction_Markets_Kalshi_Literature_Review.docx](Prediction_Markets_Kalshi_Literature_Review.docx)
- Build plan: [Kalshi_Agent_Build_Spec.md](Kalshi_Agent_Build_Spec.md)

## Setup

```
uv sync
```

Copy `.env.example` to `.env` and fill in your Kalshi API key ID and private key path
(the private key PEM file itself goes in `secrets/`, which is gitignored — never commit
key material or `.env`).

## Status

Scaffolding only. Following the phased roadmap in the build spec: Phase 0 (data layer +
auth) is next. Nothing trades until PAPER backtests and forward paper-trading pass, and
LIVE mode requires explicit human arming.
