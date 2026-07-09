import asyncio

import streamlit as st

from kalshi_agent.config import settings
from kalshi_agent.dashboard.data import (
    get_account_summary,
    get_data_collection_status,
    get_recent_audit_events,
    get_recent_orders,
    seconds_since,
)
from kalshi_agent.data.client import KalshiClient
from kalshi_agent.data.store import make_engine, make_session_factory

st.set_page_config(page_title="Kalshi Agent", layout="wide")

session_factory = make_session_factory(make_engine(settings))


@st.cache_data(ttl=30)
def _fetch_real_balance() -> float | None:
    """Live Kalshi account balance — cached 30s so every dashboard rerun
    doesn't hit the read-rate bucket. Returns None if the call fails (e.g.
    offline) rather than crashing the page."""
    async def _get() -> float:
        async with KalshiClient(settings) as client:
            data = await client.get_balance()
            return data["balance"] / 100  # cents -> dollars

    try:
        return asyncio.run(_get())
    except Exception as exc:  # noqa: BLE001 — dashboard should degrade, not crash
        st.session_state["_balance_error"] = str(exc)
        return None


st.title("Kalshi Agent")

mode_color = "🟢" if settings.mode == "PAPER" else "🔴"
st.caption(
    f"{mode_color} mode={settings.mode} · trading_enabled={settings.trading_enabled} · "
    f"live_armed={settings.live_armed} · env={settings.kalshi_env}"
)

real_balance = _fetch_real_balance()
account = get_account_summary(session_factory, settings, real_balance_dollars=real_balance)

col1, col2, col3 = st.columns(3)
col1.metric("Real Kalshi balance", f"${real_balance:.2f}" if real_balance is not None else "unavailable")
col2.metric(
    "Paper balance",
    f"${account['paper_balance_dollars']:.2f}",
    delta=f"{account['paper_balance_dollars'] - account['paper_starting_balance']:+.2f}",
)
col3.metric("Open paper positions", len(account["paper_positions"]))

if real_balance is None and "_balance_error" in st.session_state:
    st.warning(f"Couldn't fetch live balance: {st.session_state['_balance_error']}")

st.subheader("Paper positions")
if account["paper_positions"]:
    st.dataframe(account["paper_positions"], use_container_width=True)
else:
    st.caption("No open paper positions yet.")

st.divider()

st.subheader("Data collection status")
status = get_data_collection_status(session_factory, settings)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Markets tracked", status["market_count"])
c2.metric("Price snapshots", status["price_snapshot_count"])
c3.metric("DB size", f"{status['db_size_mb']:.1f} / {status['db_cap_mb']:.0f} MB")
age = seconds_since(status["last_snapshot_ts"])
c4.metric("Last snapshot", f"{age:.0f}s ago" if age is not None else "never")

st.caption(f"Target categories: {', '.join(status['target_categories'])}")
if status["top_series"]:
    st.caption("Top series by market count:")
    st.dataframe(status["top_series"], use_container_width=True)

st.divider()

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Recent orders")
    orders = get_recent_orders(session_factory, limit=20)
    if orders:
        st.dataframe(orders, use_container_width=True)
    else:
        st.caption("No orders placed yet.")

with col_b:
    st.subheader("Recent activity")
    events = get_recent_audit_events(session_factory, limit=20)
    if events:
        st.dataframe(events, use_container_width=True)
    else:
        st.caption("No audit log entries yet.")
