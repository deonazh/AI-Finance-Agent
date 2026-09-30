"""LangChain-powered data chatbot for the AI Finance Agent.

The chatbot exposes a small set of auditable finance-data tools to the LLM.
That keeps the assistant useful for data questions and Excel extracts without
allowing arbitrary Python execution over the user's machine.
"""

from __future__ import annotations

import json
import math
import time
import re
import ssl
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Annotated, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from exports import format_workbook
from reporting import money as _money, percentage as _pct, reporting_currency, currency_from_frame, currency_code
from data_engine import calculate_variances

from agents import _get_chat_model
from guardrails import validate_response_against_dataframe
from prompts import FINAL_RESPONSE_PROMPT, SYSTEM_PROMPT, TOOL_DESCRIPTIONS

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import StructuredTool
except ImportError:  # pragma: no cover - local rule-based chatbot still works
    AIMessage = HumanMessage = SystemMessage = ToolMessage = None
    StructuredTool = None


DatasetSource = Literal["current_view", "flagged", "raw"]
VarianceDirection = Literal["Favorable", "Unfavorable"]
Quarter = Literal["Q1", "Q2", "Q3", "Q4"]
GroupBy = Literal["cost_center", "gl_account", "cost_center_gl_account", "period"]
VarianceMetricsGroupBy = Literal["total", "cost_center", "gl_account", "cost_center_gl_account", "period"]


@dataclass(frozen=True)
class ChatbotResponse:
    """Response contract used by Streamlit."""

    message: str
    export_bytes: bytes | None = None
    export_filename: str | None = None
    export_mime: str | None = None
    used_llm: bool = False
    agent_used: str = "Data_Copilot_Agent"
    warnings: tuple[str, ...] = ()


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DataProfileInput(ToolInput):
    source: DatasetSource = Field(default="current_view", description="Dataset to inspect.")


class FilterRowsInput(ToolInput):
    source: DatasetSource = Field(default="current_view", description="Dataset to filter.")
    cost_center: str | None = Field(default=None, max_length=200, description="Exact cost center name.")
    gl_account: str | None = Field(default=None, max_length=200, description="Exact G/L account name.")
    period: int | None = Field(default=None, ge=1, le=12, description="Fiscal period number, 1 to 12.")
    periods: list[Annotated[int, Field(ge=1, le=12)]] | None = Field(default=None, max_length=12, description="Multiple fiscal periods to include, e.g. [10, 11].")
    quarter: Quarter | None = Field(default=None, description="Fiscal quarter, Q1 to Q4.")
    direction: VarianceDirection | None = Field(default=None, description="Favorable or Unfavorable variance.")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum preview rows to return.")


class QueryFinancialDatasetInput(FilterRowsInput):
    group_by: GroupBy | None = Field(default=None, description="Optional aggregation grain.")
    top_n: int = Field(default=10, ge=1, le=30, description="Number of grouped rows to return when aggregating.")


class CalculateVarianceMetricsInput(FilterRowsInput):
    group_by: VarianceMetricsGroupBy = Field(default="total", description="Metric grain to calculate.")
    top_n: int = Field(default=10, ge=1, le=30, description="Number of grouped variance metric rows.")


class SummariseInput(ToolInput):
    source: DatasetSource = Field(default="current_view", description="Dataset to summarise.")
    group_by: GroupBy = Field(
        default="cost_center_gl_account",
        description="Aggregation grain for the summary.",
    )
    top_n: int = Field(default=10, ge=1, le=30, description="Number of top variance groups.")


class ExportExcelInput(FilterRowsInput):
    include_summary_sheet: bool = Field(default=True, description="Include a grouped summary worksheet.")


class LargestVarianceInput(ToolInput):
    source: DatasetSource = Field(default="current_view", description="Dataset scope to analyse.")
    cost_center: str | None = Field(default=None, description="Optional cost center scope.")
    gl_account: str | None = Field(default=None, description="Optional G/L account scope.")
    period: int | None = Field(default=None, ge=1, le=12, description="Optional fiscal period scope.")
    quarter: Quarter | None = Field(default=None, description="Optional fiscal quarter scope.")
    direction: VarianceDirection | None = Field(default=None, description="Optional variance direction scope.")


class ColumnGlossaryInput(ToolInput):
    column_name: str | None = Field(default=None, description="Optional column to explain.")


class ExchangeRateInput(ToolInput):
    base_currency: str = Field(description="Three-letter ISO currency code to convert from, e.g. USD.")
    quote_currency: str = Field(description="Three-letter ISO currency code to convert to, e.g. SGD.")
    amount: float = Field(default=1.0, gt=0, description="Amount to convert.")


class ConvertCurrencyInput(ToolInput):
    amount: float = Field(gt=0, description="Amount to convert.")
    source_currency: str = Field(description="Three-letter ISO currency code to convert from, e.g. USD.")
    target_currency: str = Field(description="Three-letter ISO currency code to convert to, e.g. SGD.")


class UploadSchemaInput(ToolInput):
    file_type: Literal["csv", "xlsx"] = Field(default="xlsx", description="Upload file type to explain.")


class PeriodComparisonInput(ToolInput):
    source: DatasetSource = Field(default="raw", description="Dataset scope to compare.")
    cost_center: str | None = Field(default=None, description="Optional cost center scope.")
    gl_account: str | None = Field(default=None, description="Optional G/L account scope.")
    period_a: int = Field(ge=1, le=12, description="First fiscal period.")
    period_b: int = Field(ge=1, le=12, description="Second fiscal period.")


COLUMN_GLOSSARY: dict[str, str] = {
    "fiscal_year": "Financial year of the ledger row.",
    "period": "Fiscal accounting period, usually 1 to 12 for monthly close.",
    "period_label": "Readable fiscal period label, for example FY2026-P05.",
    "quarter": "Fiscal quarter derived from the period.",
    "cost_center": "Responsible department or budget owner.",
    "gl_account": "General ledger spend category.",
    "budget": "Approved budget amount for that period, cost center, and account.",
    "actual": "Posted actual spend from the ledger extract.",
    "variance": "Actual minus budget. Positive is overspend; negative is underspend.",
    "variance_pct": "Variance divided by budget, expressed as a percentage.",
    "abs_variance": "Absolute dollar size of the variance, used for materiality.",
    "abs_variance_pct": "Absolute percentage size of the variance, used for materiality.",
    "variance_direction": "Unfavorable means actual is above budget; Favorable means actual is below budget.",
    "flag_reason": "Which materiality threshold caused the row to be reviewed.",
    "synthetic_driver": "Synthetic root-cause label used for demo data: price, volume, timing, or normal.",
    "driver_note": "Narrative explanation attached to the demo variance event.",
}


SUPPORTED_CURRENCIES: set[str] = {
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "IDR",
    "JPY",
    "MYR",
    "SGD",
    "THB",
    "USD",
}


CURRENCY_ALIASES: dict[str, str] = {
    "singapore dollar": "SGD",
    "singapore dollars": "SGD",
    "sg dollar": "SGD",
    "usd": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "american dollar": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "sterling": "GBP",
    "jpy": "JPY",
    "yen": "JPY",
    "japanese yen": "JPY",
    "myr": "MYR",
    "ringgit": "MYR",
    "malaysian ringgit": "MYR",
    "aud": "AUD",
    "aussie dollar": "AUD",
    "australian dollar": "AUD",
    "hkd": "HKD",
    "hong kong dollar": "HKD",
    "cny": "CNY",
    "rmb": "CNY",
    "yuan": "CNY",
    "cad": "CAD",
    "canadian dollar": "CAD",
    "chf": "CHF",
    "swiss franc": "CHF",
    "idr": "IDR",
    "rupiah": "IDR",
    "thb": "THB",
    "thai baht": "THB",
}


