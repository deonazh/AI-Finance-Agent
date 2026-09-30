"""Calculations and session context shared by the finance dashboard."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, MutableMapping

import pandas as pd
import plotly.graph_objects as go
from reporting import money_prefix


def dataframe_fingerprint(df: pd.DataFrame) -> str:
    digest = sha256(repr(list(df.columns)).encode())
    digest.update(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    return digest.hexdigest()


def refresh_context(state: MutableMapping[str, Any], key: str, fingerprint: str) -> None:
    """Invalidate answers and exports before displaying a different data context."""
    if state.get(key) == fingerprint:
        return
    for name in ("executive_markdown", "analyst_json", "agent_errors", "quick_action_result",
                 "chat_messages", "chat_exports", "pending_chat_prompt", "review_pack"):
        state.pop(name, None)
    state[key] = fingerprint


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(["fiscal_year", "period"], as_index=False).agg(
        budget=("budget", "sum"), actual=("actual", "sum")
    ).sort_values(["fiscal_year", "period"])
    grouped["variance"] = grouped["actual"] - grouped["budget"]
    grouped["label"] = [f"FY{int(year)} · P{int(period):02d}" for year, period in
                        zip(grouped["fiscal_year"], grouped["period"])]
    return grouped


def style_chart(fig: go.Figure, height: int = 330) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", height=height,
        font=dict(family="sans-serif", color="#aebacd", size=12),
        margin=dict(l=10, r=10, t=35, b=20),
        legend=dict(orientation="h", y=1.16, x=0),
        yaxis=dict(title=None, tickprefix=money_prefix(), gridcolor="rgba(160,180,210,0.08)"),
        xaxis=dict(title=None, showgrid=False),
        hoverlabel=dict(bgcolor="#192437", font_size=13),
    )
    return fig


def monthly_trend_chart(df: pd.DataFrame) -> go.Figure:
    summary = monthly_summary(df)
    fig = go.Figure()
    for column, label, color, dash in [("budget", "Budget", "#8293ab", "dot"),
                                      ("actual", "Actual", "#5edbc0", "solid")]:
        fig.add_scatter(x=summary["label"], y=summary[column], name=label,
                        mode="lines+markers", line=dict(color=color, width=3, dash=dash),
                        marker=dict(size=6), hovertemplate="%{x}<br>" + label + ": " + money_prefix() + "%{y:,.2f}<extra></extra>")
    fig.update_layout(hovermode="x unified")
    return style_chart(fig)


def reconciled_waterfall(df: pd.DataFrame, driver_df: pd.DataFrame, limit: int = 6) -> go.Figure:
    budget, actual = float(df["budget"].sum()), float(df["actual"].sum())
    drivers = driver_df.groupby("gl_account").agg(budget=("budget", "sum"), actual=("actual", "sum"))
    differences = drivers["actual"] - drivers["budget"]
    differences = differences.loc[differences.abs().sort_values(ascending=False).index].head(limit)
    labels = ["Budget", *differences.index.tolist()]
    values = [budget, *differences.tolist()]
    residual = actual - budget - float(differences.sum())
    if abs(residual) > 1e-8:
        labels.append("Other movements")
        values.append(residual)
    labels.append("Actual")
    values.append(0)  # Plotly's total is the cumulative sum of all previous bars.
    fig = go.Figure(go.Waterfall(
        x=labels, y=values, measure=["absolute"] + ["relative"] * (len(values)-2) + ["total"],
        connector=dict(line=dict(color="rgba(170,190,220,0.25)")),
        increasing=dict(marker=dict(color="#ed9b74")),
        decreasing=dict(marker=dict(color="#5edbc0")),
        totals=dict(marker=dict(color="#8293ab")),
        hovertemplate="%{x}<br>" + money_prefix() + "%{y:,.2f}<extra></extra>",
    ))
    return style_chart(fig, height=360)
