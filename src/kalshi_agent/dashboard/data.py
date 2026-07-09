import datetime as dt
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from kalshi_agent.data.models import AuditLogEntry, Market, Order, PriceSnapshot
from kalshi_agent.ledger.portfolio import compute_cash_balance, compute_positions

"""Business logic for the dashboard, kept separate from app.py's Streamlit
rendering so it's plain, unit-testable Python — no `st.*` calls in here."""


def get_account_summary(session_factory: sessionmaker, settings, *, real_balance_dollars: float | None) -> dict:
    with session_factory() as session:
        paper_balance = compute_cash_balance(session, mode="PAPER", starting_balance=settings.paper_starting_balance)
        paper_positions = compute_positions(session, mode="PAPER")

    return {
        "real_balance_dollars": real_balance_dollars,
        "paper_balance_dollars": paper_balance,
        "paper_starting_balance": settings.paper_starting_balance,
        "paper_positions": [
            {"ticker": p.ticker, "side": p.side, "count": p.count, "avg_price": p.avg_price}
            for p in paper_positions.values()
        ],
        "mode": settings.mode,
        "trading_enabled": settings.trading_enabled,
        "live_armed": settings.live_armed,
    }


def get_paper_performance(session_factory: sessionmaker) -> dict:
    """Win rate among PAPER entry orders whose underlying market has since
    resolved — a "win" means the side bought matches the resolution. Not
    tied to whether an explicit exit/sell happened; a position still open
    when its market resolves counts too, since that's the real outcome."""
    with session_factory() as session:
        rows = session.execute(
            select(Order.side, Market.result)
            .join(Market, Market.ticker == Order.ticker)
            .where(
                Order.mode == "PAPER",
                Order.action == "buy",
                Order.status == "filled",
                Market.result.in_(("yes", "no")),
            )
        ).all()

    wins = sum(1 for side, result in rows if side == result)
    total = len(rows)
    return {
        "resolved_trades": total,
        "wins": wins,
        "win_rate": (wins / total) if total else None,
    }


def _db_size_mb(database_url: str) -> float:
    if not database_url.startswith("sqlite"):
        return 0.0
    db_path = Path(urlparse(database_url).path.lstrip("/"))
    return round(db_path.stat().st_size / 1_000_000, 1) if db_path.exists() else 0.0


def get_data_collection_status(session_factory: sessionmaker, settings) -> dict:
    with session_factory() as session:
        market_count = session.scalar(select(func.count()).select_from(Market)) or 0
        snapshot_count = session.scalar(select(func.count()).select_from(PriceSnapshot)) or 0
        last_snapshot_ts = session.scalar(select(func.max(PriceSnapshot.ts)))

        rows = session.execute(
            select(Market.series_ticker, func.count()).group_by(Market.series_ticker).order_by(func.count().desc()).limit(10)
        ).all()

    return {
        "market_count": market_count,
        "price_snapshot_count": snapshot_count,
        "db_size_mb": _db_size_mb(settings.database_url),
        "db_cap_mb": round(settings.max_db_size_bytes / 1_000_000, 1),
        "last_snapshot_ts": last_snapshot_ts,
        "target_categories": list(settings.target_categories),
        "top_series": [{"series_ticker": s, "market_count": n} for s, n in rows],
    }


def get_recent_orders(session_factory: sessionmaker, *, limit: int = 20) -> list[dict]:
    with session_factory() as session:
        orders = session.scalars(select(Order).order_by(Order.created_ts.desc()).limit(limit)).all()
    return [
        {
            "client_order_id": o.client_order_id,
            "ticker": o.ticker,
            "side": o.side,
            "action": o.action,
            "price": o.price,
            "count": o.count,
            "status": o.status,
            "mode": o.mode,
            "created_ts": o.created_ts,
        }
        for o in orders
    ]


def get_recent_audit_events(session_factory: sessionmaker, *, limit: int = 50) -> list[dict]:
    with session_factory() as session:
        events = session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.ts.desc()).limit(limit)).all()
    return [{"ts": e.ts, "event_type": e.event_type, "ticker": e.ticker, "details": e.details} for e in events]


def seconds_since(ts: dt.datetime | None) -> float | None:
    if ts is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return (now - ts).total_seconds()