class FinanceDataChatbot:
    """Tool-backed data assistant for Budget vs Actual analysis."""

    def __init__(
        self,
        raw_df: pd.DataFrame,
        flagged_df: pd.DataFrame,
        current_view_df: pd.DataFrame | None = None,
        default_source: DatasetSource | None = None,
    ) -> None:
        self.raw_df = self._prepare_dataframe(raw_df)
        self.flagged_df = self._prepare_dataframe(flagged_df)
        self.current_view_df = self._prepare_dataframe(current_view_df if current_view_df is not None else flagged_df)
        self._last_export: dict[str, Any] | None = None
        self._last_tool_outputs: list[Any] = []
        self.default_source = default_source

    def answer(self, question: str, provider: str | None = None, use_llm: bool = True,
               chat_history: list[dict[str, Any]] | None = None) -> ChatbotResponse:
        self._last_export = None
        self._last_tool_outputs = []
        if not isinstance(question, str) or len(question) > 4000:
            return ChatbotResponse("Please keep your question under 4,000 characters and focus on one financial task.", agent_used="Guardrail_Agent")
        chat_history = [{"role": item["role"], "content": str(item.get("content", ""))[:12000]}
                        for item in (chat_history or [])[-12:]
                        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}]
        years = sorted(self.raw_df["fiscal_year"].dropna().astype(int).unique()) if "fiscal_year" in self.raw_df else []
        requested = {int(value) for value in re.findall(r"\b(?:fy\s*|(?:fiscal\s+)?year\s+|in\s+|for\s+|during\s+)((?:19|20|21|22)\d{2})\b", question.lower())}
        relative = question.lower()
        now = datetime.now()
        expected_year = None
        if any(term in relative for term in ("last month", "previous month", "prior month")):
            expected_year = now.year - (now.month == 1)
        elif any(term in relative for term in ("last quarter", "previous quarter", "prior quarter")):
            expected_year = now.year - (now.month <= 3)
        elif any(term in relative for term in ("this month", "current month", "this quarter", "current quarter")):
            expected_year = now.year
        if expected_year is not None and expected_year not in years:
            return ChatbotResponse(f"That relative period belongs to FY{expected_year}, which is outside this reporting scope. Select that fiscal year or name an explicit month for the selected year.", agent_used="Guardrail_Agent")
        if len(years) > 1:
            return ChatbotResponse("Choose one reporting fiscal year in the sidebar before using Copilot. Cross-year comparisons are not supported.", agent_used="Guardrail_Agent")
        if requested and not requested.issubset(set(years)):
            return ChatbotResponse(f"This reporting scope contains fiscal year(s) {', '.join(map(str, years))}. Select the requested year in the sidebar; I cannot infer missing-year amounts.", agent_used="Guardrail_Agent")
        with reporting_currency(currency_from_frame(self.raw_df)):
            return self._answer(question, provider, use_llm, chat_history)

    def _answer(
        self,
        question: str,
        provider: str | None = None,
        use_llm: bool = True,
        chat_history: list[dict[str, Any]] | None = None,
    ) -> ChatbotResponse:
        """Answer a user question with LangChain tools, falling back locally."""

        clean_question = question.strip()
        if not clean_question:
            return ChatbotResponse("Ask me what you want to inspect, summarise, or export.")

        self._last_export = None
        lowered_clean = clean_question.lower()
        if _is_greeting_intent(lowered_clean):
            return ChatbotResponse(self._format_greeting_answer(), agent_used="Greeting_Agent")
        if _is_help_intent(lowered_clean):
            return ChatbotResponse(self._format_usage_guide_answer(), agent_used="Guide_Agent")
        if _is_upload_schema_intent(lowered_clean):
            return ChatbotResponse(self._format_upload_schema_answer(), agent_used="Upload_Schema_Agent")
        if _is_app_guide_intent(lowered_clean):
            return ChatbotResponse(self._format_usage_guide_answer(), agent_used="Guide_Agent")
        if _is_out_of_scope_intent(lowered_clean):
            return ChatbotResponse(self._format_out_of_scope_answer(clean_question), agent_used="Guardrail_Agent")
        if self._is_out_of_domain_question(lowered_clean):
            return ChatbotResponse(self._format_non_finance_answer(), agent_used="Guardrail_Agent")
        unresolved = self._unresolved_scope(clean_question)
        if unresolved:
            return ChatbotResponse(unresolved, agent_used="Guardrail_Agent")
        if self.default_source is not None and not _source_is_explicit(lowered_clean):
            source = self.default_source
            if _is_followup_intent(lowered_clean):
                source = self._memory_context(chat_history or []).get("source") or source
            scope = {"raw": "raw ledger", "flagged": "flagged data", "current_view": "current view"}[source]
            clean_question = f"{clean_question} (scope: {scope})"
        if _is_executive_summary_intent(lowered_clean) or _is_general_performance_intent(lowered_clean):
            return self._answer_locally(clean_question, chat_history or [])

        load_dotenv()
        if use_llm and StructuredTool is not None:
            try:
                return self._answer_with_llm(clean_question, provider, chat_history or [])
            except Exception as exc:
                warning = f"The provider request could not be completed ({type(exc).__name__}). A local data-tool answer was used."
                local = self._answer_locally(clean_question, chat_history or [])
                return ChatbotResponse(
                    message=local.message,
                    export_bytes=local.export_bytes,
                    export_filename=local.export_filename,
                    export_mime=local.export_mime,
                    used_llm=False,
                    agent_used=local.agent_used,
                    warnings=(warning,),
                )

        return self._answer_locally(clean_question, chat_history or [])

    def _answer_with_llm(
        self,
        question: str,
        provider: str | None,
        chat_history: list[dict[str, Any]],
    ) -> ChatbotResponse:
        llm = _get_chat_model(provider)
        if llm is None:
            raise RuntimeError("No configured LLM provider/key found.")

        if not hasattr(llm, "bind_tools"):
            raise RuntimeError("Configured LangChain chat model does not support tool calling.")

        tools = self._build_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        bound_llm = llm.bind_tools(tools)
        self._last_tool_outputs = []
        messages: list[Any] = [
            SystemMessage(content=SYSTEM_PROMPT),
            *_history_to_langchain_messages(chat_history[-12:]),
            HumanMessage(content=question),
        ]

        deadline = time.monotonic() + 120
        tool_count = 0
        required_filters = self._detect_filters(question)
        if _is_followup_intent(question.lower()):
            required_filters = _merge_filters(required_filters, self._memory_context(chat_history).get("filters", {}))
        for _ in range(4):
            if time.monotonic() >= deadline:
                raise TimeoutError("Chat request exceeded its time budget.")
            ai_message = bound_llm.invoke(messages)
            messages.append(ai_message)
            tool_calls = getattr(ai_message, "tool_calls", None) or []
            if not tool_calls:
                if self._last_tool_outputs:
                    return self._make_llm_response(ai_message, question, chat_history)
                local = self._answer_locally(question, chat_history)
                return ChatbotResponse(
                    message=local.message,
                    export_bytes=local.export_bytes,
                    export_filename=local.export_filename,
                    export_mime=local.export_mime,
                    used_llm=False,
                    agent_used=local.agent_used,
                    warnings=("LLM did not call a data tool, so validated local routing was used instead.",),
                )

            tool_count += len(tool_calls)
            if tool_count > 8:
                raise RuntimeError("Chat tool-call budget exceeded.")
            for call in tool_calls:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Chat request exceeded its time budget.")
                name = str(call.get("name", ""))
                args = call.get("args") or {}
                tool = tools_by_name.get(name)
                if tool is None:
                    raise ValueError("The provider requested an unavailable tool.")
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be an object.")
                if _source_is_explicit(question.lower()) and "source" in tool.args_schema.model_fields:
                    expected = self._detect_source(question.lower())
                    if args.get("source", "current_view") != expected:
                        raise ValueError("The provider attempted to change the requested data scope.")
                if name in {"fetch_exchange_rate", "convert_currency"} and not _is_fx_intent(question.lower()):
                    raise ValueError("An external FX lookup requires a currency question.")
                if "source" in tool.args_schema.model_fields:
                    for field in ("cost_center", "gl_account", "direction"):
                        required = required_filters.get(field)
                        if required is not None and _normalise_lookup(str(args.get(field, ""))) != _normalise_lookup(str(required)):
                            raise ValueError("The provider changed a requested entity or direction filter.")
                    required_periods = required_filters.get("periods")
                    if required_filters.get("period") is not None:
                        required_periods = [required_filters["period"]]
                    if required_filters.get("quarter") is not None:
                        first = (int(required_filters["quarter"][1]) - 1) * 3 + 1
                        quarter_periods = set(range(first, first + 3))
                        required_periods = list(set(required_periods) & quarter_periods) if required_periods else list(quarter_periods)
                    if required_periods is not None:
                        actual_periods = args.get("periods")
                        if args.get("period") is not None:
                            actual_periods = [args["period"]]
                        if args.get("period_a") is not None and args.get("period_b") is not None:
                            actual_periods = [args["period_a"], args["period_b"]]
                        if args.get("quarter") is not None:
                            first = (int(args["quarter"][1]) - 1) * 3 + 1
                            actual_quarter = set(range(first, first + 3))
                            actual_periods = list(set(actual_periods) & actual_quarter) if actual_periods else list(actual_quarter)
                        if actual_periods is None or set(actual_periods) != set(required_periods):
                            raise ValueError("The provider changed the requested reporting periods.")
                result = tool.invoke(args)
                if len(str(result)) > 100_000:
                    raise ValueError("Tool result exceeds the provider context budget.")
                self._last_tool_outputs.append({"tool": name, "args": args, "result": result})
                messages.append(
                    ToolMessage(
                        content=str(result),
                        tool_call_id=str(call.get("id") or name),
                    )
                )

        if time.monotonic() >= deadline:
            raise TimeoutError("Chat request exceeded its time budget.")
        final_message = llm.invoke(
            messages
            + [
                SystemMessage(
                    content=FINAL_RESPONSE_PROMPT,
                )
            ]
        )
        return self._make_llm_response(final_message, question, chat_history)

    def _make_llm_response(self, message: Any, question: str, chat_history: list[dict[str, Any]]) -> ChatbotResponse:
        content = _message_content_to_text(getattr(message, "content", message))
        export = self._last_export or {}
        if not validate_response_against_dataframe(content, {"tool_outputs": [item["result"] for item in self._last_tool_outputs]}):
            local = self._answer_locally(question, chat_history)
            return ChatbotResponse(
                message=local.message,
                export_bytes=local.export_bytes,
                export_filename=local.export_filename,
                export_mime=local.export_mime,
                used_llm=False,
                agent_used=local.agent_used,
                warnings=("LLM final answer failed numeric guardrail validation; deterministic local routing was used instead.",),
            )
        return ChatbotResponse(
            message=content,
            export_bytes=export.get("bytes"),
            export_filename=export.get("filename"),
            export_mime=export.get("mime"),
            used_llm=True,
            agent_used="LangChain_ReAct_Agent",
        )

    def _answer_locally(self, question: str, chat_history: list[dict[str, Any]] | None = None) -> ChatbotResponse:
        lowered = question.lower()
        invalid_period = _detect_invalid_period(lowered)
        invalid_quarter = _detect_invalid_quarter(lowered)
        if invalid_period is not None:
            return ChatbotResponse(
                f"I cannot answer that as written because period `{invalid_period}` is outside the supported range. "
                "This dataset has fiscal periods 1 to 12."
            )
        if invalid_quarter is not None:
            return ChatbotResponse(
                f"I cannot answer that as written because `{invalid_quarter}` is not a valid fiscal quarter. "
                "Use Q1, Q2, Q3, or Q4."
            )

        source = self._detect_source(lowered)
        filters = self._detect_filters(question)
        period_pair = _detect_period_pair(lowered)
        memory = self._memory_context(chat_history or [])
        inherited_intent = None
        if _is_followup_intent(lowered):
            source = source if _source_is_explicit(lowered) else (memory.get("source") or source)
            filters = _merge_filters(filters, memory.get("filters", {}))
            inherited_intent = memory.get("intent")
            period_pair = period_pair or memory.get("period_pair")

        df = self._filter_dataframe(self._dataset(source), **filters)
        wants_excel = any(token in lowered for token in ["excel", "xlsx", "export", "download", "pull"])
        wants_period_comparison = (period_pair is not None and _is_period_comparison_intent(lowered)) or (
            inherited_intent == "period_comparison" and period_pair is not None and not _has_new_analysis_intent(lowered)
        )
        wants_biggest = _is_biggest_variance_intent(lowered) or (inherited_intent == "biggest" and not _has_new_analysis_intent(lowered))
        wants_investigation = _is_investigation_intent(lowered) or (wants_excel and inherited_intent in {"biggest", "investigation"})
        wants_help = _is_help_intent(lowered)
        wants_trend = _is_trend_intent(lowered)
        wants_compare = _is_compare_intent(lowered)
        wants_amount = _is_amount_lookup_intent(lowered)
        wants_total_variance = _is_total_variance_intent(lowered)
        wants_general_performance = _is_general_performance_intent(lowered)
        wants_executive_summary = _is_executive_summary_intent(lowered)
        wants_directional_listing = _is_directional_listing_intent(lowered)
        wants_anomaly_review = _is_anomaly_review_intent(lowered)
        wants_risk_action = _is_risk_action_intent(lowered)
        if inherited_intent and not _has_new_analysis_intent(lowered):
            wants_trend = wants_trend or inherited_intent == "trend"
            wants_compare = wants_compare or inherited_intent == "compare"
            wants_amount = wants_amount or inherited_intent == "amount_lookup"
            wants_total_variance = wants_total_variance or inherited_intent == "total_variance"

        if _is_greeting_intent(lowered):
            return ChatbotResponse(self._format_greeting_answer(), agent_used="Greeting_Agent")

        if wants_help or _is_app_guide_intent(lowered):
            return ChatbotResponse(self._format_usage_guide_answer(), agent_used="Guide_Agent")

        if _is_out_of_scope_intent(lowered):
            return ChatbotResponse(self._format_out_of_scope_answer(question), agent_used="Guardrail_Agent")

        if self._is_out_of_domain_question(lowered):
            return ChatbotResponse(self._format_non_finance_answer(), agent_used="Guardrail_Agent")

        if _is_fx_intent(lowered):
            return ChatbotResponse(self._format_exchange_rate_answer(question), agent_used="External_Market_Data_Agent")

        if _is_upload_schema_intent(lowered):
            return ChatbotResponse(self._format_upload_schema_answer(), agent_used="Upload_Schema_Agent")

        if (
            wants_amount
            or wants_compare
            or wants_trend
            or wants_total_variance
            or wants_period_comparison
            or wants_general_performance
            or wants_executive_summary
            or wants_biggest
            or wants_investigation
            or wants_directional_listing
            or wants_anomaly_review
            or wants_risk_action
            or _is_top_list_intent(lowered)
        ) and not _source_is_explicit(lowered):
            source = "raw"
            df = self._filter_dataframe(self._dataset(source), **filters)

        if df.empty:
            return ChatbotResponse(self._format_no_data_answer(source=source, filters=filters), agent_used="Guardrail_Agent")

        if wants_period_comparison and period_pair is not None:
            available = set(df["period"].astype(int))
            missing = set(period_pair) - available
            if missing:
                labels = ", ".join(_period_name(period) for period in sorted(missing))
                return ChatbotResponse(f"No rows are available for {labels} in this scope. Both periods must be present; missing data is not zero spend.", agent_used="Guardrail_Agent")
            if wants_excel:
                payload = self._export_period_comparison_excel_payload(
                    df=df,
                    source=source,
                    filters=filters,
                    period_a=period_pair[0],
                    period_b=period_pair[1],
                )
                if not payload:
                    return ChatbotResponse(
                        f"I could not prepare the period comparison report because `{source}` has no matching rows.",
                        agent_used="Guardrail_Agent",
                    )
                summary = payload["summary"]
                message = (
                    f"I prepared an Excel comparison report for {_period_name(period_pair[0])} to {_period_name(period_pair[1])}.\n\n"
                    f"- Scope: `{source}`; filters: {_filter_sentence(filters)}\n"
                    f"- {_period_name(period_pair[0])} actual: {_money(summary['period_a_actual'])}\n"
                    f"- {_period_name(period_pair[1])} actual: {_money(summary['period_b_actual'])}\n"
                    f"- Actual cost difference: {_signed_money(summary['actual_difference'])} "
                    f"({_pct(summary['actual_difference_pct'])})\n\n"
                    "The workbook includes `Comparison Summary`, `Driver Breakdown`, `Period Detail`, "
                    "and `Monthly Trend` sheets."
                )
                return ChatbotResponse(
                    message=message,
                    export_bytes=payload["bytes"],
                    export_filename=payload["filename"],
                    export_mime=payload["mime"],
                    agent_used="Excel_Report_Agent",
                )

            return ChatbotResponse(
                self._format_period_comparison_answer(df, source, filters, period_pair[0], period_pair[1]),
                agent_used="Data_Analyst_Agent",
            )

        if wants_excel and (wants_biggest or wants_investigation):
            payload = self._export_biggest_variance_investigation_payload(df=df, source=source, filters=filters)
            if not payload:
                return ChatbotResponse(
                    f"I could not prepare the investigation report because `{source}` has no matching rows.",
                    agent_used="Guardrail_Agent",
                )
            driver = payload["driver"]
            message = (
                "I prepared a focused Excel investigation workbook for the biggest variance driver.\n\n"
                f"- Driver: `{driver['cost_center']} / {driver['gl_account']}`\n"
                f"- Variance: {_money(driver['variance'])} ({_pct(driver['variance_pct'])}), "
                f"{driver['variance_direction'].lower()}\n"
                f"- Scope used to identify driver: `{source}`; filters: {_filter_sentence(filters)}\n\n"
                "The workbook includes `Investigation`, `Full Year Detail`, `Flagged Rows`, "
                "`Monthly Trend`, and `Top Drivers` sheets."
            )
            return ChatbotResponse(
                message=message,
                export_bytes=payload["bytes"],
                export_filename=payload["filename"],
                export_mime=payload["mime"],
                agent_used="Excel_Report_Agent",
            )

        if wants_excel:
            payload = self._export_excel_payload(
                df=df,
                source=source,
                filters=filters,
                include_summary_sheet=True,
            )
            message = (
                f"I prepared an Excel extract with {len(df):,} rows from `{source}`.\n\n"
                f"Filters applied: {_filter_sentence(filters)}\n\n"
                "It includes a `Detail` sheet and a `Summary` sheet for quick pivot-style review."
            )
            return ChatbotResponse(
                message=message,
                export_bytes=payload["bytes"],
                export_filename=payload["filename"],
                export_mime=payload["mime"],
                agent_used="Excel_Report_Agent",
            )

        if _is_top_list_intent(lowered):
            top_n = _detect_top_n(lowered, default=5)
            group_by = self._detect_group_by(lowered)
            if not _has_explicit_group_by(lowered) and memory.get("group_by"):
                group_by = memory["group_by"]
            return ChatbotResponse(
                self._format_ranked_variances_answer(
                    df,
                    source,
                    filters,
                    group_by,
                    top_n,
                    title=_ranked_variance_title(filters),
                ),
                agent_used="Data_Analyst_Agent",
            )

        if wants_executive_summary or wants_general_performance:
            return ChatbotResponse(
                self._format_executive_snapshot_answer(df=df, source=source, filters=filters),
                agent_used="Executive_Briefing_Agent",
            )

        if wants_biggest or wants_investigation:
            group_by = self._detect_largest_group_by(lowered)
            if not _has_explicit_group_by(lowered) and memory.get("group_by"):
                group_by = memory["group_by"]
            return ChatbotResponse(
                self._format_biggest_variance_answer(df=df, source=source, filters=filters, group_by=group_by),
                agent_used="Variance_Investigation_Agent",
            )

        if wants_risk_action:
            return ChatbotResponse(
                self._format_risk_action_answer(df=df, source=source, filters=filters),
                agent_used="Executive_Action_Agent",
            )

        if wants_directional_listing or wants_anomaly_review:
            group_by = self._detect_group_by(lowered)
            if wants_directional_listing and not _has_explicit_group_by(lowered):
                group_by = "cost_center_gl_account"
            if wants_anomaly_review and not _has_explicit_group_by(lowered):
                group_by = "cost_center_gl_account"
            return ChatbotResponse(
                self._format_ranked_variances_answer(
                    df,
                    source,
                    filters,
                    group_by,
                    _detect_top_n(lowered, default=10),
                    title="top anomalies" if wants_anomaly_review else _ranked_variance_title(filters),
                ),
                agent_used="Data_Analyst_Agent",
            )

        if wants_total_variance:
            return ChatbotResponse(
                self._format_total_variance_answer(df=df, source=source, filters=filters),
                agent_used="Data_Analyst_Agent",
            )

        if wants_trend:
            return ChatbotResponse(
                self._format_ranked_variances_answer(df, source, filters, "period", 12, title="period trend"),
                agent_used="Data_Analyst_Agent",
            )

        if wants_compare:
            group_by = self._detect_group_by(lowered)
            if not _has_explicit_group_by(lowered) and memory.get("group_by"):
                group_by = memory["group_by"]
            if group_by == "cost_center_gl_account" and filters.get("cost_center"):
                group_by = "gl_account"
            return ChatbotResponse(
                self._format_ranked_variances_answer(df, source, filters, group_by, 10, title="budget vs actual"),
                agent_used="Data_Analyst_Agent",
            )

        if wants_amount:
            return ChatbotResponse(
                self._format_amount_lookup_answer(df=df, source=source, filters=filters, question=lowered),
                agent_used="Data_Analyst_Agent",
            )

        glossary_lookup = _normalise_lookup(question)
        mentions_known_column = any(_normalise_lookup(column) in glossary_lookup for column in COLUMN_GLOSSARY)
        data_question = any(
            token in lowered
            for token in [
                "total",
                "sum",
                "largest",
                "biggest",
                "highest",
                "worst",
                "top",
                "show",
                "list",
                "export",
                "pull",
                "rows",
                "summary",
                "investigation",
                "compare",
                "trend",
            ]
        )
        if ("column" in lowered or "explain" in lowered or lowered.startswith("what is")) and mentions_known_column and not data_question:
            return ChatbotResponse(self._format_glossary_answer(question), agent_used="Data_Glossary_Agent")

        if any(token in lowered for token in ["summary", "summarise", "summarize", "top", "driver", "breakdown"]):
            group_by = self._detect_group_by(lowered)
            summary = self._summary_dataframe(df, group_by=group_by, top_n=10)
            return ChatbotResponse(
                "\n".join(
                    [
                        f"Here are the top variance groups from `{source}`.",
                        "",
                        _dataframe_to_markdown(
                            summary,
                            [
                                *[col for col in ["cost_center", "gl_account", "period_label"] if col in summary.columns],
                                "budget",
                                "actual",
                                "variance",
                                "variance_pct",
                                "rows",
                            ],
                        ),
                    ]
                ),
                agent_used="Data_Analyst_Agent",
            )

        if _is_show_rows_intent(lowered):
            preview = df.sort_values("abs_variance", ascending=False).head(12)
            return ChatbotResponse(
                "\n".join(
                    [
                        f"Showing {len(preview):,} of {len(df):,} matching rows from `{source}`.",
                        "",
                        _dataframe_to_markdown(
                            preview,
                            [
                                "period_label",
                                "cost_center",
                                "gl_account",
                                "budget",
                                "actual",
                                "variance",
                                "variance_pct",
                                "variance_direction",
                            ],
                        ),
                    ]
                ),
                agent_used="Data_Analyst_Agent",
            )

        if _looks_like_question(lowered):
            return ChatbotResponse(self._format_unclear_answer(question), agent_used="Guardrail_Agent")

        return ChatbotResponse(self._format_profile_answer(source), agent_used="Data_Analyst_Agent")

    def _build_tools(self) -> list[Any]:
        return [
            StructuredTool.from_function(
                func=self._tool_query_financial_dataset,
                name="query_financial_dataset",
                description=TOOL_DESCRIPTIONS["query_financial_dataset"],
                args_schema=QueryFinancialDatasetInput,
            ),
            StructuredTool.from_function(
                func=self._tool_calculate_variance_metrics,
                name="calculate_variance_metrics",
                description=TOOL_DESCRIPTIONS["calculate_variance_metrics"],
                args_schema=CalculateVarianceMetricsInput,
            ),
            StructuredTool.from_function(
                func=self._tool_convert_currency,
                name="convert_currency",
                description=TOOL_DESCRIPTIONS["convert_currency"],
                args_schema=ConvertCurrencyInput,
            ),
            StructuredTool.from_function(
                func=self._tool_export_excel_report,
                name="export_excel_report",
                description=TOOL_DESCRIPTIONS["export_excel_report"],
                args_schema=ExportExcelInput,
            ),
            StructuredTool.from_function(
                func=self._tool_dataset_profile,
                name="dataset_profile",
                description="Inspect dataset shape, totals, available cost centers, G/L accounts, and largest variance.",
                args_schema=DataProfileInput,
            ),
            StructuredTool.from_function(
                func=self._tool_filter_rows,
                name="filter_variance_rows",
                description=TOOL_DESCRIPTIONS["query_financial_dataset"],
                args_schema=FilterRowsInput,
            ),
            StructuredTool.from_function(
                func=self._tool_summarise_variance_data,
                name="summarise_variance_data",
                description="Aggregate variances by cost center, G/L account, cost center plus G/L, or period.",
                args_schema=SummariseInput,
            ),
            StructuredTool.from_function(
                func=self._tool_export_variance_excel,
                name="export_variance_excel",
                description="Create an Excel workbook for matching Budget vs Actual rows.",
                args_schema=ExportExcelInput,
            ),
            StructuredTool.from_function(
                func=self._tool_largest_variance_investigation,
                name="largest_variance_investigation",
                description="Identify the largest absolute variance driver and return root-cause investigation context.",
                args_schema=LargestVarianceInput,
            ),
            StructuredTool.from_function(
                func=self._tool_export_biggest_variance_investigation,
                name="export_biggest_variance_investigation",
                description="Create a focused Excel workbook for the largest variance driver investigation.",
                args_schema=LargestVarianceInput,
            ),
            StructuredTool.from_function(
                func=self._tool_column_glossary,
                name="column_glossary",
                description="Explain variance dataset columns and enterprise accounting definitions.",
                args_schema=ColumnGlossaryInput,
            ),
            StructuredTool.from_function(
                func=self._tool_fetch_exchange_rate,
                name="fetch_exchange_rate",
                description=TOOL_DESCRIPTIONS["convert_currency"],
                args_schema=ExchangeRateInput,
            ),
            StructuredTool.from_function(
                func=self._tool_upload_schema_requirements,
                name="upload_schema_requirements",
                description=TOOL_DESCRIPTIONS["upload_schema_requirements"],
                args_schema=UploadSchemaInput,
            ),
            StructuredTool.from_function(
                func=self._tool_compare_periods,
                name="compare_periods",
                description=TOOL_DESCRIPTIONS["compare_periods"],
                args_schema=PeriodComparisonInput,
            ),
            StructuredTool.from_function(
                func=self._tool_export_period_comparison_excel,
                name="export_period_comparison_excel",
                description="Create an Excel report comparing cost movement between two fiscal periods.",
                args_schema=PeriodComparisonInput,
            ),
        ]

    def _tool_query_financial_dataset(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
        limit: int = 20,
        group_by: GroupBy | None = None,
        top_n: int = 10,
    ) -> str:
        filters = {
            "cost_center": cost_center,
            "gl_account": gl_account,
            "period": period,
            "periods": periods,
            "quarter": quarter,
            "direction": direction,
        }
        df = self._filter_dataframe(self._dataset(source), **filters)
        if df.empty:
            return _json_dump(
                {
                    "source": source,
                    "row_count": 0,
                    "filters": filters,
                    "message": "That cost center/account/period is not present in the current financial records.",
                }
            )
        if group_by:
            summary = self._summary_dataframe(_recalculate_variance_columns(df), group_by=group_by, top_n=top_n)
            return _json_dump(
                {
                    "source": source,
                    "row_count": len(df),
                    "filters": filters,
                    "group_by": group_by,
                    "summary_rows": _serialise_records(summary),
                }
            )

        preview = _recalculate_variance_columns(df).sort_values("abs_variance", ascending=False).head(limit)
        return _json_dump(
            {
                "source": source,
                "row_count": len(df),
                "filters": filters,
                "preview_rows": _serialise_records(preview),
            }
        )

    def _tool_calculate_variance_metrics(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
        limit: int = 20,
        group_by: VarianceMetricsGroupBy = "total",
        top_n: int = 10,
    ) -> str:
        del limit
        filters = {
            "cost_center": cost_center,
            "gl_account": gl_account,
            "period": period,
            "periods": periods,
            "quarter": quarter,
            "direction": direction,
        }
        df = _recalculate_variance_columns(self._filter_dataframe(self._dataset(source), **filters))
        if df.empty:
            return _json_dump(
                {
                    "source": source,
                    "row_count": 0,
                    "filters": filters,
                    "message": "That cost center/account/period is not present in the current financial records.",
                }
            )

        total_budget = float(df["budget"].sum())
        total_actual = float(df["actual"].sum())
        variance = total_actual - total_budget
        variance_pct = (variance / total_budget) * 100 if total_budget else float("nan")
        payload: dict[str, Any] = {
            "source": source,
            "row_count": len(df),
            "filters": filters,
            "calculation_basis": "Python calculated: Variance_USD = Actual - Budget; Variance_Pct = Variance_USD / Budget * 100.",
            "total_budget": round(total_budget, 2),
            "total_actual": round(total_actual, 2),
            "variance_usd": round(variance, 2),
            "variance_pct": round(variance_pct, 2),
            "variance_direction": "Unfavorable" if variance >= 0 else "Favorable",
            "flag_basis": _flag_basis(df),
        }
        if group_by != "total":
            summary = self._summary_dataframe(df, group_by=group_by, top_n=top_n)
            payload["group_by"] = group_by
            payload["summary_rows"] = _serialise_records(summary)
        return _json_dump(payload)

    def _tool_convert_currency(self, amount: float, source_currency: str, target_currency: str) -> str:
        return _json_dump(_fetch_exchange_rate(source_currency, target_currency, amount))

    def _tool_export_excel_report(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
        limit: int = 20,
        include_summary_sheet: bool = True,
    ) -> str:
        return self._tool_export_variance_excel(
            source=source,
            cost_center=cost_center,
            gl_account=gl_account,
            period=period,
            periods=periods,
            quarter=quarter,
            direction=direction,
            limit=limit,
            include_summary_sheet=include_summary_sheet,
        )

    def _tool_dataset_profile(self, source: DatasetSource = "current_view") -> str:
        return _json_dump(self._profile_dict(source))

    def _tool_filter_rows(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
        limit: int = 20,
    ) -> str:
        df = self._filter_dataframe(
            self._dataset(source),
            cost_center=cost_center,
            gl_account=gl_account,
            period=period,
            periods=periods,
            quarter=quarter,
            direction=direction,
        )
        preview = df.sort_values("abs_variance", ascending=False).head(limit)
        return _json_dump(
            {
                "source": source,
                "row_count": len(df),
                "filters": {
                    "cost_center": cost_center,
                    "gl_account": gl_account,
                    "period": period,
                    "periods": periods,
                    "quarter": quarter,
                    "direction": direction,
                },
                "preview_rows": _serialise_records(preview),
            }
        )

    def _tool_summarise_variance_data(
        self,
        source: DatasetSource = "current_view",
        group_by: GroupBy = "cost_center_gl_account",
        top_n: int = 10,
    ) -> str:
        summary = self._summary_dataframe(self._dataset(source), group_by=group_by, top_n=top_n)
        return _json_dump({"source": source, "group_by": group_by, "summary_rows": _serialise_records(summary)})

    def _tool_export_variance_excel(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
        limit: int = 20,
        include_summary_sheet: bool = True,
    ) -> str:
        del limit
        df = self._filter_dataframe(
            self._dataset(source),
            cost_center=cost_center,
            gl_account=gl_account,
            period=period,
            periods=periods,
            quarter=quarter,
            direction=direction,
        )
        payload = self._export_excel_payload(
            df=df,
            source=source,
            filters={
                "cost_center": cost_center,
                "gl_account": gl_account,
                "period": period,
                "periods": periods,
                "quarter": quarter,
                "direction": direction,
            },
            include_summary_sheet=include_summary_sheet,
        )
        self._last_export = payload
        return _json_dump(
            {
                "filename": payload["filename"],
                "row_count": len(df),
                "mime": payload["mime"],
                "sheets": payload["sheets"],
                "filters": payload["filters"],
            }
        )

    def _tool_largest_variance_investigation(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
    ) -> str:
        df = self._filter_dataframe(
            self._dataset(source),
            cost_center=cost_center,
            gl_account=gl_account,
            period=period,
            quarter=quarter,
            direction=direction,
        )
        driver = self._largest_variance_driver(df)
        if not driver:
            return _json_dump({"source": source, "error": "No matching variance rows found."})
        return _json_dump(
            {
                "source": source,
                "filters": {
                    "cost_center": cost_center,
                    "gl_account": gl_account,
                    "period": period,
                    "quarter": quarter,
                    "direction": direction,
                },
                "largest_variance_driver": driver,
                "largest_individual_row": self._largest_variance_row(df),
                "investigation_notes": self._investigation_notes(driver),
            }
        )

    def _tool_export_biggest_variance_investigation(
        self,
        source: DatasetSource = "current_view",
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
    ) -> str:
        filters = {
            "cost_center": cost_center,
            "gl_account": gl_account,
            "period": period,
            "quarter": quarter,
            "direction": direction,
        }
        df = self._filter_dataframe(self._dataset(source), **filters)
        payload = self._export_biggest_variance_investigation_payload(df=df, source=source, filters=filters)
        if not payload:
            return _json_dump({"source": source, "error": "No matching variance rows found."})
        self._last_export = payload
        return _json_dump(
            {
                "filename": payload["filename"],
                "row_count": payload["row_count"],
                "mime": payload["mime"],
                "sheets": payload["sheets"],
                "driver": payload["driver"],
            }
        )

    def _tool_column_glossary(self, column_name: str | None = None) -> str:
        if column_name:
            key = _normalise_lookup(column_name)
            matched = _best_column_match(key, list(COLUMN_GLOSSARY))
            if matched:
                return _json_dump({matched: COLUMN_GLOSSARY[matched]})
            return _json_dump({"error": f"No glossary entry found for {column_name}."})
        return _json_dump(COLUMN_GLOSSARY)

    def _tool_fetch_exchange_rate(self, base_currency: str, quote_currency: str, amount: float = 1.0) -> str:
        return _json_dump(_fetch_exchange_rate(base_currency, quote_currency, amount))

    def _tool_upload_schema_requirements(self, file_type: Literal["csv", "xlsx"] = "xlsx") -> str:
        return _json_dump({"file_type": file_type, **_upload_schema_dict()})

    def _tool_compare_periods(
        self,
        period_a: int,
        period_b: int,
        source: DatasetSource = "raw",
        cost_center: str | None = None,
        gl_account: str | None = None,
    ) -> str:
        filters = {
            "cost_center": cost_center,
            "gl_account": gl_account,
            "period": None,
            "periods": [period_a, period_b],
            "quarter": None,
            "direction": None,
        }
        df = self._filter_dataframe(self._dataset(source), **filters)
        summary = self._period_comparison_summary(df, period_a, period_b)
        drivers = self._period_comparison_dataframe(df, period_a, period_b, top_n=10)
        return _json_dump(
            {
                "source": source,
                "filters": filters,
                "period_a": period_a,
                "period_b": period_b,
                "summary": summary,
                "driver_rows": _serialise_records(drivers),
            }
        )

    def _tool_export_period_comparison_excel(
        self,
        period_a: int,
        period_b: int,
        source: DatasetSource = "raw",
        cost_center: str | None = None,
        gl_account: str | None = None,
    ) -> str:
        filters = {
            "cost_center": cost_center,
            "gl_account": gl_account,
            "period": None,
            "periods": [period_a, period_b],
            "quarter": None,
            "direction": None,
        }
        df = self._filter_dataframe(self._dataset(source), **filters)
        payload = self._export_period_comparison_excel_payload(df, source, filters, period_a, period_b)
        if not payload:
            return _json_dump({"source": source, "error": "No matching period comparison rows found."})
        self._last_export = payload
        return _json_dump(
            {
                "filename": payload["filename"],
                "mime": payload["mime"],
                "sheets": payload["sheets"],
                "summary": payload["summary"],
            }
        )

    def _export_excel_payload(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        include_summary_sheet: bool = True,
    ) -> dict[str, Any]:
        detail = df.copy()
        summary = self._summary_dataframe(detail, group_by="cost_center_gl_account", top_n=max(len(detail), 1))
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            detail.to_excel(writer, sheet_name="Detail", index=False)
            if include_summary_sheet:
                summary.to_excel(writer, sheet_name="Summary", index=False)
            profile = {"source": source, "filters": _filter_sentence(filters), "currency": currency_code(),
                       "rows": len(detail), "total_budget": float(detail["budget"].sum()),
                       "total_actual": float(detail["actual"].sum()), "net_variance": float(detail["variance"].sum())}
            pd.DataFrame([profile]).to_excel(writer, sheet_name="Profile", index=False)
            format_workbook(writer.book)
        buffer.seek(0)

        filename = _excel_filename(source, filters)
        return {
            "bytes": buffer.getvalue(),
            "filename": filename,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sheets": ["Detail", "Summary", "Profile"] if include_summary_sheet else ["Detail", "Profile"],
            "filters": filters,
        }

    def _export_period_comparison_excel_payload(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        period_a: int,
        period_b: int,
    ) -> dict[str, Any] | None:
        period_df = df[df["period"].astype(int).isin([period_a, period_b])].copy()
        if period_df.empty:
            return None

        summary = self._period_comparison_summary(period_df, period_a, period_b)
        drivers = self._period_comparison_dataframe(period_df, period_a, period_b, top_n=max(len(period_df), 1))
        monthly_trend = self._summary_dataframe(
            self._filter_dataframe(
                self.raw_df,
                cost_center=filters.get("cost_center"),
                gl_account=filters.get("gl_account"),
            ),
            group_by="period",
            top_n=12,
        )

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame([summary]).to_excel(writer, sheet_name="Comparison Summary", index=False)
            drivers.to_excel(writer, sheet_name="Driver Breakdown", index=False)
            period_df.to_excel(writer, sheet_name="Period Detail", index=False)
            monthly_trend.to_excel(writer, sheet_name="Monthly Trend", index=False)
            format_workbook(writer.book)
        buffer.seek(0)

        filename = _period_comparison_filename(filters, period_a, period_b)
        return {
            "bytes": buffer.getvalue(),
            "filename": filename,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sheets": ["Comparison Summary", "Driver Breakdown", "Period Detail", "Monthly Trend"],
            "filters": filters,
            "summary": summary,
        }

    def _export_biggest_variance_investigation_payload(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
    ) -> dict[str, Any] | None:
        driver = self._largest_variance_driver(df)
        if not driver:
            return None

        driver_filters = {
            "cost_center": driver["cost_center"],
            "gl_account": driver["gl_account"],
            "period": filters.get("period"),
            "quarter": filters.get("quarter"),
            "direction": filters.get("direction"),
        }
        full_year_detail = self._filter_dataframe(
            self.raw_df,
            cost_center=driver["cost_center"],
            gl_account=driver["gl_account"],
        )
        flagged_rows = self._filter_dataframe(
            self.flagged_df,
            cost_center=driver["cost_center"],
            gl_account=driver["gl_account"],
        )
        scoped_driver_rows = self._filter_dataframe(self._dataset(source), **driver_filters)
        monthly_trend = self._summary_dataframe(full_year_detail, group_by="period", top_n=12)
        top_drivers = self._summary_dataframe(df, group_by="cost_center_gl_account", top_n=10)
        investigation = pd.DataFrame(
            [
                {
                    **driver,
                    "source_scope": source,
                    "user_filters": _filter_sentence(filters),
                    "largest_individual_row": _json_dump(self._largest_variance_row(scoped_driver_rows)),
                    "likely_root_cause": self._investigation_notes(driver)["likely_root_cause"],
                    "investigation_focus": self._investigation_notes(driver)["investigation_focus"],
                    "recommended_action": self._investigation_notes(driver)["recommended_action"],
                }
            ]
        )

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            investigation.to_excel(writer, sheet_name="Investigation", index=False)
            full_year_detail.to_excel(writer, sheet_name="Full Year Detail", index=False)
            flagged_rows.to_excel(writer, sheet_name="Flagged Rows", index=False)
            monthly_trend.to_excel(writer, sheet_name="Monthly Trend", index=False)
            top_drivers.to_excel(writer, sheet_name="Top Drivers", index=False)
            format_workbook(writer.book)
        buffer.seek(0)

        filename = _investigation_filename(driver)
        return {
            "bytes": buffer.getvalue(),
            "filename": filename,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sheets": ["Investigation", "Full Year Detail", "Flagged Rows", "Monthly Trend", "Top Drivers"],
            "filters": filters,
            "driver": driver,
            "row_count": int(len(full_year_detail)),
        }

    def _profile_dict(self, source: DatasetSource) -> dict[str, Any]:
        df = self._dataset(source)
        total_budget = float(df["budget"].sum()) if "budget" in df else 0.0
        total_actual = float(df["actual"].sum()) if "actual" in df else 0.0
        net_variance = total_actual - total_budget
        net_variance_pct = (net_variance / total_budget) * 100 if total_budget else float("nan")
        largest = (
            df.sort_values("abs_variance", ascending=False).head(1).to_dict("records")
            if "abs_variance" in df and not df.empty
            else []
        )
        return {
            "source": source,
            "rows": int(len(df)),
            "columns": df.columns.tolist(),
            "total_budget": round(total_budget, 2),
            "total_actual": round(total_actual, 2),
            "net_variance": round(net_variance, 2),
            "net_variance_pct": round(net_variance_pct, 2),
            "cost_centers": sorted(df["cost_center"].dropna().astype(str).unique().tolist())
            if "cost_center" in df
            else [],
            "gl_accounts": sorted(df["gl_account"].dropna().astype(str).unique().tolist()) if "gl_account" in df else [],
            "periods": sorted(df["period"].dropna().astype(int).unique().tolist()) if "period" in df else [],
            "largest_variance_row": _serialise_records(pd.DataFrame(largest)),
        }

    def _format_profile_answer(self, source: DatasetSource) -> str:
        profile = self._profile_dict(source)
        largest = profile["largest_variance_row"][0] if profile["largest_variance_row"] else {}
        lines = [
            f"I’m looking at `{source}` with {profile['rows']:,} rows.",
            "",
            f"- Total budget: {_money(profile['total_budget'])}",
            f"- Total actuals: {_money(profile['total_actual'])}",
            f"- Net variance: {_money(profile['net_variance'])} ({_pct(profile['net_variance_pct'])})",
            f"- Cost centers: {', '.join(profile['cost_centers']) or 'None'}",
            f"- G/L accounts: {', '.join(profile['gl_accounts'][:8])}",
        ]
        if largest:
            lines.append(
                "- Largest row: "
                f"{largest.get('period_label', '')} / {largest.get('cost_center', '')} / "
                f"{largest.get('gl_account', '')} at {_money(largest.get('variance', 0.0))}."
            )
        lines.extend(
            [
                "",
                "Try asking: `summarise by G/L account`, `show IT Ops Cloud Hosting`, "
                "or `export Marketing Campaigns to Excel`.",
            ]
        )
        return "\n".join(lines)

    def _format_help_answer(self) -> str:
        return self._format_usage_guide_answer()

    def _format_greeting_answer(self) -> str:
        return "\n".join(
            [
                "Hello. I’m your Finance Data Copilot for this Budget vs Actual app.",
                "",
                "You can ask me things like:",
                "- `what is the biggest variance in December?`",
                "- `which was our best cost saving last month?`",
                "- `where are we overspending last month?`",
                "- `why did Marketing overspend in November?`",
                "",
                "Ask `how do I use this app?` when you want the full workflow guide.",
            ]
        )

    def _format_usage_guide_answer(self) -> str:
        return "\n".join(
            [
                "Here is the end-to-end guide for using AI Finance Agent.",
                "This guide covers Actuals & investigation. For budgets, forecasts, scenarios, business drivers, and cash planning, select Planning & forecasting in the sidebar.",
                "",
                "**1. Load Data**",
                "- Use `Demo dataset` to explore synthetic data, or upload your own Budget vs Actual CSV/XLSX.",
                "- Required upload columns: `fiscal_year`, `period`, `cost_center`, `gl_account`, `budget`, `actual`.",
                "",
                "**2. Set Materiality**",
                "- The sidebar threshold controls what gets flagged as material.",
                "- A row is flagged when absolute variance percentage or amount exceeds the threshold. Nonzero activity with zero budget is always flagged.",
                "",
                "**3. Review The Workbench**",
                "- `Refine your view` controls the KPI cards, table, charts, quick actions, chatbot current view, and AI analysis scope.",
                "- Filter by cost center, G/L account, month/period, quarter, and variance direction.",
                "- Use `Material variances` for material exceptions or `Full ledger` when you need to investigate all rows.",
                "",
                "**4. Run AI Analysis**",
                "- `Generate executive brief` sends the selected view into the analyst/storyteller pipeline.",
                "- Python calculates all numbers first. The AI only writes the executive explanation after validated figures are prepared.",
                "- Use `local fallback` for deterministic demo mode, or OpenAI/Claude when API keys are configured.",
                "",
                "**5. Use The Chatbot**",
                "- Ask natural business questions about the full raw ledger, flagged rows, or current filtered view.",
                "- Say `raw data` or switch the workbench to `Full ledger` when you want non-material rows included.",
                "- I can answer totals, best savings, overspends, trends, month-to-month movement, root-cause investigations, and management actions.",
                "- I can create Excel workbooks for extracts, biggest variance investigations, and month-to-month comparisons.",
                "",
                "**Useful Chat Prompts**",
                "- `what is the biggest variance in December?`",
                "- `which was our best cost saving last month?`",
                "- `how much did we overspend last month?`",
                "- `show departments below budget in Q2`",
                "- `give me accounts above budget in December`",
                "- `why did Marketing overspend in November?`",
                "- `what changed between October and November?`",
                "- `what actions should we take on overspends last month?`",
                "- `export biggest variance investigation in December to Excel`",
                "",
                "**Calculation Controls**",
                "- The app does not let the LLM perform raw arithmetic.",
                "- Variance is calculated in Python as `Actual - Budget`.",
                "- Variance percentage is calculated in Python as `(Variance / Budget) * 100`.",
                "- Guardrails reject unsupported periods. Numeric checks reject unsupported figures; unknown causes remain unverified. Review conclusions against source evidence.",
                "- If the dataset does not contain something like vendor name, approver, PO number, or live ERP status, I will say so instead of inventing it.",
            ]
        )

    def _format_no_data_answer(self, source: DatasetSource, filters: dict[str, Any]) -> str:
        return "\n".join(
            [
                "I found no matching rows, so I did not guess.",
                f"- Dataset searched: `{source}`",
                f"- Filters applied: {_filter_sentence(filters)}",
                "",
                "Try widening the scope, for example remove the month/quarter, switch from `current view` to `raw data`, or use one of the available cost centers/G/L accounts from the table.",
            ]
        )

    def _format_out_of_scope_answer(self, question: str) -> str:
        del question
        return "\n".join(
            [
                "I cannot answer that reliably from the current Budget vs Actual dataset.",
                "",
                "The uploaded/generated data contains financial rows by period, cost center, G/L account, budget, actual, variance, and optional driver notes. It does not contain invoice approvers, vendor master details, PO numbers, payment status, live ERP workflow history, or email/Slack/Teams delivery credentials.",
                "",
                "If you upload those columns or connect a delivery integration later, I can include them in the chatbot and reports.",
            ]
        )

    def _format_non_finance_answer(self) -> str:
        return (
            "I am configured strictly as an Enterprise Financial Analytics Assistant. "
            "I can only assist with budget analysis, variance reporting, and financial data queries."
        )

    def _format_exchange_rate_answer(self, question: str) -> str:
        fx_request = _extract_fx_request(question)
        if fx_request is None:
            return "\n".join(
                [
                    "I can look up exchange rates, but I need two currencies.",
                    "",
                    "Try: `USD to SGD`, `convert 100 USD to SGD`, or `EUR/GBP exchange rate`.",
                ]
            )

        result = _fetch_exchange_rate(
            fx_request["base_currency"],
            fx_request["quote_currency"],
            fx_request["amount"],
        )
        if result.get("error"):
            return "\n".join(
                [
                    "I could not fetch that exchange rate, so I did not estimate it.",
                    f"- Reason: {result['error']}",
                    "- Source attempted: Frankfurter public exchange-rate API",
                ]
            )

        converted = Decimal(str(result["converted_amount"]))
        amount = Decimal(str(result["amount"]))
        rate = Decimal(str(result["rate"]))
        return "\n".join(
            [
                f"{amount:,.2f} {result['base_currency']} = {converted:,.2f} {result['quote_currency']}.",
                f"- Rate: 1 {result['base_currency']} = {rate:,.6f} {result['quote_currency']}",
                f"- Date: {result['date']}",
                "- Source: Frankfurter public exchange-rate API",
            ]
        )

    def _format_upload_schema_answer(self) -> str:
        schema = _upload_schema_dict()
        return "\n".join(
            [
                "For upload, your file should be a Budget vs Actual extract in `.csv` or `.xlsx` format.",
                "",
                "Required columns:",
                *[f"- `{column}`: {description}" for column, description in schema["required_columns"].items()],
                "",
                "Optional columns:",
                *[f"- `{column}`: {description}" for column, description in schema["optional_columns"].items()],
                "",
                "Minimum example row:",
                "`2026, 10, Marketing, Marketing Campaigns, 194700.00, 245300.00`",
                "",
                "Accepted aliases include `cost_centre`, `costcenter`, `gl`, `account`, `actuals`, `budget_amount`, `actual_amount`, and `fy`.",
            ]
        )

    def _format_unclear_answer(self, question: str) -> str:
        del question
        return "\n".join(
            [
                "I’m not confident I understood the request, so I’m not going to give a made-up answer.",
                "",
                "Please ask using one of these patterns:",
                "- `what is the biggest variance in December?`",
                "- `which was our best cost saving last month?`",
                "- `where are we overspending last month?`",
                "- `why did Marketing overspend in November?`",
                "- `what actions should we take on overspends last month?`",
                "- `top 10 variances by account`",
                "- `show Marketing Campaigns rows`",
                "- `export Q4 unfavorable variances to Excel`",
                "- `compare budget vs actual for IT Ops`",
            ]
        )

    def _format_ranked_variances_answer(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        group_by: GroupBy,
        top_n: int,
        title: str = "top variance groups",
    ) -> str:
        summary = self._summary_dataframe(df, group_by=group_by, top_n=top_n)
        columns = [
            *[col for col in ["cost_center", "gl_account", "period_label"] if col in summary.columns],
            "budget",
            "actual",
            "variance",
            "variance_pct",
            "rows",
        ]
        lines = [
            f"Here is the {title} from `{source}`.",
            f"Filters applied: {_filter_sentence(filters)}.",
            "",
            _dataframe_to_markdown(summary, columns),
        ]
        return "\n".join(lines)

    def _format_executive_snapshot_answer(self, df: pd.DataFrame, source: DatasetSource, filters: dict[str, Any]) -> str:
        """Create a concise management-ready Budget vs Actual summary."""

        total_budget = float(df["budget"].sum()) if "budget" in df else 0.0
        total_actual = float(df["actual"].sum()) if "actual" in df else 0.0
        variance = total_actual - total_budget
        variance_pct = (variance / total_budget) * 100 if total_budget else float("nan")
        direction = "unfavorable" if variance >= 0 else "favorable"
        top_drivers = self._summary_dataframe(df, group_by="cost_center_gl_account", top_n=5)
        largest = top_drivers.iloc[0].to_dict() if not top_drivers.empty else {}
        scope_label = _scope_label(filters)

        lines = [
            f"Management finance summary for **{scope_label}** from `{source}`.",
            "",
            "**Executive Summary**",
            f"- **Total Budget**: {_money(total_budget)}",
            f"- **Total Actuals**: {_money(total_actual)}",
            f"- **Net Variance**: {_money(variance)} ({_pct(variance_pct)}), {direction}",
            f"- **Rows Reviewed**: {len(df):,}",
        ]
        if any(filters.values()):
            lines.append(f"- **Scope / Filters**: {_filter_sentence(filters)}")

        if largest:
            largest_direction = "unfavorable" if float(largest["variance"]) >= 0 else "favorable"
            lines.extend(
                [
                    "",
                    "**Main Talking Point**",
                    (
                        f"- Largest driver is `{largest['cost_center']} / {largest['gl_account']}` "
                        f"with variance of **{_money(float(largest['variance']))}** "
                        f"({_pct(largest['variance_pct'])}), {largest_direction}."
                    ),
                ]
            )

        lines.extend(
            [
                "",
                "**Top Variance Drivers**",
                _dataframe_to_markdown(
                    top_drivers,
                    ["cost_center", "gl_account", "budget", "actual", "variance", "variance_pct", "rows"],
                ),
                "",
                "**Suggested Management Actions**",
                "- Ask cost center owners to confirm whether the top variances are timing-related or true run-rate changes.",
                "- Reforecast the largest unfavorable drivers if they are expected to persist.",
                "- Separate one-off timing items from structural overspend before presenting the final management view.",
            ]
        )
        return "\n".join(lines)

    def _format_period_comparison_answer(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        period_a: int,
        period_b: int,
    ) -> str:
        summary = self._period_comparison_summary(df, period_a, period_b)
        drivers = self._period_comparison_dataframe(df, period_a, period_b, top_n=8)
        return "\n".join(
            [
                f"Here is the cost movement from {_period_name(period_a)} to {_period_name(period_b)} using `{source}`.",
                f"Filters applied: {_filter_sentence(filters)}.",
                "",
                f"- {_period_name(period_a)} actual: {_money(summary['period_a_actual'])}",
                f"- {_period_name(period_b)} actual: {_money(summary['period_b_actual'])}",
                f"- Actual cost difference: {_signed_money(summary['actual_difference'])} "
                f"({_pct(summary['actual_difference_pct'])})",
                f"- Budget difference: {_signed_money(summary['budget_difference'])}",
                f"- Variance movement: {_signed_money(summary['variance_difference'])}",
                "",
                "Top movement drivers:",
                _dataframe_to_markdown(
                    drivers,
                    [
                        "cost_center",
                        "gl_account",
                        "period_a_actual",
                        "period_b_actual",
                        "actual_difference",
                        "actual_difference_pct",
                        "period_a_variance",
                        "period_b_variance",
                    ],
                ),
                "",
                "For the workbook, ask: `export this period comparison to Excel`.",
            ]
        )

    def _format_biggest_variance_answer(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        group_by: GroupBy = "cost_center_gl_account",
    ) -> str:
        if group_by != "cost_center_gl_account":
            summary = self._summary_dataframe(df, group_by=group_by, top_n=1)
            if summary.empty:
                return f"I could not find {_variance_focus_phrase(filters)} because `{source}` has no matching rows."
            record = summary.iloc[0].to_dict()
            label_columns = [col for col in ["cost_center", "gl_account", "period_label"] if col in summary.columns]
            label = " / ".join(str(record[col]) for col in label_columns)
            direction = "unfavorable" if float(record["variance"]) >= 0 else "favorable"
            lines = [
                f"The {_variance_focus_phrase(filters)} by `{group_by}` in `{source}` is `{label}`.",
                "",
                f"- Budget: {_money(float(record['budget']))}",
                f"- Actual: {_money(float(record['actual']))}",
                f"- Variance: {_money(float(record['variance']))} ({_pct(record['variance_pct'])}), {direction}",
                f"- Rows included: {int(record['rows'])}",
            ]
            if any(filters.values()):
                lines.append(f"- Filters applied: {_filter_sentence(filters)}")
            return "\n".join(lines)

        driver = self._largest_variance_driver(df)
        if not driver:
            return f"I could not find {_variance_focus_phrase(filters)} because `{source}` has no matching rows."

        largest_row = self._largest_variance_row(df)
        notes = self._investigation_notes(driver)
        lines = [
            f"The {_variance_focus_phrase(filters)} driver in `{source}` is `{driver['cost_center']} / {driver['gl_account']}`.",
            "",
            f"- Budget: {_money(driver['budget'])}",
            f"- Actual: {_money(driver['actual'])}",
            f"- Variance: {_money(driver['variance'])} ({_pct(driver['variance_pct'])}), {driver['variance_direction'].lower()}",
            f"- Rows included: {driver['rows']}",
            f"- Periods: {driver['periods']}",
            f"- Likely root cause: {notes['likely_root_cause']}",
            f"- Investigation focus: {notes['investigation_focus']}",
        ]
        if largest_row:
            lines.extend(
                [
                    "",
                    "Largest individual ledger row:",
                    (
                        f"- `{largest_row.get('period_label', '')} / {largest_row.get('cost_center', '')} / "
                        f"{largest_row.get('gl_account', '')}` at {_money(float(largest_row.get('variance', 0.0)))} "
                        f"({_pct(largest_row.get('variance_pct', 0.0))})"
                    ),
                ]
            )
        lines.append("")
        lines.append("For the focused workbook, ask: `export biggest variance investigation to Excel`.")
        if any(filters.values()):
            lines.append(f"Filters applied: {_filter_sentence(filters)}.")
        return "\n".join(lines)

    def _format_total_variance_answer(self, df: pd.DataFrame, source: DatasetSource, filters: dict[str, Any]) -> str:
        total_budget = float(df["budget"].sum()) if "budget" in df else 0.0
        total_actual = float(df["actual"].sum()) if "actual" in df else 0.0
        variance = total_actual - total_budget
        variance_pct = (variance / total_budget) * 100 if total_budget else float("nan")
        direction = "unfavorable" if variance >= 0 else "favorable"
        if filters.get("direction") == "Favorable":
            headline = f"For `{source}`, total cost savings are {_money(abs(variance))} ({_pct(abs(variance_pct))}), favorable."
        elif filters.get("direction") == "Unfavorable":
            headline = f"For `{source}`, total overspend is {_money(abs(variance))} ({_pct(abs(variance_pct))}), unfavorable."
        else:
            headline = f"For `{source}`, total variance is {_money(variance)} ({_pct(variance_pct)}), {direction}."

        lines = [
            headline,
            f"- Total budget: {_money(total_budget)}",
            f"- Total actuals: {_money(total_actual)}",
            f"- Rows included: {len(df):,}",
        ]
        if any(filters.values()):
            lines.append(f"- Filters applied: {_filter_sentence(filters)}")
        return "\n".join(lines)

    def _format_amount_lookup_answer(
        self,
        df: pd.DataFrame,
        source: DatasetSource,
        filters: dict[str, Any],
        question: str,
    ) -> str:
        total_budget = float(df["budget"].sum()) if "budget" in df else 0.0
        total_actual = float(df["actual"].sum()) if "actual" in df else 0.0
        variance = total_actual - total_budget
        variance_pct = (variance / total_budget) * 100 if total_budget else float("nan")
        wants_budget_only = "budget" in question and not any(token in question for token in ["actual", "spend", "spent"])
        wants_actual_only = any(token in question for token in ["actual", "spend", "spent"]) and "budget" not in question

        if filters.get("direction") == "Favorable":
            headline = f"Total cost savings for `{source}` are {_money(abs(variance))}."
        elif filters.get("direction") == "Unfavorable":
            headline = f"Total overspend for `{source}` is {_money(abs(variance))}."
        elif wants_budget_only:
            headline = f"Total budget for `{source}` is {_money(total_budget)}."
        elif wants_actual_only:
            headline = f"Total actual spend for `{source}` is {_money(total_actual)}."
        else:
            headline = f"For `{source}`, budget is {_money(total_budget)} and actuals are {_money(total_actual)}."

        lines = [
            headline,
            f"- Variance: {_money(variance)} ({_pct(variance_pct)})",
            f"- Rows included: {len(df):,}",
        ]
        if any(filters.values()):
            lines.append(f"- Filters applied: {_filter_sentence(filters)}")
        return "\n".join(lines)

    def _format_risk_action_answer(self, df: pd.DataFrame, source: DatasetSource, filters: dict[str, Any]) -> str:
        total_budget = float(df["budget"].sum()) if "budget" in df else 0.0
        total_actual = float(df["actual"].sum()) if "actual" in df else 0.0
        variance = total_actual - total_budget
        variance_pct = (variance / total_budget) * 100 if total_budget else float("nan")
        risk_df = df[df["variance"] > 0].copy() if "variance" in df else df.copy()
        top_risks = self._summary_dataframe(risk_df if not risk_df.empty else df, group_by="cost_center_gl_account", top_n=5)

        lines = [
            f"Here is the management action view from `{source}`.",
            f"Filters applied: {_filter_sentence(filters)}.",
            "",
            "**Risk Position**",
            f"- Net variance: {_money(variance)} ({_pct(variance_pct)})",
            f"- Total budget: {_money(total_budget)}",
            f"- Total actuals: {_money(total_actual)}",
        ]

        if risk_df.empty:
            lines.extend(
                [
                    "- No unfavorable variance rows were found in this scope.",
                    "- Treat favorable savings as provisional until cost owners confirm whether they are permanent savings or delayed spend.",
                ]
            )
        else:
            lines.append(f"- Unfavorable rows requiring review: {len(risk_df):,}")

        lines.extend(["", "**Priority Drivers & Actions**"])
        for idx, row in enumerate(top_risks.to_dict("records"), start=1):
            driver = {
                **row,
                "driver_type": self._infer_driver_type(str(row.get("cost_center", "")), str(row.get("gl_account", ""))),
                "variance_direction": "Unfavorable" if float(row.get("variance", 0.0)) >= 0 else "Favorable",
            }
            notes = self._investigation_notes(driver)
            lines.extend(
                [
                    (
                        f"{idx}. `{row.get('cost_center', '')} / {row.get('gl_account', '')}`: "
                        f"{_money(float(row.get('variance', 0.0)))} ({_pct(row.get('variance_pct', 0.0))})"
                    ),
                    f"   Action: {notes['recommended_action']}",
                ]
            )

        lines.extend(
            [
                "",
                "**Boss Talking Points**",
                "- Separate timing items from true run-rate issues before committing to a forecast change.",
                "- Ask each cost owner for cause, permanence, and recovery plan.",
                "- Reforecast only drivers that are expected to persist into the next period.",
            ]
        )
        return "\n".join(lines)

    def _period_comparison_summary(self, df: pd.DataFrame, period_a: int, period_b: int) -> dict[str, Any]:
        period_df = df[df["period"].astype(int).isin([period_a, period_b])].copy()
        if not {period_a, period_b}.issubset(set(period_df["period"].astype(int))):
            raise ValueError("Both comparison periods must have data; a missing period is not zero.")
        first = period_df[period_df["period"].astype(int) == period_a]
        second = period_df[period_df["period"].astype(int) == period_b]
        first_budget = float(first["budget"].sum())
        second_budget = float(second["budget"].sum())
        first_actual = float(first["actual"].sum())
        second_actual = float(second["actual"].sum())
        first_variance = float(first["variance"].sum())
        second_variance = float(second["variance"].sum())
        actual_difference = second_actual - first_actual
        actual_difference_pct = (actual_difference / first_actual) * 100 if first_actual else float("nan")
        return {
            "period_a": period_a,
            "period_a_label": _period_name(period_a),
            "period_a_budget": round(first_budget, 2),
            "period_a_actual": round(first_actual, 2),
            "period_a_variance": round(first_variance, 2),
            "period_b": period_b,
            "period_b_label": _period_name(period_b),
            "period_b_budget": round(second_budget, 2),
            "period_b_actual": round(second_actual, 2),
            "period_b_variance": round(second_variance, 2),
            "budget_difference": round(second_budget - first_budget, 2),
            "actual_difference": round(actual_difference, 2),
            "actual_difference_pct": round(actual_difference_pct, 2),
            "variance_difference": round(second_variance - first_variance, 2),
            "rows_compared": int(len(period_df)),
        }

    def _period_comparison_dataframe(
        self,
        df: pd.DataFrame,
        period_a: int,
        period_b: int,
        top_n: int,
    ) -> pd.DataFrame:
        period_df = df[df["period"].astype(int).isin([period_a, period_b])].copy()
        if period_df.empty:
            return pd.DataFrame()

        grouped = (
            period_df.groupby(["cost_center", "gl_account", "period"], dropna=False)
            .agg(budget=("budget", "sum"), actual=("actual", "sum"), variance=("variance", "sum"), rows=("variance", "size"))
            .reset_index()
        )
        base_keys = grouped[["cost_center", "gl_account"]].drop_duplicates().to_dict("records")
        rows: list[dict[str, Any]] = []
        for key in base_keys:
            first = grouped[
                (grouped["cost_center"] == key["cost_center"])
                & (grouped["gl_account"] == key["gl_account"])
                & (grouped["period"].astype(int) == period_a)
            ]
            second = grouped[
                (grouped["cost_center"] == key["cost_center"])
                & (grouped["gl_account"] == key["gl_account"])
                & (grouped["period"].astype(int) == period_b)
            ]
            first_values = first.iloc[0].to_dict() if not first.empty else {}
            second_values = second.iloc[0].to_dict() if not second.empty else {}
            first_actual = float(first_values.get("actual", 0.0))
            second_actual = float(second_values.get("actual", 0.0))
            actual_difference = second_actual - first_actual
            rows.append(
                {
                    "cost_center": key["cost_center"],
                    "gl_account": key["gl_account"],
                    "period_a": period_a,
                    "period_b": period_b,
                    "period_a_actual": round(first_actual, 2),
                    "period_b_actual": round(second_actual, 2),
                    "actual_difference": round(actual_difference, 2),
                    "actual_difference_pct": round((actual_difference / first_actual) * 100, 2)
                    if first_actual
                    else 0.0,
                    "period_a_budget": round(float(first_values.get("budget", 0.0)), 2),
                    "period_b_budget": round(float(second_values.get("budget", 0.0)), 2),
                    "budget_difference": round(
                        float(second_values.get("budget", 0.0)) - float(first_values.get("budget", 0.0)),
                        2,
                    ),
                    "period_a_variance": round(float(first_values.get("variance", 0.0)), 2),
                    "period_b_variance": round(float(second_values.get("variance", 0.0)), 2),
                    "variance_difference": round(
                        float(second_values.get("variance", 0.0)) - float(first_values.get("variance", 0.0)),
                        2,
                    ),
                    "rows": int(first_values.get("rows", 0)) + int(second_values.get("rows", 0)),
                }
            )

        result = pd.DataFrame(rows)
        result["abs_actual_difference"] = result["actual_difference"].abs()
        return result.sort_values("abs_actual_difference", ascending=False).drop(columns=["abs_actual_difference"]).head(top_n)

    def _format_glossary_answer(self, question: str) -> str:
        lookup = _normalise_lookup(question)
        matched = _best_column_match(lookup, list(COLUMN_GLOSSARY))
        if matched:
            return f"`{matched}`: {COLUMN_GLOSSARY[matched]}"
        return "\n".join(f"- `{column}`: {definition}" for column, definition in COLUMN_GLOSSARY.items())

    def _largest_variance_driver(self, df: pd.DataFrame) -> dict[str, Any] | None:
        if df.empty or not {"cost_center", "gl_account", "budget", "actual", "variance"}.issubset(df.columns):
            return None
        grouped = (
            df.groupby(["cost_center", "gl_account"], dropna=False)
            .agg(
                budget=("budget", "sum"),
                actual=("actual", "sum"),
                variance=("variance", "sum"),
                rows=("variance", "size"),
                max_abs_row_variance=("abs_variance", "max"),
                periods=(
                    "period_label" if "period_label" in df.columns else "period",
                    lambda values: ", ".join(sorted({str(value) for value in values})),
                ),
            )
            .reset_index()
        )
        grouped["variance_pct"] = grouped.apply(
            lambda row: (row["variance"] / row["budget"]) * 100 if row["budget"] else float("nan"),
            axis=1,
        )
        grouped["abs_total_variance"] = grouped["variance"].abs()
        top = grouped.sort_values(["abs_total_variance", "max_abs_row_variance"], ascending=False).iloc[0].to_dict()
        top["variance_direction"] = "Unfavorable" if float(top["variance"]) >= 0 else "Favorable"
        top["driver_type"] = self._infer_driver_type(str(top["cost_center"]), str(top["gl_account"]))
        for key in ["budget", "actual", "variance", "variance_pct", "abs_total_variance", "max_abs_row_variance"]:
            top[key] = round(float(top[key]), 2)
        top["rows"] = int(top["rows"])
        return top

    def _largest_variance_row(self, df: pd.DataFrame) -> dict[str, Any]:
        if df.empty or "abs_variance" not in df.columns:
            return {}
        return _serialise_records(df.sort_values("abs_variance", ascending=False).head(1))[0]

    def _infer_driver_type(self, cost_center: str, gl_account: str) -> str:
        candidates = self.raw_df[
            (self.raw_df["cost_center"].astype(str) == cost_center)
            & (self.raw_df["gl_account"].astype(str) == gl_account)
        ]
        if "synthetic_driver" in candidates:
            drivers = candidates["synthetic_driver"].dropna().astype(str)
            drivers = drivers[drivers != "normal"]
            if not drivers.empty:
                return str(drivers.mode().iloc[0])

        return "unknown"

    def _investigation_notes(self, driver: dict[str, Any]) -> dict[str, str]:
        driver_type = str(driver.get("driver_type", "unknown"))
        direction = str(driver.get("variance_direction", "Unfavorable"))
        account = str(driver.get("gl_account", "the account"))
        notes = {
            "price": {
                "likely_root_cause": "Source-supplied price classification; confirm against rate and contract evidence.",
                "investigation_focus": f"Check vendor rates, contract changes, usage tiers, and accrual accuracy for {account}.",
                "recommended_action": f"Confirm whether {account} run-rate needs reforecasting or contract remediation.",
            },
            "volume": {
                "likely_root_cause": "Source-supplied volume classification; confirm against activity evidence.",
                "investigation_focus": f"Validate consumption, project scope, headcount, campaign volume, or service demand for {account}.",
                "recommended_action": f"Decide whether {account} demand is temporary or should be rebased in forecast.",
            },
            "timing": {
                "likely_root_cause": "Source-supplied timing classification; confirm against invoice and accrual dates.",
                "investigation_focus": f"Match invoices and accruals for {account} against the budget calendar.",
                "recommended_action": "Separate permanent savings/overrun from timing that may reverse in later periods.",
            },
        }.get(
            driver_type,
            {
                "likely_root_cause": "Root cause requires finance business partner validation.",
                "investigation_focus": "Review invoice support, cost owner explanation, and budget phasing.",
                "recommended_action": "Assign an owner to validate cause and propose forecast treatment.",
            },
        )
        if direction == "Favorable":
            notes = {
                **notes,
                "recommended_action": f"{notes['recommended_action']} Confirm whether the favorable variance is a true saving or delayed spend.",
            }
        return notes

    def _summary_dataframe(
        self,
        df: pd.DataFrame,
        group_by: GroupBy,
        top_n: int,
    ) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["budget", "actual", "variance", "variance_pct", "rows"])

        group_columns = {
            "cost_center": ["cost_center"],
            "gl_account": ["gl_account"],
            "cost_center_gl_account": ["cost_center", "gl_account"],
            "period": ["period_label"] if "period_label" in df.columns else ["period"],
        }[group_by]
        grouped = (
            df.groupby(group_columns, dropna=False)
            .agg(budget=("budget", "sum"), actual=("actual", "sum"), variance=("variance", "sum"), rows=("variance", "size"))
            .reset_index()
        )
        grouped["variance_pct"] = grouped.apply(
            lambda row: (row["variance"] / row["budget"]) * 100 if row["budget"] else float("nan"),
            axis=1,
        )
        grouped["abs_variance"] = grouped["variance"].abs()
        grouped = grouped.sort_values("abs_variance", ascending=False).head(top_n)
        return grouped.drop(columns=["abs_variance"]).reset_index(drop=True)

    def _filter_dataframe(
        self,
        df: pd.DataFrame,
        cost_center: str | None = None,
        gl_account: str | None = None,
        period: int | None = None,
        periods: list[int] | tuple[int, ...] | None = None,
        quarter: Quarter | None = None,
        direction: VarianceDirection | None = None,
    ) -> pd.DataFrame:
        result = df.copy()
        if cost_center and "cost_center" in result:
            needle = _normalise_lookup(cost_center)
            result = result[result["cost_center"].astype(str).map(_normalise_lookup).eq(needle)]
        if gl_account and "gl_account" in result:
            needle = _normalise_lookup(gl_account)
            result = result[result["gl_account"].astype(str).map(_normalise_lookup).eq(needle)]
        if period is not None and "period" in result:
            result = result[result["period"].astype(int) == int(period)]
        if periods is not None and "period" in result:
            valid_periods = [int(value) for value in periods if 1 <= int(value) <= 12]
            result = result[result["period"].astype(int).isin(valid_periods)]
        if quarter is not None and "quarter" in result:
            result = result[result["quarter"].astype(str).str.upper() == quarter.upper()]
        if direction and "variance_direction" in result:
            result = result[result["variance_direction"].astype(str).str.lower() == direction.lower()]
        return result.reset_index(drop=True)

    def _detect_source(self, lowered_question: str) -> DatasetSource:
        if re.search(r"\b(?:raw|all data|all rows|full ledger)\b", lowered_question):
            return "raw"
        if re.search(r"\b(?:flagged|significant|material)\b", lowered_question):
            return "flagged"
        return "current_view"

    def _unresolved_scope(self, question: str) -> str | None:
        """Fail clearly when a named entity is absent or a multi-entity route is ambiguous."""
        lookup = _normalise_lookup(question)
        choices = {column: self.raw_df[column].dropna().astype(str).unique().tolist()
                   for column in ("cost_center", "gl_account") if column in self.raw_df}
        matches = {}
        for column, values in choices.items():
            remaining = lookup
            matches[column] = []
            # Match the longest exact name first: Finance Operations is one entity.
            for value in sorted(values, key=len, reverse=True):
                phrase = _normalise_lookup(value)
                pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
                if re.search(pattern, remaining):
                    matches[column].append(value)
                    remaining = re.sub(pattern, " ", remaining)
        if any(len(values) > 1 for values in matches.values()):
            return "This question names multiple departments or accounts. Select them together in the dashboard filters, choose Current dashboard view, and ask the question for that view. I won't silently select only one."
        known = [value for values in choices.values() for value in values]
        patterns = [
            r"\b(?:cost cent(?:er|re)|department|(?:g/l |gl )?account)\s+(?:named\s+)?(.+?)(?=\s+(?:in|for|during|by|period|quarter|with)\b|[?;,]|$)",
            r"\b(?:did|does|has)\s+(.+?)\s+(?:spend|spent|save|overspend|underspend)\b",
            r"\bfor\s+(.+?)(?=\s+(?:in|during|by|period|quarter|to excel)\b|[?;,]|$)",
            r"\bshow\s+(.+?)\s+(?:rows|records|transactions)\b",
        ]
        generic = {"we", "us", "me", "our", "the", "a", "an", "all", "any", "each", "this", "last", "current", "selected", "filtered", "raw", "full", "flagged", "material", "significant", "above", "below", "over", "under", "has", "have", "had", "is", "was", "saved", "spent", "with", "and", "or", "budget", "actual", "actuals", "variance", "variances", "spend", "spending", "cost", "costs", "data", "rows", "ledger", "month", "year", "period", "quarter", "summary", "business", "presentation"}
        for pattern in patterns:
            for match in re.finditer(pattern, question, re.IGNORECASE):
                candidate = match.group(1).strip(" `\"'.")
                normalized = _normalise_lookup(candidate)
                if not normalized:
                    continue
                has_known = any(_contains_normalised_phrase(normalized, value) for value in known)
                if has_known:
                    residual = normalized
                    for value in sorted(known, key=len, reverse=True):
                        residual = re.sub(r"(?<!\w)" + re.escape(_normalise_lookup(value)) + r"(?!\w)", " ", residual)
                    # A second, unknown entity must not disappear beside a known one.
                    if re.search(r"\b(?:and|or|versus|vs)\b", residual):
                        unexplained = [part for part in residual.split() if part not in generic]
                        if unexplained and _detect_period(" ".join(unexplained)) is None:
                            return "I couldn't match every requested department or account. Select the intended names in the dashboard filters before asking for that view."
                    continue
                first = normalized.split()[0]
                if first in generic or _detect_period(candidate.lower()) is not None or _detect_quarter(candidate.lower()) is not None:
                    continue
                return "I couldn't match the requested department or account to this ledger. Choose an exact name from the dashboard filters; I won't substitute whole-ledger totals."
        return None

    def _detect_filters(self, question: str) -> dict[str, Any]:
        lowered = question.lower()
        period_mentions = _detect_period_mentions(lowered)
        multi_periods = period_mentions if len(period_mentions) > 1 else None
        return {
            "cost_center": self._detect_choice(question, "cost_center"),
            "gl_account": self._detect_choice(question, "gl_account"),
            "period": None if multi_periods else _detect_period(lowered),
            "periods": multi_periods,
            "quarter": _detect_quarter(lowered),
            "direction": None if _is_net_budget_status_intent(lowered) else _detect_direction(lowered),
        }

    def _is_out_of_domain_question(self, lowered_question: str) -> bool:
        if not _looks_like_question(lowered_question):
            return False
        if _has_finance_domain_signal(lowered_question, self.raw_df):
            return False
        return True

    def _detect_choice(self, question: str, column: str) -> str | None:
        lookup = _normalise_lookup(question)
        choices = self.raw_df[column].dropna().astype(str).unique().tolist() if column in self.raw_df else []
        for choice in sorted(choices, key=len, reverse=True):
            if _contains_normalised_phrase(lookup, choice):
                return choice
        return None

    def _detect_group_by(
        self,
        lowered_question: str,
    ) -> GroupBy:
        if (
            "g/l" in lowered_question
            or "gl " in lowered_question
            or "by gl" in lowered_question
            or "account" in lowered_question
            or "accounts" in lowered_question
        ):
            return "gl_account"
        if (
            "cost center" in lowered_question
            or "cost centers" in lowered_question
            or "cost centre" in lowered_question
            or "cost centres" in lowered_question
            or "department" in lowered_question
            or "departments" in lowered_question
            or "area" in lowered_question
            or "areas" in lowered_question
        ):
            return "cost_center"
        if "period" in lowered_question or "month" in lowered_question:
            return "period"
        return "cost_center_gl_account"

    def _detect_largest_group_by(self, lowered_question: str) -> GroupBy:
        if "which month" in lowered_question or "which period" in lowered_question:
            return "period"
        if (
            "which cost center" in lowered_question
            or "which cost centers" in lowered_question
            or "which cost centre" in lowered_question
            or "which cost centres" in lowered_question
            or "by cost center" in lowered_question
            or "by cost centers" in lowered_question
            or "by cost centre" in lowered_question
            or "by cost centres" in lowered_question
            or "which department" in lowered_question
            or "which departments" in lowered_question
            or "by department" in lowered_question
            or "by departments" in lowered_question
            or "which area" in lowered_question
            or "which areas" in lowered_question
            or "by area" in lowered_question
            or "by areas" in lowered_question
        ):
            return "cost_center"
        if (
            "which account" in lowered_question
            or "which accounts" in lowered_question
            or "which g/l" in lowered_question
            or "which gl" in lowered_question
            or "by account" in lowered_question
            or "by accounts" in lowered_question
            or "by g/l" in lowered_question
            or "by gl" in lowered_question
        ):
            return "gl_account"
        return "cost_center_gl_account"

    def _memory_context(self, chat_history: list[dict[str, Any]]) -> dict[str, Any]:
        context: dict[str, Any] = {
            "filters": {},
            "source": None,
            "intent": None,
            "group_by": None,
            "period_pair": None,
        }
        for message in reversed(chat_history[-12:]):
            # Returned tables mention many accounts; only user requests define filters.
            if message.get("role") != "user":
                continue
            content = str(message.get("content", ""))
            if not content:
                continue
            lowered = content.lower()

            if context["intent"] is None:
                context["intent"] = _detect_intent_label(lowered)

            if context["period_pair"] is None:
                context["period_pair"] = _detect_period_pair(lowered)

            if context["group_by"] is None:
                if context["intent"] == "biggest":
                    context["group_by"] = self._detect_largest_group_by(lowered)
                elif context["intent"] in {"compare", "top_list", "trend"}:
                    context["group_by"] = self._detect_group_by(lowered)

            if context["source"] is None and _source_is_explicit(lowered):
                context["source"] = self._detect_source(lowered)

            detected = self._detect_filters(content)
            has_period_context = any(context["filters"].get(key) is not None for key in ("period", "periods", "quarter"))
            for key, value in detected.items():
                if has_period_context and key in {"period", "periods", "quarter"}:
                    continue
                if value is not None and key not in context["filters"]:
                    context["filters"][key] = value
            if not _is_followup_intent(lowered):
                break

        return context

    def _dataset(self, source: DatasetSource) -> pd.DataFrame:
        return {
            "raw": self.raw_df,
            "flagged": self.flagged_df,
            "current_view": self.current_view_df,
        }[source]

    @staticmethod
    def _prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        return calculate_variances(df)


