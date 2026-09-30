"""Streamlit interface for the AI Finance Agent."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from hashlib import sha256
from html import escape
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agents import run_variance_analysis
from reporting import money, reporting_currency, currency_from_frame, currency_code, money_prefix
from exports import csv_bytes
from review import EDITABLE, STATUSES, ledger_id, review_register, save_edits, review_json, restore_review, month_end_pack
from chatbot import FinanceDataChatbot
from epm_ui import render_planning_workspace
from dashboard import dataframe_fingerprint, refresh_context, monthly_trend_chart, reconciled_waterfall, style_chart
from data_engine import (
    filter_significant_variances,
    generate_synthetic_budget_actuals,
    load_budget_actuals_file,
)


st.set_page_config(
    page_title="AI Finance Agent",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="auto",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: #0c1220;
            color: #f4f6fb;
        }
        [data-testid="stSidebar"] {
            background: #101a2b;
            border-right: 1px solid rgba(255,255,255,0.08);
        }
        .block-container {
            padding-top: 4.5rem;
            max-width: 1700px;
            padding-bottom: 3rem;
        }
        .copilot-jump { display: none; }
        .eyebrow { color: #5edbc0; font-size: 0.72rem; letter-spacing: 0.18em;
            font-weight: 700; margin-bottom: 0.65rem; }
        .status-pill { display: inline-block; padding: 0.3rem 0.65rem; border-radius: 20px;
            background: #1c3040; color: #a6e4d5; font-size: 0.76rem; margin: 0.25rem 0.4rem 0.8rem 0; }
        [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
        [data-baseweb="tab-list"] { gap: 1.2rem; }
        [data-testid="stChatMessage"] { background: #131f32; border-radius: 12px; }
        .st-key-copilot_panel,
        .st-key-copilot_panel [data-testid="stChatMessage"],
        .st-key-copilot_panel [data-testid="stChatMessageContent"],
        .st-key-copilot_panel [data-testid="stMarkdownContainer"] {
            min-width: 0;
            max-width: 100%;
            box-sizing: border-box;
        }
        .st-key-copilot_panel [data-testid="stChatMessageContent"] {
            flex: 1 1 0%;
            overflow-wrap: anywhere;
        }
        .st-key-copilot_panel [data-testid="stMarkdownContainer"] {
            overflow-wrap: anywhere;
        }
        .st-key-copilot_panel h3 {
            font-size: 1.125rem;
            line-height: 1.35;
        }
        .st-key-copilot_panel h4 {
            font-size: 0.95rem;
            line-height: 1.4;
        }
        .st-key-copilot_panel [data-testid="stChatMessage"] {
            padding: 0.85rem;
            gap: 0.65rem;
            border: 1px solid rgba(174,190,213,0.08);
        }
        .st-key-copilot_panel [data-testid="stChatMessageAvatarUser"],
        .st-key-copilot_panel [data-testid="stChatMessageAvatarAssistant"],
        .st-key-copilot_panel [data-testid="stChatMessageAvatarCustom"] {
            background: #213346;
            color: #a6e4d5;
        }
        .st-key-copilot_panel [data-testid="stMarkdownContainer"] table {
            display: block;
            width: max-content;
            max-width: 100%;
            overflow-x: auto;
            overscroll-behavior-x: contain;
            scrollbar-width: thin;
            scrollbar-color: #63788f #172438;
            border: 1px solid #2c3d53;
            border-radius: 8px;
            font-size: 0.84rem;
            line-height: 1.45;
            margin: 0.8rem 0;
        }
        .st-key-copilot_panel table th,
        .st-key-copilot_panel table td {
            padding: 0.5rem 0.65rem;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }
        .st-key-copilot_panel table th {
            background: #1d2d43;
            color: #cbd7e7;
            font-weight: 600;
        }
        .st-key-copilot_panel table tbody tr:nth-child(even) {
            background: rgba(255,255,255,0.025);
        }
        .st-key-copilot_panel pre {
            max-width: 100%;
            overflow-x: auto;
        }
        [data-testid="stSidebar"] .block-container { padding-top: 1rem; }
        .app-title {
            font-size: clamp(1.8rem, 3vw, 2.6rem);
            letter-spacing: -0.04em;
            line-height: 1.1;
            font-weight: 720;
            margin-bottom: 0.2rem;
        }
        .app-subtitle {
            color: #9aa4b2;
            font-size: 0.98rem;
            margin-bottom: 1.3rem;
        }
        .kpi-card {
            background: linear-gradient(135deg, #18253a 0%, #111c2e 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 1rem 1.05rem;
            min-height: 132px;
        }
        .kpi-label {
            color: #9aa4b2;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.45rem;
        }
        .kpi-value {
            color: #f6f8fb;
            font-size: clamp(1.25rem, 2vw, 1.85rem);
            letter-spacing: -0.025em;
            font-weight: 700;
        }
        .kpi-help {
            color: #aab2c0;
            font-size: 0.86rem;
            margin-top: 0.4rem;
        }
        .section-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin: 1.2rem 0 0.6rem;
        }
        .guide-callout {
            background: #171c27;
            border: 1px solid rgba(255,255,255,0.08);
            border-left: 4px solid #60a5a6;
            border-radius: 12px;
            padding: 1rem 1.1rem;
            margin: 0.8rem 0 1rem;
        }
        .guide-callout strong {
            color: #f6f8fb;
        }
        .guide-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.85rem;
            margin: 0.7rem 0 1rem;
        }
        .guide-card {
            background: #151a24;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 1rem;
            min-height: 150px;
        }
        .guide-card h4 {
            margin: 0 0 0.45rem;
            color: #f6f8fb;
            font-size: 0.98rem;
        }
        .guide-card p, .guide-card li {
            color: #b8c0cc;
            font-size: 0.9rem;
            line-height: 1.45;
        }
        .guide-card ul {
            margin: 0.3rem 0 0;
            padding-left: 1.1rem;
        }
        .prompt-chip {
            display: inline-block;
            background: #202838;
            border: 1px solid rgba(255,255,255,0.09);
            border-radius: 6px;
            color: #e9edf5;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.82rem;
            padding: 0.34rem 0.48rem;
            margin: 0.22rem 0.18rem 0.22rem 0;
        }
        @media (max-width: 900px) {
            .copilot-jump { display: inline-block; color: #5edbc0; margin-bottom: 0.7rem; }
            .guide-grid {
                grid-template-columns: 1fr;
            }
        }
        [data-testid="stMetricValue"] { font-size: 1.45rem; }
        [data-testid="stMetricValue"] > div { white-space: normal; overflow-wrap: anywhere; }
        .st-key-copilot_panel [data-testid="stFullScreenFrame"],
        .st-key-copilot_panel [data-testid="stDataFrame"] { min-width: 0; max-width: 100%; }
        div[data-testid="stMetric"] {
            background: #171c27;
            border: 1px solid rgba(255,255,255,0.08);
            padding: 0.8rem 1rem;
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def pct(value: float) -> str:
    return f"{value:,.1f}%"


PERIOD_NAMES: dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def period_filter_label(period: int) -> str:
    """Format fiscal period filters in business-friendly month language."""

    return f"{PERIOD_NAMES.get(int(period), 'Period')} (P{int(period):02d})"


def filter_dashboard_dataframe(
    df: pd.DataFrame,
    cost_centers: list[str],
    gl_accounts: list[str],
    periods: list[int],
    quarters: list[str],
    directions: list[str],
) -> pd.DataFrame:
    """Apply dashboard filters to raw or flagged Budget vs Actual rows."""

    result = df.copy()
    if cost_centers and "cost_center" in result:
        result = result[result["cost_center"].isin(cost_centers)]
    if gl_accounts and "gl_account" in result:
        result = result[result["gl_account"].isin(gl_accounts)]
    if periods and "period" in result:
        result = result[result["period"].astype(int).isin([int(period) for period in periods])]
    if quarters and "quarter" in result:
        result = result[result["quarter"].astype(str).isin(quarters)]
    if directions and "variance_direction" in result:
        result = result[result["variance_direction"].astype(str).isin(directions)]
    return result.copy()


@st.cache_data(show_spinner=False)
def cached_synthetic(seed: int, threshold_pct: float, dollar_threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = generate_synthetic_budget_actuals(seed=seed)
    flagged = filter_significant_variances(raw, threshold_pct, dollar_threshold)
    return raw, flagged


def render_kpis(raw_df: pd.DataFrame) -> None:
    total_budget = float(raw_df["budget"].sum())
    total_actual = float(raw_df["actual"].sum())
    variance = total_actual - total_budget
    variance_pct = variance / total_budget * 100 if total_budget else None
    status = "Above budget" if variance > 0 else "Below budget" if variance < 0 else "On budget"
    accent = "#ed9b74" if variance > 0 else "#5edbc0" if variance < 0 else "#f6f8fb"
    values = [
        ("Budget", money(total_budget), "Selected ledger baseline", "#f6f8fb"),
        ("Actual spend", money(total_actual), f"{len(raw_df):,} rows in this view", "#f6f8fb"),
        ("Net variance", money(variance), status, accent),
        ("Variance %", pct(variance_pct) if variance_pct is not None else "N/A",
         "Actual minus budget / budget" if total_budget else "No budget baseline", accent),
    ]
    for col, (label, value, help_text, color) in zip(st.columns(4), values, strict=True):
        with col:
            st.markdown(f'''<div class="kpi-card"><div class="kpi-label">{label}</div>
                <div class="kpi-value" style="color:{color}">{value}</div>
                <div class="kpi-help">{help_text}</div></div>''', unsafe_allow_html=True)


def budget_actual_chart(raw_df: pd.DataFrame) -> go.Figure:
    summary = raw_df.groupby("cost_center", as_index=False).agg(
        budget=("budget", "sum"), actual=("actual", "sum")
    ).sort_values("actual", ascending=False)
    fig = go.Figure()
    for column, label, color in [("budget", "Budget", "#8293ab"), ("actual", "Actual", "#5edbc0")]:
        fig.add_bar(x=summary["cost_center"], y=summary[column], name=label, marker_color=color,
                    hovertemplate="%{x}<br>" + label + ": " + money_prefix() + "%{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.35)
    return style_chart(fig)


def waterfall_chart(raw_df: pd.DataFrame, flagged_df: pd.DataFrame) -> go.Figure:
    return reconciled_waterfall(raw_df, flagged_df)


def render_dashboard_filters(raw_df: pd.DataFrame, flagged_df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Keep filters compact; empty selections mean all values."""
    with st.expander("Refine your view", expanded=False):
        scope = st.radio("Ledger scope", ["Full ledger", "Material variances"], horizontal=True, key="dashboard_scope")
        source_df = raw_df if scope == "Full ledger" else flagged_df
        specs = [
            ("Fiscal year", "fiscal_year"), ("Cost center", "cost_center"), ("G/L account", "gl_account"),
            ("Month / period", "period"), ("Quarter", "quarter"), ("Direction", "variance_direction"),
        ]
        selections = {}
        cols = st.columns(3)
        for index, (label, column) in enumerate(specs):
            options = sorted(raw_df[column].dropna().unique().tolist())
            key = f"dashboard_{column}"
            if key in st.session_state:
                st.session_state[key] = [v for v in st.session_state[key] if v in options]
            with cols[index % 3]:
                selections[column] = st.multiselect(label, options, key=key, placeholder="All",
                    format_func=period_filter_label if column == "period" else str)
        st.caption("No selection includes all values. Material variances meet either review threshold.")
        if st.button("Reset filters", key="reset_dashboard_filters"):
            for _, column in specs:
                st.session_state.pop(f"dashboard_{column}", None)
            st.session_state.pop("dashboard_scope", None)
            st.rerun()
    view = filter_dashboard_dataframe(source_df, selections["cost_center"], selections["gl_account"],
        selections["period"], selections["quarter"], selections["variance_direction"])
    if selections["fiscal_year"]:
        view = view[view["fiscal_year"].isin(selections["fiscal_year"])]
    details = []
    for label, column in specs:
        selected = selections[column]
        if selected:
            names = [period_filter_label(v) if column == "period" else str(v) for v in selected]
            details.append(f"{label}: {', '.join(names)}")
    view_label = "raw ledger rows" if scope == "Full ledger" else "flagged variance rows"
    st.caption(f"{scope} · {len(view):,} of {len(source_df):,} rows" + (" · " + " · ".join(details) if details else " · All periods and departments"))
    return view.copy(), view_label


