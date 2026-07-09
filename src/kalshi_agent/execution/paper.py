import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from kalshi_agent.data.models import Fill, Order
from kalshi_agent.execution.adapter import ExecutionAdapter, OrderResult
from kalshi_agent.ledger.audit import log_event
from kalshi_agent.ledger.portfolio import compute_cash_balance, compute_portfolio_state
from kalshi_agent.risk.fees import maker_fee, taker_fee
from kalshi_agent.risk.guardrails import OrderRequest, check_order


class PaperExecutionAdapter(ExecutionAdapter):
    """Simulated-fill adapter — never calls Kalshi's order-placement API (which
    doesn't exist in this codebase yet; see KalshiClient). Runs the same
    guardrail checks a LIVE adapter would, so PAPER results are meaningful
    evidence before any real capital is at risk (build spec §7.3).

    MVP simplification: every accepted order fills immediately in full at the
    requested price. Real fill/queue-position realism is Phase 2/7 backtest-
    harness work, not this scaffolding."""

    def __init__(self, session_factory: sessionmaker, settings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def submit_order(self, request: OrderRequest, *, client_order_id: str, reason: str | None = None) -> OrderResult:
        if request.mode != "PAPER":
            raise ValueError(f"PaperExecutionAdapter only accepts mode='PAPER' orders, got {request.mode!r}")

        now = dt.datetime.now(dt.timezone.utc)
        with self._session_factory() as session:
            # Idempotency (build spec §6): a retried submission of the same
            # client_order_id must return the original outcome, not double-fill
            # or crash on the Order table's primary-key collision.
            existing = session.get(Order, client_order_id)
            if existing is not None:
                if existing.status == "filled":
                    fill = session.scalars(select(Fill).where(Fill.order_id == client_order_id)).first()
                    return OrderResult(
                        accepted=True,
                        client_order_id=client_order_id,
                        filled_count=existing.count,
                        fill_price=existing.price,
                        fee=fill.fee if fill else 0.0,
                    )
                return OrderResult(accepted=False, client_order_id=client_order_id, reason=existing.reason or "duplicate submission of a non-filled order")

            cash_balance = compute_cash_balance(
                session, mode="PAPER", starting_balance=self._settings.paper_starting_balance
            )
            portfolio = compute_portfolio_state(session, mode="PAPER", balance=cash_balance)

            log_event(
                session,
                "order_evaluated",
                ticker=request.ticker,
                details={"client_order_id": client_order_id, "price": request.price, "count": request.count, "reason": reason},
            )

            result = check_order(request, portfolio, self._settings)

            if not result.allowed:
                session.add(
                    Order(
                        client_order_id=client_order_id,
                        kalshi_order_id=None,
                        ticker=request.ticker,
                        side=request.side,
                        action=request.action,
                        order_type=request.order_type,
                        price=request.price,
                        count=request.count,
                        status="rejected",
                        mode=request.mode,
                        strategy=None,
                        reason=reason,
                        created_ts=now,
                        updated_ts=now,
                        raw=None,
                    )
                )
                log_event(session, "order_rejected", ticker=request.ticker, details={"client_order_id": client_order_id, "reason": result.reason})
                return OrderResult(accepted=False, client_order_id=client_order_id, reason=result.reason)

            is_taker = request.order_type == "market"
            fee = (taker_fee if is_taker else maker_fee)(request.price, request.count)

            session.add(
                Order(
                    client_order_id=client_order_id,
                    kalshi_order_id=None,
                    ticker=request.ticker,
                    side=request.side,
                    action=request.action,
                    order_type=request.order_type,
                    price=request.price,
                    count=request.count,
                    status="filled",
                    mode=request.mode,
                    strategy=None,
                    reason=reason,
                    created_ts=now,
                    updated_ts=now,
                    raw=None,
                )
            )
            session.add(
                Fill(
                    order_id=client_order_id,
                    ticker=request.ticker,
                    side=request.side,
                    action=request.action,
                    price=request.price,
                    count=request.count,
                    fee=fee,
                    is_taker=is_taker,
                    mode=request.mode,
                    ts=now,
                    raw=None,
                )
            )
            session.commit()
            log_event(
                session,
                "order_filled",
                ticker=request.ticker,
                details={"client_order_id": client_order_id, "price": request.price, "count": request.count, "fee": fee},
            )

        return OrderResult(accepted=True, client_order_id=client_order_id, filled_count=request.count, fill_price=request.price, fee=fee)

    async def cancel_order(self, client_order_id: str) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        with self._session_factory() as session:
            order = session.get(Order, client_order_id)
            if order is None or order.status in ("filled", "cancelled", "rejected"):
                return False
            order.status = "cancelled"
            order.updated_ts = now
            session.commit()
            log_event(session, "order_cancelled", ticker=order.ticker, details={"client_order_id": client_order_id})
        return True