def _recalculate_variance_columns(df: pd.DataFrame) -> pd.DataFrame:
    return calculate_variances(df)


def _flag_basis(df: pd.DataFrame) -> str:
    """Describe whether material rows were flagged by dollar, percent, or both."""

    if "flag_reason" in df and not df["flag_reason"].dropna().empty:
        reasons = sorted(set(df["flag_reason"].dropna().astype(str)))
        return ", ".join(reasons)
    return "Not flagged or threshold basis unavailable in current dataset."


def _excel_filename(source: DatasetSource, filters: dict[str, Any]) -> str:
    parts = ["variance", source]
    for key in ["cost_center", "gl_account", "period", "quarter", "direction"]:
        value = filters.get(key)
        if value:
            parts.append(str(value))
    slug = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_") for part in parts)
    return f"{slug.lower() or 'variance_extract'}.xlsx"


def _investigation_filename(driver: dict[str, Any]) -> str:
    parts = [
        "biggest_variance_investigation",
        str(driver.get("cost_center", "")),
        str(driver.get("gl_account", "")),
    ]
    slug = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_") for part in parts if part)
    return f"{slug.lower() or 'biggest_variance_investigation'}.xlsx"


def _period_comparison_filename(filters: dict[str, Any], period_a: int, period_b: int) -> str:
    parts = ["period_comparison"]
    for key in ["cost_center", "gl_account"]:
        if filters.get(key):
            parts.append(str(filters[key]))
    parts.extend([_period_name(period_a), "to", _period_name(period_b)])
    slug = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_") for part in parts if part)
    return f"{slug.lower() or 'period_comparison'}.xlsx"