def markdown_to_pdf(markdown_text: str) -> bytes:
    """Create a management working paper with page numbers."""

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError("PDF export requires reportlab; install the project requirements.") from exc

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42)
    styles = getSampleStyleSheet()
    story = []
    for line in markdown_text.splitlines():
        if not line.strip():
            story.append(Spacer(1, 8))
            continue
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe = safe.replace("**", "")
        if line.startswith("# "):
            story.append(Paragraph(safe[2:], styles["Title"]))
        elif line.startswith("## "):
            story.append(Paragraph(safe[3:], styles["Heading2"]))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {safe[2:]}", styles["BodyText"]))
        else:
            story.append(Paragraph(safe, styles["BodyText"]))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColorRGB(0.35, 0.4, 0.46)
        canvas.drawString(42, 24, "AI Finance Agent | Management working paper")
        canvas.drawRightString(A4[0]-42, 24, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def toggle_copilot_layout() -> None:
    st.session_state.copilot_expanded = not st.session_state.get("copilot_expanded", False)


def render_copilot_message(index: int, message: dict) -> None:
    avatar = ":material/person:" if message["role"] == "user" else ":material/query_stats:"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        if message.get("mode"):
            st.caption(f"{message['mode']} · {message.get('scope', '')}")
        if message.get("warnings"):
            with st.expander("Response notes"):
                for warning in message["warnings"]:
                    st.caption(warning)
        export_id = message.get("export_id")
        if export_id and export_id in st.session_state.chat_exports:
            export = st.session_state.chat_exports[export_id]
            st.download_button("Download Excel", data=export["bytes"], file_name=export["filename"],
                mime=export["mime"], key=f"download_{export_id}_{index}", width="stretch")


def render_data_chatbot(raw_df: pd.DataFrame, flagged_df: pd.DataFrame, view_df: pd.DataFrame, view_label: str) -> None:
    """Keep the latest answer readable, with optional full-width conversation space."""
    with st.container(border=True, key="copilot_panel", autoscroll=False):
        title, layout = st.columns([0.65, 0.35], vertical_alignment="center")
        with title:
            st.markdown("### Finance Copilot")
        with layout:
            expanded = st.session_state.get("copilot_expanded", False)
            st.button("Back to dashboard" if expanded else "Expand", key="toggle_copilot_layout",
                on_click=toggle_copilot_layout, width="stretch",
                help="Switch between dashboard-side chat and full-width chat without losing this conversation.")
        st.caption("Ask about your numbers. Wide tables scroll sideways.")
        source_label = st.selectbox("Answer using", ["Current dashboard view", "Full ledger"], key="chat_scope",
            help="Sets the default data scope. You can explicitly name another scope in your question. Changing the dashboard view starts a fresh conversation.")
        source = "current_view" if source_label == "Current dashboard view" else "raw"
        if st.session_state.get("chat_scope_context") != source:
            st.session_state.chat_messages = []
            st.session_state.chat_exports = {}
            st.session_state.chat_scope_context = source
        row_count = len(view_df) if source == "current_view" else len(raw_df)
        st.caption(f"{row_count:,} rows available")
        st.session_state.setdefault("chat_messages", [])
        st.session_state.setdefault("chat_exports", {})
        prompt = ""
        suggestions = [("Summarise finances", "summarise finances"),
                       ("Top overspends", "top 5 unfavorable variances"),
                       ("Monthly trend", "show monthly trend"),
                       ("Export to Excel", "export to Excel")]
        cols = st.columns(4 if expanded else 2)
        for index, (label, question) in enumerate(suggestions):
            with cols[index % len(cols)]:
                if st.button(label, key=f"chat_suggestion_{index}", width="stretch", disabled=row_count == 0):
                    prompt = question
        # The composer stays above the answer, so a longer reply doesn't push it away.
        typed_prompt = st.chat_input("Ask about your finances…", key="copilot_input", max_chars=4000, disabled=row_count == 0)
        with st.expander("Assistant settings"):
            provider = st.selectbox("Provider", ["local fallback", "openai", "anthropic"], key="chatbot_provider")
            st.caption("Local mode works immediately. OpenAI and Claude use the credentials in your project environment.")
            st.caption("This session keeps the latest 40 messages and 5 Excel downloads. Save working papers before clearing chat or closing the session.")
        prompt = (typed_prompt or prompt).strip()
        messages = st.session_state.chat_messages
        # Content grows with the response; no nested vertical scroll or bottom-following.
        with st.container(key="copilot_latest", autoscroll=False):
            if not messages:
                st.markdown("**Where would you like to start?**")
                st.markdown("Ask which department is over budget, explore savings, or compare two months. Select Expand for more space to read tables.")
            else:
                st.markdown("#### Latest response")
                for index in range(max(0, len(messages) - 2), len(messages)):
                    render_copilot_message(index, messages[index])
        if len(messages) > 2:
            with st.expander(f"Earlier messages ({len(messages) - 2})", expanded=False):
                for index, message in enumerate(messages[:-2]):
                    render_copilot_message(index, message)
        if messages:
            conversation = "\n\n".join(f"{m['role'].title()}: {m['content']}" for m in messages)
            clear_col, download_col = st.columns(2)
            with clear_col:
                if st.button("Clear chat", key="clear_chatbot", width="stretch"):
                    st.session_state.chat_messages = []
                    st.session_state.chat_exports = {}
                    st.rerun()
            with download_col:
                st.download_button("Save conversation", conversation, "finance_conversation.md", "text/markdown", width="stretch")
        if prompt:
            prior_messages = list(st.session_state.chat_messages)
            bot = FinanceDataChatbot(raw_df, flagged_df, view_df, default_source=source)
            with st.spinner("Reviewing your data…"):
                try:
                    response = bot.answer(prompt, provider=None if provider == "local fallback" else provider,
                        use_llm=provider != "local fallback", chat_history=prior_messages)
                except Exception:
                    st.error("I couldn't complete that request. Try a narrower question or use local mode in Assistant settings.")
                    return
            st.session_state.chat_messages.append({"role": "user", "content": prompt})
            assistant_message = {"role": "assistant", "content": response.message,
                "mode": "AI + checked data tools" if response.used_llm else "Local data analysis",
                "scope": f"Default scope: {source_label}", "warnings": response.warnings}
            if response.export_bytes and response.export_filename and response.export_mime:
                export_id = str(uuid4())
                st.session_state.chat_exports[export_id] = {"bytes": response.export_bytes,
                    "filename": response.export_filename, "mime": response.export_mime}
                assistant_message["export_id"] = export_id
            st.session_state.chat_messages.append(assistant_message)
            st.session_state.chat_messages = st.session_state.chat_messages[-40:]
            referenced = {message.get("export_id") for message in st.session_state.chat_messages}
            retained = [(key, value) for key, value in st.session_state.chat_exports.items() if key in referenced][-5:]
            st.session_state.chat_exports = dict(retained)
            st.rerun()


def render_workbench_quick_actions(
    raw_df: pd.DataFrame,
    flagged_df: pd.DataFrame,
    view_df: pd.DataFrame,
) -> None:
    """Render one-click actions outside the free-form chatbot."""

    st.markdown('<div class="section-title">Workbench Quick Actions</div>', unsafe_allow_html=True)
    with st.container(border=True):
        st.caption(
            "These buttons use the current filtered workbench rows. "
            "The full workflow guide now lives in the top Guide tab."
        )
        action_col_1, action_col_2, action_col_3 = st.columns([1, 1, 1])
        disabled = view_df.empty

        with action_col_1:
            top_clicked = st.button("Show Top Drivers", key="workbench_top_drivers", width="stretch", disabled=disabled)
        with action_col_2:
            xlsx_clicked = st.button(
                "Create Investigation XLSX",
                key="workbench_investigation_xlsx",
                width="stretch",
                disabled=disabled,
            )
        with action_col_3:
            clear_clicked = st.button("Clear Result", key="workbench_clear_quick_action", width="stretch")

        if clear_clicked:
            st.session_state.quick_action_result = None

        if top_clicked or xlsx_clicked:
            bot = FinanceDataChatbot(raw_df=raw_df, flagged_df=flagged_df, current_view_df=view_df)
            prompt = (
                "export biggest variance investigation in current view to Excel"
                if xlsx_clicked
                else "top 10 variances in current view"
            )
            with st.spinner("Preparing workbench action..."):
                response = bot.answer(prompt, use_llm=False)

            st.session_state.quick_action_result = {
                "message": response.message,
                "agent_used": response.agent_used,
                "export_bytes": response.export_bytes,
                "export_filename": response.export_filename,
                "export_mime": response.export_mime,
            }

        result = st.session_state.get("quick_action_result")
        if result:
            st.markdown(result["message"])
            if result.get("agent_used"):
                st.caption(f"Handled by {result['agent_used']}")
            if result.get("export_bytes") and result.get("export_filename") and result.get("export_mime"):
                st.download_button(
                    "Download Investigation Workbook",
                    data=result["export_bytes"],
                    file_name=result["export_filename"],
                    mime=result["export_mime"],
                    key="download_workbench_investigation_xlsx",
                    width="stretch",
                )


def render_application_guide():
    st.markdown("## User guide")
    st.caption("Reporting, budgets, forecasts, drivers, cash, and version review in one workspace.")
    topics = ["Getting started", "Planning workflows", "Reporting workflows"]
    selected = st.selectbox("Guide topic", topics, key="guide_topic")
    document = {"Getting started": "QUICKSTART.md", "Planning workflows": "PLANNING.md",
                "Reporting workflows": "USER_GUIDE.md"}[selected]
    text = (Path(__file__).parent / "docs" / document).read_text()
    # Repository-relative links are useful on GitHub, but not routes in Streamlit.
    import re
    text = re.sub(r"!?\[([^]]+)\]\((?!https?://|#)[^)]+\)", r"\1", text)
    st.markdown(text)


def render_overview(view_df: pd.DataFrame, threshold_pct: float, dollar_threshold: float) -> None:
    if view_df.empty:
        st.info("No rows match this view. Open Refine your view to adjust or reset the filters.")
        return
    flagged = filter_significant_variances(view_df, threshold_pct, dollar_threshold)
    with st.container(border=True):
        st.markdown("#### Monthly performance")
        st.caption("Budget and actual spend across the selected fiscal periods.")
        st.plotly_chart(monthly_trend_chart(view_df), width="stretch")
    with st.container(border=True):
        st.markdown("#### Where to focus")
        st.caption(f"{len(flagged):,} of {len(view_df):,} rows exceed {threshold_pct:g}% or {money(dollar_threshold)} in variance.")
        grouped = view_df.groupby(["cost_center", "gl_account"], as_index=False).agg(budget=("budget", "sum"), actual=("actual", "sum"))
        grouped["variance"] = grouped["actual"] - grouped["budget"]
        grouped = grouped.loc[grouped["variance"].abs().sort_values(ascending=False).index]
        st.dataframe(grouped.head(5), hide_index=True, width="stretch", column_config=ledger_columns())
    with st.container(border=True):
        chart = st.selectbox("Explore spend", ["Spend by department", "Budget to actual bridge"], label_visibility="collapsed")
        if chart == "Spend by department":
            st.plotly_chart(budget_actual_chart(view_df), width="stretch")
        else:
            st.caption("The largest account movements plus all remaining movements reconcile budget to actual spend.")
            st.plotly_chart(waterfall_chart(view_df, view_df), width="stretch")


def ledger_columns() -> dict:
    return {
        "fiscal_year": st.column_config.NumberColumn("Fiscal year", format="%d"),
        "period": st.column_config.NumberColumn("Period", format="%d"),
        "period_label": "Fiscal period", "cost_center": "Cost center", "gl_account": "G/L account",
        "budget": st.column_config.NumberColumn("Budget", format=money_prefix()+"%.2f"),
        "actual": st.column_config.NumberColumn("Actual", format=money_prefix()+"%.2f"),
        "variance": st.column_config.NumberColumn("Variance", format=money_prefix()+"%.2f"),
        "variance_pct": st.column_config.NumberColumn("Variance %", format="%.2f%%"),
        "variance_direction": "Direction", "flag_reason": "Review reason",
    }


def render_briefing(view_df: pd.DataFrame, threshold_pct: float, dollar_threshold: float) -> None:
    st.markdown("#### A briefing ready for review")
    st.caption(f"Build a management summary from the {len(view_df):,} selected rows, with drivers and recommended follow-up actions.")
    provider = st.selectbox("Commentary provider", ["local fallback", "openai", "anthropic"], key="briefing_provider")
    if st.button("Generate executive brief", type="primary", width="stretch", disabled=view_df.empty):
        with st.spinner("Preparing your financial briefing…"):
            try:
                result = run_variance_analysis(view_df, threshold_pct=threshold_pct, threshold_dollars=dollar_threshold,
                    provider=None if provider == "local fallback" else provider, use_llm=provider != "local fallback")
            except Exception:
                st.error("The briefing could not be generated. Try local mode or review the selected data.")
                return
        st.session_state.executive_markdown = result.executive_markdown
        st.session_state.analyst_json = result.analyst_json
        st.session_state.agent_errors = result.errors
    if st.session_state.get("executive_markdown"):
        with st.container(border=True):
            st.markdown(st.session_state.executive_markdown)
        if st.session_state.get("agent_errors"):
            with st.expander("Analysis notes"):
                for error in st.session_state.agent_errors:
                    st.caption(error)
        left, right = st.columns(2)
        with left:
            st.download_button("Download Markdown", st.session_state.executive_markdown,
                "variance_briefing.md", "text/markdown", width="stretch")
        with right:
            st.download_button("Download PDF", markdown_to_pdf(st.session_state.executive_markdown),
                "variance_briefing.pdf", "application/pdf", width="stretch")
    else:
        st.info("Generate a brief to review the financial position, key drivers, and actions. Changing the data or filters clears the previous brief.")


def render_review_workspace(source_ledger, view_df, source_name, threshold_pct, dollar_threshold, year, closed_period):
    st.markdown("#### From variance to follow-up")
    st.caption("Assign an owner, record supporting explanations, and track each material item. These notes are working papers, not approvals.")
    notes = st.session_state.setdefault("review_notes", {})
    register = review_register(view_df, notes, float(threshold_pct), float(dollar_threshold))
    metrics = st.columns(3)
    metrics[0].metric("Review items", len(register))
    metrics[1].metric("Open / in progress", int(register["status"].ne("Resolved").sum()))
    metrics[2].metric("Unassigned", int(register["owner"].eq("").sum()))
    st.caption("Save your edits before downloading. Download the review JSON to keep your notes after the browser session ends.")
    editor_key = "review_editor_" + dataframe_fingerprint(view_df)[:12]
    if not register.empty:
        with st.form("review_form"):
            edited = st.data_editor(register, hide_index=True, width="stretch", height=350,
                disabled=[c for c in register if c not in EDITABLE], key=editor_key,
                column_order=["cost_center", "gl_account", "period", "variance", "owner", "status", "due_date", "explanation", "next_action", "budget", "actual", "variance_pct", "flag_reason", "fiscal_year"],
                column_config={**ledger_columns(), "review_id": None,
                    "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
                    "owner": st.column_config.TextColumn("Owner"),
                    "due_date": st.column_config.TextColumn("Due date (YYYY-MM-DD)"),
                    "explanation": st.column_config.TextColumn("Supporting explanation", width="large"),
                    "next_action": st.column_config.TextColumn("Next action", width="large")})
            if st.form_submit_button("Save review notes", type="primary"):
                try:
                    st.session_state.review_notes = save_edits(edited, notes, source_ledger)
                    st.session_state.pop("review_pack", None)
                    st.success("Review notes saved for this session. Download the JSON backup to retain them.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info("No material items in this view. The reporting pack can still include the selected ledger.")
    notes = st.session_state.review_notes
    st.download_button("Download review backup", review_json(source_ledger, notes),
        "finance_review.json", "application/json", key="download_review_json", width="stretch")
    with st.expander("Restore a saved review"):
        saved = st.file_uploader("Review JSON", type=["json"], key="restore_review_upload")
        if st.button("Restore notes", disabled=saved is None):
            try:
                st.session_state.review_notes = restore_review(saved.getvalue(), source_ledger)
                st.session_state.pop("review_pack", None)
                st.session_state.pop(editor_key, None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.markdown("#### Month-end reporting pack")
    st.caption("A formatted workbook with department totals, ledger detail, review notes, methodology, and reporting context.")
    if st.button("Prepare reporting pack", disabled=view_df.empty, width="stretch"):
        st.session_state.review_pack = month_end_pack(view_df, review_register(view_df, notes, threshold_pct, dollar_threshold),
            {"Source": source_name, "Reporting year": year, "Through period": closed_period,
             "Percentage threshold": threshold_pct, "Amount threshold": dollar_threshold,
             "Filters": str({key: value for key, value in st.session_state.items() if key.startswith("dashboard_")})},
            st.session_state.get("executive_markdown", ""))
    if st.session_state.get("review_pack"):
        st.download_button("Download month-end pack", st.session_state.review_pack,
            f"finance_review_FY{year}_P{closed_period:02d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")


def main() -> None:
    inject_css()
    st.sidebar.markdown("## ◈ Finance Agent")
    st.sidebar.caption("Planning, performance and financial review")
    pages = ["Reporting", "Planning", "Budgeting", "Forecasting", "Business drivers", "Cash planning", "Plan review", "Guide"]
    tabs = st.tabs(pages, key="main_navigation", on_change="rerun")
    for name, tab in zip(pages, tabs):
        if tab.open:
            with tab:
                if name == "Reporting":
                    render_reporting()
                elif name == "Guide":
                    render_application_guide()
                else:
                    render_planning_workspace(name)


def render_reporting():
    st.sidebar.divider()
    mode = st.sidebar.radio("Data source", ["Demo dataset", "Upload your data"], key="data_mode")
    if mode == "Demo dataset":
        with st.sidebar.expander("Demo settings"):
            seed = st.number_input("Synthetic data seed", min_value=1, max_value=9999, value=42, step=1)
        raw_df, _ = cached_synthetic(int(seed), 10.0, 50_000.0)
        source_name = "Synthetic FY2026 ERP data"
    else:
        uploaded = st.sidebar.file_uploader("Budget vs Actual CSV / XLSX", type=["csv", "xlsx"])
        template = "fiscal_year,period,cost_center,gl_account,budget,actual\n2026,1,Finance,Software Licenses,10000,11200\n"
        st.sidebar.download_button("Download upload template", template, "budget_actual_template.csv", "text/csv")
        if uploaded is None:
            st.title("Bring your financial data into focus")
            st.info("Upload a CSV or Excel ledger in the sidebar, or select Demo dataset to explore the workspace.")
            return
        if len(uploaded.getvalue()) > 20_000_000:
            st.error("Upload a file under 20 MB. Aggregate transactions to monthly ledger rows first.")
            return
        signature = sha256(uploaded.getvalue()).hexdigest()
        try:
            if st.session_state.get("upload_signature") != signature:
                upload_buffer = BytesIO(uploaded.getvalue())
                upload_buffer.name = uploaded.name
                st.session_state.upload_df = load_budget_actuals_file(upload_buffer)
                st.session_state.upload_signature = signature
            raw_df = st.session_state.upload_df
        except Exception as exc:
            st.error(f"Unable to load this file: {exc}")
            return
        source_name = uploaded.name
    st.sidebar.divider()
    with st.sidebar.expander("Materiality thresholds", expanded=True):
        threshold_pct = st.slider("Variance threshold %", 1, 50, 10, 1)
        dollar_threshold = st.number_input("Dollar threshold", min_value=0, value=50_000, step=5_000,
            help="A row is material when either absolute percentage or absolute dollar variance exceeds the threshold.")
    st.sidebar.divider()
    source_identity = dataframe_fingerprint(raw_df) + source_name
    if st.session_state.get("report_source") != source_identity:
        for key in list(st.session_state):
            if key == "reporting_year" or key.startswith("closed_period_"):
                st.session_state.pop(key, None)
        st.session_state.report_source = source_identity
    years = sorted(raw_df["fiscal_year"].astype(int).unique().tolist())
    year = st.sidebar.selectbox("Reporting fiscal year", years, index=len(years)-1, key="reporting_year")
    periods = sorted(raw_df.loc[raw_df["fiscal_year"] == year, "period"].astype(int).unique().tolist())
    closed_period = st.sidebar.selectbox("Include through period", periods, index=len(periods)-1,
        format_func=period_filter_label, key=f"closed_period_{year}",
        help="Only include posted or closed periods. Exclude future months whose actuals are not yet available.")
    if "currency" in raw_df:
        code = currency_from_frame(raw_df)
        st.sidebar.caption(f"Reporting currency: {code} (from source)")
    else:
        code = st.sidebar.selectbox("Reporting currency", ["USD", "SGD", "EUR", "GBP", "AUD", "HKD"], key="reporting_currency")
    st.sidebar.caption("Currency labels amounts; no FX conversion is applied. Period names assume a January–December fiscal calendar.")
    source_ledger = raw_df.assign(currency=code)
    st.session_state.epm_source_ledger = source_ledger
    review_fingerprint = ledger_id(source_ledger)
    if st.session_state.get("review_ledger_id") != review_fingerprint:
        st.session_state.review_notes = {}
        st.session_state.review_ledger_id = review_fingerprint
        st.session_state.pop("review_pack", None)
    raw_df = source_ledger[(source_ledger["fiscal_year"] == year) & (source_ledger["period"] <= closed_period)].copy()
    observed = set(raw_df["period"].astype(int))
    missing = sorted(set(range(1, closed_period+1))-observed)
    if missing:
        st.sidebar.warning("Missing periods: " + ", ".join(map(str, missing)) + ". No values are imputed.")
    with reporting_currency(code):
        render_workspace(raw_df, source_ledger, source_name, mode, threshold_pct, dollar_threshold, year, closed_period)


def render_workspace(raw_df, source_ledger, source_name, mode, threshold_pct, dollar_threshold, year, closed_period):
    labels = ["Overview", "Ledger explorer", "Review & export", "Executive brief"]
    tabs = st.tabs(labels, key="report_navigation", on_change="rerun")
    for selected, tab in zip(labels, tabs):
        if tab.open:
            with tab:
                render_reporting_page(raw_df, source_ledger, source_name, mode, threshold_pct,
                                      dollar_threshold, year, closed_period, selected)


def render_reporting_page(raw_df, source_ledger, source_name, mode, threshold_pct,
                          dollar_threshold, year, closed_period, selected):
    raw_fingerprint = dataframe_fingerprint(raw_df) + source_name
    if st.session_state.get("data_context") != raw_fingerprint:
        for key in list(st.session_state):
            if key.startswith("dashboard_"):
                st.session_state.pop(key, None)
    refresh_context(st.session_state, "data_context", raw_fingerprint)
    st.session_state.raw_df = raw_df
    st.session_state.data_source = source_name
    flagged_df = filter_significant_variances(raw_df, float(threshold_pct), float(dollar_threshold))
    st.markdown('<div class="eyebrow">FINANCIAL INTELLIGENCE / WORKSPACE</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-title">Every variance tells a story.</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-subtitle">Understand your spend. Investigate the drivers. Prepare your next decision.</div>', unsafe_allow_html=True)
    source_badge = "DEMO DATA" if mode == "Demo dataset" else "UPLOADED LEDGER"
    st.markdown(f'<span class="status-pill">{source_badge}</span><span class="status-pill">{escape(source_name)}</span>'
        f'<span class="status-pill">FY{year} · through P{closed_period:02d} · {currency_code()} · {len(raw_df):,} rows</span>', unsafe_allow_html=True)
    st.markdown('<a class="copilot-jump" href="#finance-copilot">Ask Finance Copilot ↓</a>', unsafe_allow_html=True)
    view_df, view_label = render_dashboard_filters(raw_df, flagged_df)
    context = f"{dataframe_fingerprint(view_df)}:{view_label}:{threshold_pct}:{dollar_threshold}"
    refresh_context(st.session_state, "view_context", context)
    render_kpis(view_df)
    st.write("")
    if st.session_state.get("copilot_expanded", False):
        render_data_chatbot(raw_df, flagged_df, view_df, view_label)
        return
    workbench, copilot = st.columns([0.65, 0.35], gap="large")
    with workbench:
        if selected == "Overview":
            render_overview(view_df, float(threshold_pct), float(dollar_threshold))
        elif selected == "Ledger explorer":
            st.markdown("#### The detail behind the numbers")
            st.caption("Search within the table, sort any column, or download the entire filtered view.")
            columns = [c for c in ["fiscal_year", "period", "cost_center", "gl_account", "budget", "actual", "variance", "variance_pct", "variance_direction", "flag_reason"] if c in view_df]
            st.dataframe(view_df, column_order=columns, column_config=ledger_columns(), hide_index=True, height=420, width="stretch")
            st.download_button("Download view as CSV", csv_bytes(view_df),
                "ledger_current_view.csv", "text/csv", width="stretch")
            with st.expander("Investigate and export"):
                render_workbench_quick_actions(raw_df, flagged_df, view_df)
        elif selected == "Review & export":
            render_review_workspace(source_ledger, view_df, source_name, threshold_pct, dollar_threshold, year, closed_period)
        elif selected == "Executive brief":
            render_briefing(view_df, float(threshold_pct), float(dollar_threshold))
    with copilot:
        render_data_chatbot(raw_df, flagged_df, view_df, view_label)


if __name__ == "__main__":
    main()
