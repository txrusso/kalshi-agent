import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    event_ticker: Mapped[str] = mapped_column(String, index=True)
    series_ticker: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(Text)
    # Raw resolution rule text — critical for cross-venue arbitrage filtering (build spec §3.2).
    resolution_rules: Mapped[str | None] = mapped_column(Text)
    open_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expiration_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    result: Mapped[str | None] = mapped_column(String)
    raw: Mapped[dict] = mapped_column(JSON)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("markets.ticker"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    yes_bid: Mapped[int | None] = mapped_column(Integer)
    yes_ask: Mapped[int | None] = mapped_column(Integer)
    last_price: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    open_interest: Mapped[int | None] = mapped_column(Integer)


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("markets.ticker"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    yes_levels: Mapped[list] = mapped_column(JSON)  # [[price_cents, size], ...]
    no_levels: Mapped[list] = mapped_column(JSON)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    is_taker: Mapped[bool] = mapped_column()
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw: Mapped[dict] = mapped_column(JSON)