MONTH_TO_PERIOD: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


PERIOD_TO_MONTH: dict[int, str] = {
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


def _period_name(period: int) -> str:
    return f"{PERIOD_TO_MONTH.get(int(period), f'P{int(period):02d}')} (P{int(period):02d})"


def _is_biggest_variance_intent(lowered_question: str) -> bool:
    size_words = [
        "biggest",
        "largest",
        "highest",
        "worst",
        "best",
        "top",
        "most",
        "most material",
        "maximum",
        "max",
        "main",
        "primary",
        "largest driver",
    ]
    variance_words = [
        "variance",
        "overrun",
        "overspend",
        "underspend",
        "save",
        "saving",
        "savings",
        "saved",
        "cost saving",
        "cost savings",
        "cost reduction",
        "cost pressure",
        "budget gap",
        "budget miss",
        "difference",
        "favorable",
        "favourable",
        "unfavorable",
        "unfavourable",
        "above budget",
        "below budget",
        "under budget",
        "over budget",
    ]
    return any(word in lowered_question for word in variance_words) and any(word in lowered_question for word in size_words)


def _is_period_comparison_intent(lowered_question: str) -> bool:
    if _detect_period_pair(lowered_question) is None:
        return False
    comparison_words = [
        "between",
        " vs ",
        " versus ",
        "compare",
        "compared",
        "difference",
        "movement",
        "change",
        "increase",
        "decrease",
        "from",
    ]
    subject_words = [
        "cost",
        "spend",
        "actual",
        "budget",
        "variance",
        "expense",
        "expenses",
        "financial",
        "finance",
        "performance",
        "changed",
        "happened",
        "movement",
    ]
    return any(word in lowered_question for word in comparison_words) and any(
        word in lowered_question for word in subject_words
    )


def _is_top_list_intent(lowered_question: str) -> bool:
    variance_words = [
        "variance",
        "variances",
        "overrun",
        "overruns",
        "overspend",
        "overspends",
        "overspending",
        "underspend",
        "underspends",
        "underspending",
        "save",
        "saving",
        "savings",
        "saved",
        "cost reduction",
        "cost pressure",
        "cost avoidance",
        "budget gap",
        "driver",
        "anomal",
        "outlier",
        "exception",
        "above budget",
        "below budget",
        "under budget",
        "over budget",
    ]
    if not any(word in lowered_question for word in variance_words):
        return False
    return bool(re.search(r"\btop\s+\d+\b", lowered_question)) or any(
        token in lowered_question
        for token in [
            "top variances",
            "top variance",
            "top drivers",
            "top overruns",
            "top overspends",
            "top savings",
            "top cost savings",
            "top underspends",
            "top outliers",
            "top exceptions",
            "largest savings",
            "biggest savings",
            "largest variances",
            "biggest variances",
            "biggest overruns",
            "material drivers",
        ]
    )


def _is_total_variance_intent(lowered_question: str) -> bool:
    total_words = ["total", "overall", "net", "sum", "are we", "did we", "how much"]
    variance_words = [
        "variance",
        "overrun",
        "overruns",
        "overspend",
        "overspends",
        "overspending",
        "underspend",
        "underspends",
        "underspending",
        "save",
        "saving",
        "savings",
        "saved",
        "cost saving",
        "cost savings",
        "budget gap",
        "above budget",
        "below budget",
        "under budget",
        "over budget",
    ]
    return any(word in lowered_question for word in variance_words) and any(word in lowered_question for word in total_words)


def _is_amount_lookup_intent(lowered_question: str) -> bool:
    amount_words = [
        "budget",
        "actual",
        "actuals",
        "spend",
        "spent",
        "cost",
        "costs",
        "expense",
        "expenses",
        "save",
        "saving",
        "savings",
        "saved",
        "overspend",
        "overspent",
        "overrun",
    ]
    return any(word in lowered_question for word in amount_words) and any(
        token in lowered_question for token in ["what", "how much", "total", "show", "give me"]
    )


def _is_investigation_intent(lowered_question: str) -> bool:
    investigation_terms = [
        "investigation",
        "investigate",
        "root cause",
        "root-cause",
        "explain why",
        "explain the variance",
        "explain this variance",
        "why did",
        "why are",
        "why is",
        "what caused",
        "what cause",
        "what drove",
        "what is driving",
        "what was driving",
        "driver analysis",
        "cause of",
        "reason for",
        "reasons for",
        "variance bridge",
    ]
    return any(term in lowered_question for term in investigation_terms) and _has_finance_domain_signal(lowered_question)


def _is_fx_intent(lowered_question: str) -> bool:
    if re.search(r"\b(?:exchange rates?|fx|currenc(?:y|ies)|convert)\b", lowered_question):
        return True
    currencies = "|".join(sorted(code.lower() for code in SUPPORTED_CURRENCIES))
    return bool(re.search(rf"\b(?:{currencies})\s*(?:to|/)\s*(?:{currencies})\b", lowered_question))


def _is_upload_schema_intent(lowered_question: str) -> bool:
    schema_words = ["what data", "what columns", "which columns", "required columns", "must i have", "need in my"]
    upload_words = ["upload", "file", "csv", "excel", "xlsx", "spreadsheet"]
    return any(term in lowered_question for term in schema_words) and any(term in lowered_question for term in upload_words)


def _is_greeting_intent(lowered_question: str) -> bool:
    clean = re.sub(r"[^a-z\s]", " ", lowered_question).strip()
    clean = re.sub(r"\s+", " ", clean)
    if _has_finance_domain_signal(clean):
        return False
    return clean in {
        "hi",
        "hello",
        "hey",
        "yo",
        "hiya",
        "good morning",
        "good afternoon",
        "good evening",
        "hi there",
        "hello there",
        "thanks",
        "thank you",
        "ok thanks",
        "okay thanks",
    }


def _is_help_intent(lowered_question: str) -> bool:
    return lowered_question.strip() in {"help", "examples", "what can you do", "how to use this"} or any(
        token in lowered_question for token in ["what can i ask", "sample prompts", "example prompts"]
    )


def _is_app_guide_intent(lowered_question: str) -> bool:
    guide_phrases = [
        "how to use",
        "how do i use",
        "how should i use",
        "walk me through",
        "walkthrough",
        "tutorial",
        "getting started",
        "user guide",
        "guide",
        "instructions",
        "end to end",
        "end-to-end",
        "workflow",
        "what is this app",
        "what does this app do",
        "how does this app work",
        "how does the app work",
        "how does it work",
        "how does ai analysis work",
        "how does the ai analysis work",
        "what does run ai analysis do",
        "what happens when i run ai analysis",
        "how does the analysis work",
        "how reliable is this",
        "can i trust",
        "why can i trust",
        "why should i trust",
        "how are numbers calculated",
        "how is variance calculated",
    ]
    if any(phrase in lowered_question for phrase in guide_phrases):
        return True
    return "analysis" in lowered_question and any(
        token in lowered_question for token in ["work", "works", "trust", "reliable", "confidence", "explain"]
    )


def _detect_intent_label(lowered_question: str) -> str | None:
    if _is_greeting_intent(lowered_question):
        return "greeting"
    if _is_app_guide_intent(lowered_question):
        return "guide"
    if _is_fx_intent(lowered_question):
        return "exchange_rate"
    if _is_upload_schema_intent(lowered_question):
        return "upload_schema"
    wants_export = any(token in lowered_question for token in ["excel", "xlsx", "export", "download", "pull"])
    if _is_period_comparison_intent(lowered_question):
        return "period_comparison"
    if _is_biggest_variance_intent(lowered_question):
        return "biggest"
    if _is_investigation_intent(lowered_question):
        return "investigation"
    if wants_export:
        return "export"
    if _is_top_list_intent(lowered_question):
        return "top_list"
    if _is_total_variance_intent(lowered_question):
        return "total_variance"
    if _is_trend_intent(lowered_question):
        return "trend"
    if _is_compare_intent(lowered_question):
        return "compare"
    if _is_amount_lookup_intent(lowered_question):
        return "amount_lookup"
    if _is_show_rows_intent(lowered_question):
        return "show_rows"
    return None


def _is_followup_intent(lowered_question: str) -> bool:
    short = len(lowered_question.split()) <= 5
    followup_terms = [
        "that",
        "same",
        "it",
        "those",
        "them",
        "what about",
        "how about",
        "for that",
        "for the same",
        "also",
        "then",
        "instead",
        "now",
    ]
    temporal_only = short and (
        _detect_period(lowered_question) is not None
        or _detect_quarter(lowered_question) is not None
        or _detect_direction(lowered_question) is not None
    )
    return temporal_only or any(term in lowered_question for term in followup_terms)


def _has_new_analysis_intent(lowered_question: str) -> bool:
    return any(
        check(lowered_question)
        for check in [
            _is_fx_intent,
            _is_period_comparison_intent,
            _is_biggest_variance_intent,
            _is_top_list_intent,
            _is_total_variance_intent,
            _is_trend_intent,
            _is_compare_intent,
            _is_amount_lookup_intent,
            _is_show_rows_intent,
            _is_executive_summary_intent,
            _is_general_performance_intent,
            _is_investigation_intent,
            _is_directional_listing_intent,
            _is_anomaly_review_intent,
            _is_risk_action_intent,
        ]
    )


def _merge_filters(current: dict[str, Any], remembered: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    replaced_period = any(current.get(key) is not None for key in ("period", "periods", "quarter"))
    for key, value in remembered.items():
        if replaced_period and key in {"period", "periods", "quarter"}:
            continue
        if merged.get(key) is None and value is not None:
            merged[key] = value
    return merged


def _source_is_explicit(lowered_question: str) -> bool:
    return bool(re.search(r"\b(?:raw|all data|all rows|full ledger|flagged|significant|material|current view|filtered view)\b", lowered_question))


def _has_explicit_group_by(lowered_question: str) -> bool:
    return any(
        token in lowered_question
        for token in [
            "by account",
            "by g/l",
            "by gl",
            "by cost center",
            "by cost centers",
            "by cost centre",
            "by cost centres",
            "by department",
            "by departments",
            "by month",
            "by period",
            "which account",
            "which g/l",
            "which gl",
            "which cost center",
            "which cost centers",
            "which cost centre",
            "which cost centres",
            "which department",
            "which departments",
            "account",
            "accounts",
            "cost center",
            "cost centers",
            "cost centre",
            "cost centres",
            "department",
            "departments",
            "area",
            "areas",
            "which month",
            "which period",
        ]
    )


def _is_net_budget_status_intent(lowered_question: str) -> bool:
    return any(
        phrase in lowered_question
        for phrase in [
            "are we over budget",
            "are we under budget",
            "are we above budget",
            "are we below budget",
            "did we exceed budget",
            "did we beat budget",
            "did we come in over budget",
            "did we come in under budget",
            "did we land over budget",
            "did we land under budget",
        ]
    )


def _is_out_of_scope_intent(lowered_question: str) -> bool:
    if "who approved" in lowered_question:
        return True
    out_of_scope_terms = [
        "email",
        "send to cfo",
        "send this report",
        "send the report",
        "share this report",
        "slack",
        "teams",
        "invoice approver",
        "approval workflow",
        "po number",
        "purchase order",
        "vendor name",
        "vendor master",
        "payment status",
        "paid yet",
        "sap workflow",
        "oracle workflow",
        "journal number",
    ]
    return any(term in lowered_question for term in out_of_scope_terms)


FINANCE_DOMAIN_TERMS: tuple[str, ...] = (
    "budget",
    "actual",
    "actuals",
    "variance",
    "var ",
    "cost",
    "costs",
    "spend",
    "spent",
    "expense",
    "expenses",
    "cost center",
    "cost centre",
    "department",
    "g/l",
    "gl ",
    "account",
    "ledger",
    "erp",
    "sap",
    "oracle",
    "fiscal",
    "period",
    "quarter",
    "month",
    "monthly",
    "forecast",
    "reforecast",
    "run rate",
    "run-rate",
    "cfo",
    "fp&a",
    "fpa",
    "finance",
    "financial",
    "management action",
    "action",
    "actions",
    "risk",
    "risks",
    "outlier",
    "outliers",
    "exception",
    "exceptions",
    "anomaly",
    "anomalies",
    "saving",
    "savings",
    "saved",
    "driver",
    "drivers",
    "overrun",
    "overspend",
    "underspend",
    "above budget",
    "below budget",
    "current view",
    "flagged",
    "material",
    "raw data",
    "excel",
    "xlsx",
    "csv",
    "upload",
    "report",
    "download",
    "export",
    "currency",
    "exchange rate",
    "fx",
)


def _has_finance_domain_signal(lowered_question: str, df: pd.DataFrame | None = None) -> bool:
    """Return True when the question is about finance data or app operations."""

    if any(term in lowered_question for term in FINANCE_DOMAIN_TERMS):
        return True
    if re.search(r"\bq[1-4]\b", lowered_question):
        return True
    if _detect_period_mentions(lowered_question):
        return True
    if _detect_quarter(lowered_question):
        return True
    if _is_help_intent(lowered_question) or _is_upload_schema_intent(lowered_question) or _is_fx_intent(lowered_question):
        return True

    lookup = _normalise_lookup(lowered_question)
    known_terms = list(COLUMN_GLOSSARY)
    if df is not None:
        for column in ["cost_center", "gl_account"]:
            if column in df:
                known_terms.extend(df[column].dropna().astype(str).unique().tolist())
    return any(_normalise_lookup(term) in lookup for term in known_terms if str(term).strip())


def _is_general_performance_intent(lowered_question: str) -> bool:
    return any(
        phrase in lowered_question
        for phrase in [
            "how are we doing",
            "how did we do",
            "where do we stand",
            "overall performance",
            "financial performance",
            "finance performance",
            "summarise finances",
            "summarize finances",
            "summarise financials",
            "summarize financials",
            "finance summary",
            "financial summary",
            "are we over budget",
            "are we under budget",
            "did we exceed budget",
            "did we beat budget",
            "what happened last month",
            "what happened this month",
            "what happened last quarter",
        ]
    )


def _is_executive_summary_intent(lowered_question: str) -> bool:
    if _is_top_list_intent(lowered_question):
        return False
    summary_signal = any(
        token in lowered_question
        for token in [
            "summary",
            "summarise",
            "summarize",
            "brief",
            "briefing",
            "presentation",
            "present",
            "boss",
            "management",
            "executive",
            "cfo",
        ]
    )
    finance_signal = _has_finance_domain_signal(lowered_question)
    period_signal = (
        _detect_period(lowered_question) is not None
        or _detect_quarter(lowered_question) is not None
        or any(token in lowered_question for token in ["last month", "previous month", "this month", "current month"])
    )
    return summary_signal and finance_signal and period_signal


def _is_directional_listing_intent(lowered_question: str) -> bool:
    if _detect_direction(lowered_question) is None or _is_net_budget_status_intent(lowered_question):
        return False
    return any(
        token in lowered_question
        for token in [
            "which",
            "where",
            "show",
            "list",
            "areas",
            "area",
            "departments",
            "department",
            "cost centers",
            "cost centre",
            "accounts",
            "drivers",
            "items",
            "lines",
        ]
    )


def _is_anomaly_review_intent(lowered_question: str) -> bool:
    return any(
        token in lowered_question
        for token in [
            "anomaly",
            "anomalies",
            "outlier",
            "outliers",
            "exception",
            "exceptions",
            "unusual",
            "odd",
            "strange",
        ]
    )


def _is_risk_action_intent(lowered_question: str) -> bool:
    return _has_finance_domain_signal(lowered_question) and any(
        token in lowered_question
        for token in [
            "risk",
            "risks",
            "action",
            "actions",
            "recommend",
            "recommendation",
            "recommendations",
            "what should i tell",
            "what should we tell",
            "talking points",
            "management view",
        ]
    )


def _is_trend_intent(lowered_question: str) -> bool:
    return any(token in lowered_question for token in ["trend", "by month", "monthly", "over time", "period trend"])


def _is_compare_intent(lowered_question: str) -> bool:
    return any(token in lowered_question for token in ["compare", "budget vs actual", "budget versus actual", "actual vs budget"])


def _is_show_rows_intent(lowered_question: str) -> bool:
    return any(
        token in lowered_question
        for token in ["show", "list", "rows", "detail", "data", "give me", "pull up", "display"]
    )


def _looks_like_question(lowered_question: str) -> bool:
    return lowered_question.endswith("?") or any(
        lowered_question.startswith(prefix)
        for prefix in [
            "what",
            "which",
            "why",
            "how",
            "can you",
            "could you",
            "please",
            "give me",
            "am i",
            "are we",
            "are you",
            "is ",
            "do i",
            "do we",
            "does ",
            "did ",
            "should ",
            "would ",
            "will ",
            "was ",
            "were ",
        ]
    )


def _detect_top_n(lowered_question: str, default: int) -> int:
    match = re.search(r"\btop\s+(\d{1,2})\b", lowered_question)
    if not match:
        return default
    return min(max(int(match.group(1)), 1), 30)


def _detect_invalid_period(lowered_question: str) -> int | None:
    for match in re.finditer(r"\b(?:period|p)\s*0?(\d{1,2})\b", lowered_question):
        period = int(match.group(1))
        if not 1 <= period <= 12:
            return period
    return None


def _detect_invalid_quarter(lowered_question: str) -> str | None:
    match = re.search(r"\bq([1-9])\b", lowered_question)
    if not match:
        return None
    quarter = int(match.group(1))
    return None if 1 <= quarter <= 4 else f"Q{quarter}"


def _detect_period(lowered_question: str) -> int | None:
    mentions = _detect_period_mentions(lowered_question)
    return mentions[0] if mentions else None


def _detect_period_pair(lowered_question: str) -> tuple[int, int] | None:
    mentions = _detect_period_mentions(lowered_question)
    if len(mentions) < 2:
        return None
    first, second = mentions[0], mentions[1]
    if first == second:
        return None
    return first, second


def _detect_period_mentions(lowered_question: str) -> list[int]:
    tokens = _normalise_lookup(lowered_question).split()
    periods: list[int] = []
    for index, token in enumerate(tokens):
        period: int | None = None
        if token in MONTH_TO_PERIOD:
            period = MONTH_TO_PERIOD[token]
        elif token in {"last", "previous", "prior"} and index + 1 < len(tokens) and tokens[index + 1] == "month":
            period = _previous_calendar_period()
        elif token in {"this", "current"} and index + 1 < len(tokens) and tokens[index + 1] == "month":
            period = _current_calendar_period()
        elif token == "period" and index + 1 < len(tokens) and tokens[index + 1].isdigit():
            period = int(tokens[index + 1])
        else:
            match = re.fullmatch(r"p0?(\d{1,2})", token)
            if match:
                period = int(match.group(1))

        if period is not None and 1 <= period <= 12 and (not periods or periods[-1] != period):
            periods.append(period)
    return periods


def _detect_quarter(lowered_question: str) -> Quarter | None:
    if "this quarter" in lowered_question or "current quarter" in lowered_question:
        return _current_calendar_quarter()
    if "last quarter" in lowered_question or "previous quarter" in lowered_question or "prior quarter" in lowered_question:
        return _previous_calendar_quarter()
    match = re.search(r"\bq([1-4])\b", lowered_question)
    if match:
        return f"Q{match.group(1)}"  # type: ignore[return-value]
    quarter_words = {
        "first quarter": "Q1",
        "second quarter": "Q2",
        "third quarter": "Q3",
        "fourth quarter": "Q4",
        "quarter 1": "Q1",
        "quarter 2": "Q2",
        "quarter 3": "Q3",
        "quarter 4": "Q4",
    }
    for phrase, quarter in quarter_words.items():
        if phrase in lowered_question:
            return quarter  # type: ignore[return-value]
    return None


def _current_calendar_quarter() -> Quarter:
    quarter = ((datetime.now().month - 1) // 3) + 1
    return f"Q{quarter}"  # type: ignore[return-value]


def _current_calendar_period() -> int:
    return datetime.now().month


def _previous_calendar_period() -> int:
    current = _current_calendar_period()
    return 12 if current == 1 else current - 1


def _previous_calendar_quarter() -> Quarter:
    current = int(_current_calendar_quarter()[1])
    previous = 4 if current == 1 else current - 1
    return f"Q{previous}"  # type: ignore[return-value]


def _detect_direction(lowered_question: str) -> VarianceDirection | None:
    if any(
        token in lowered_question
        for token in [
            "unfavorable",
            "unfavourable",
            "overspend",
            "overspends",
            "overspent",
            "overrun",
            "overruns",
            "above budget",
            "over budget",
            "budget miss",
            "cost pressure",
            "spend pressure",
            "higher than budget",
            "worse than budget",
        ]
    ):
        return "Unfavorable"
    if any(
        token in lowered_question
        for token in [
            "favorable",
            "favourable",
            "underspend",
            "underspends",
            "underspent",
            "below budget",
            "under budget",
            "save",
            "savings",
            "saving",
            "cost saving",
            "cost savings",
            "saved",
            "save money",
            "came in below",
            "lower than budget",
            "better than budget",
        ]
    ):
        return "Favorable"
    return None


def _filter_sentence(filters: dict[str, Any]) -> str:
    active = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, list | tuple):
            if not value:
                continue
            if key == "periods":
                active.append(f"periods = {', '.join(_period_name(int(item)) for item in value)}")
            else:
                active.append(f"{key.replace('_', ' ')} = {', '.join(map(str, value))}")
            continue
        active.append(f"{key.replace('_', ' ')} = {value}")
    return ", ".join(active) if active else "none"


def _scope_label(filters: dict[str, Any]) -> str:
    """Return a human-readable label for the selected finance scope."""

    if filters.get("period") is not None:
        return _period_name(int(filters["period"]))
    if filters.get("periods"):
        return ", ".join(_period_name(int(period)) for period in filters["periods"])
    if filters.get("quarter"):
        return str(filters["quarter"])
    return "the selected period"


def _variance_focus_phrase(filters: dict[str, Any]) -> str:
    direction = filters.get("direction")
    if direction == "Favorable":
        return "largest cost saving"
    if direction == "Unfavorable":
        return "largest overspend"
    return "biggest variance"


def _ranked_variance_title(filters: dict[str, Any]) -> str:
    direction = filters.get("direction")
    if direction == "Favorable":
        return "top cost savings"
    if direction == "Unfavorable":
        return "top overspends"
    return "top variance groups"


def _extract_fx_request(question: str) -> dict[str, Any] | None:
    lowered = _normalise_lookup(question)
    currencies: list[str] = []

    codes = re.findall(r"\b[A-Za-z]{3}\b", question)
    for code in codes:
        upper = code.upper()
        if upper in SUPPORTED_CURRENCIES and upper not in currencies:
            currencies.append(upper)

    for phrase, code in sorted(CURRENCY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if phrase in lowered and code not in currencies:
            currencies.append(code)

    slash_match = re.search(r"\b([A-Za-z]{3})\s*/\s*([A-Za-z]{3})\b", question)
    if slash_match:
        first, second = slash_match.group(1).upper(), slash_match.group(2).upper()
        if first in SUPPORTED_CURRENCIES and second in SUPPORTED_CURRENCIES:
            currencies = [first, second]

    if len(currencies) < 2:
        return None

    amount = Decimal("1")
    amount_match = re.search(r"\b(\d+(?:,\d{3})*(?:\.\d+)?)\b", question)
    if amount_match:
        try:
            amount = Decimal(amount_match.group(1).replace(",", ""))
        except InvalidOperation:
            amount = Decimal("1")
    if amount <= 0:
        amount = Decimal("1")

    return {
        "base_currency": currencies[0],
        "quote_currency": currencies[1],
        "amount": float(amount),
    }


def _fetch_exchange_rate(base_currency: str, quote_currency: str, amount: float = 1.0) -> dict[str, Any]:
    base = base_currency.upper().strip()
    quote = quote_currency.upper().strip()
    if not math.isfinite(amount) or amount <= 0:
        return {"error": "Conversion amount must be finite and positive."}
    if base not in SUPPORTED_CURRENCIES or quote not in SUPPORTED_CURRENCIES:
        return {"error": "Unsupported or unclear currency code."}
    if base == quote:
        return {
            "base_currency": base,
            "quote_currency": quote,
            "amount": amount,
            "rate": 1.0,
            "converted_amount": amount,
            "date": "same currency",
            "source": "local identity conversion",
        }

    url = f"https://api.frankfurter.dev/v2/rate/{base}/{quote}"
    request = Request(url, headers={"User-Agent": "AI-Finance-Agent/0.1"})
    try:
        with urlopen(request, timeout=10, context=_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return {"error": f"FX API returned HTTP {exc.code}."}
    except URLError as exc:
        return {"error": "Network error while contacting the FX API."}
    except TimeoutError:
        return {"error": "FX API request timed out."}
    except Exception as exc:
        return {"error": f"FX lookup failed ({type(exc).__name__})."}

    if not isinstance(payload, dict):
        return {"error": "FX API returned an invalid response."}
    if payload.get("base", base) != base or payload.get("quote", quote) != quote:
        return {"error": "FX API returned a different currency pair."}
    rate_value = payload.get("rate")
    if rate_value is None and isinstance(payload.get("rates"), dict):
        rate_value = payload.get("rates", {}).get(quote)
    if rate_value is None:
        return {"error": f"FX API response did not include {quote}."}

    try:
        rate = Decimal(str(rate_value))
        amount_decimal = Decimal(str(amount))
        if not rate.is_finite() or rate <= 0:
            raise ValueError("Invalid rate")
        converted = amount_decimal * rate
        if not math.isfinite(float(converted)):
            raise ValueError("Conversion exceeds numeric range")
    except (InvalidOperation, ValueError, OverflowError):
        return {"error": "FX API returned an invalid or out-of-range rate."}
    return {
        "base_currency": base,
        "quote_currency": quote,
        "amount": float(amount_decimal),
        "rate": float(rate),
        "converted_amount": float(converted),
        "date": payload.get("date", "unknown"),
        "source": "Frankfurter public exchange-rate API",
    }


def _upload_schema_dict() -> dict[str, Any]:
    return {
        "required_columns": {
            "fiscal_year": "Financial year, e.g. 2026.",
            "period": "Fiscal period number from 1 to 12. Month names are not required in the upload.",
            "cost_center": "Department or budget owner, e.g. Marketing, IT Ops, Finance.",
            "gl_account": "General ledger category, e.g. Payroll, Cloud Hosting, Travel.",
            "budget": "Budget amount for that period/cost center/G/L account.",
            "actual": "Actual posted amount for that period/cost center/G/L account.",
        },
        "optional_columns": {
            "period_label": "Readable fiscal period label, e.g. FY2026-P10.",
            "quarter": "Fiscal quarter, e.g. Q4. If absent, the app derives it from period.",
            "synthetic_driver": "Optional driver label such as price, volume, timing, or mix.",
            "driver_note": "Optional finance/business explanation for the variance.",
        },
        "supported_formats": [".csv", ".xlsx"],
        "grain": "One row per fiscal year, period, cost center, and G/L account.",
    }


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _normalise_lookup(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _contains_normalised_phrase(lookup: str, phrase: Any) -> bool:
    normalised_phrase = _normalise_lookup(phrase)
    if not normalised_phrase:
        return False
    return bool(re.search(rf"(?:^|\s){re.escape(normalised_phrase)}(?:\s|$)", lookup))


def _best_column_match(lookup: str, columns: list[str]) -> str | None:
    for column in sorted(columns, key=lambda item: len(_normalise_lookup(item)), reverse=True):
        normalised = _normalise_lookup(column)
        if _contains_normalised_phrase(lookup, normalised) or lookup == normalised:
            return column
    return None


def _dataframe_to_markdown(df: pd.DataFrame, columns: list[str]) -> str:
    available = [column for column in columns if column in df.columns]
    if df.empty or not available:
        return "_No matching rows found._"

    limited = df.loc[:, available].head(20)
    header = "| " + " | ".join(available) + " |"
    separator = "| " + " | ".join(["---"] * len(available)) + " |"
    rows = []
    for record in limited.to_dict("records"):
        cells = [_format_cell(column, record.get(column)) for column in available]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, separator, *rows])


def _format_cell(column: str, value: Any) -> str:
    if pd.isna(value):
        return "N/A" if "pct" in column else ""
    money_columns = {
        "budget",
        "actual",
        "variance",
        "abs_variance",
        "period_a_actual",
        "period_b_actual",
        "actual_difference",
        "period_a_budget",
        "period_b_budget",
        "budget_difference",
        "period_a_variance",
        "period_b_variance",
        "variance_difference",
    }
    if column in money_columns:
        return _money(float(value))
    if column in {"variance_pct", "abs_variance_pct", "actual_difference_pct"}:
        return _pct(float(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("|", "\\|")


def _serialise_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    clean = df.copy()
    clean = clean.replace({pd.NA: None})
    clean = clean.astype(object).where(pd.notnull(clean), None)
    records = clean.to_dict("records")
    for record in records:
        for key, value in list(record.items()):
            if hasattr(value, "item"):
                record[key] = value.item()
    return records


def _json_dump(value: Any) -> str:
    def safe(item):
        if isinstance(item, dict):
            return {key: safe(v) for key, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [safe(v) for v in item]
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item
    return json.dumps(safe(value), default=str, indent=2, allow_nan=False)


def _history_to_langchain_messages(chat_history: list[dict[str, Any]]) -> list[Any]:
    if HumanMessage is None or SystemMessage is None:
        return []

    messages: list[Any] = []
    for item in chat_history:
        role = str(item.get("role", "")).lower()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content)


def _signed_money(value: float) -> str:
    return ("+" if value > 0 else "") + _money(value)


if __name__ == "__main__":
    from data_engine import filter_significant_variances, generate_synthetic_budget_actuals

    raw = generate_synthetic_budget_actuals()
    flagged = filter_significant_variances(raw)
    bot = FinanceDataChatbot(raw, flagged, flagged)
    print(bot.answer("summarise top drivers by cost center").message)
