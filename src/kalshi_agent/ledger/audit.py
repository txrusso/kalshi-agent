import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from kalshi_agent.data.models import AuditLogEntry


def log_event(session: Session, event_type: str, *, ticker: str | None = None, details: dict[str, Any] | None = None) -> None:
    """Every signal, decision, order, fill, and cancel goes through here
    (build spec §6 "Audit log") — nothing about the agent's behavior should
    be reconstructable-in-hindsight from anywhere else."""
    session.add(
        AuditLogEntry(
            ts=dt.datetime.now(dt.timezone.utc),
            event_type=event_type,
            ticker=ticker,
            details=details or {},
        )
    )
    session.commit()
