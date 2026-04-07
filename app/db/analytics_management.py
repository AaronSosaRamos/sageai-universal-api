"""
Repositorio para tablas de analítica (interaction_events, llm_invocation_metrics).
Los fallos al registrar nunca deben romper el flujo principal: usar try/except en el caller
o las funciones safe_* de este módulo.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client
from postgrest.exceptions import APIError

logger = logging.getLogger(__name__)


@dataclass
class InteractionEventInsert:
    event_category: str
    event_name: str
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    assistant_id: Optional[str] = None
    session_key: Optional[str] = None
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    http_method: Optional[str] = None
    http_path: Optional[str] = None
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None
    success: Optional[bool] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    client: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmInvocationInsert:
    model_name: str
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    assistant_id: Optional[str] = None
    interaction_event_id: Optional[str] = None
    provider: str = "google"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    time_to_first_token_ms: Optional[int] = None
    finish_reason: Optional[str] = None
    tool_calls_count: int = 0
    tools_used: Optional[List[str]] = None
    estimated_cost_usd: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)


class AnalyticsRepository:
    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_KEY")
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("SUPABASE_URL y/o SUPABASE_KEY no configuradas.")
        self.client: Client = create_client(self.supabase_url, self.supabase_key)

    def insert_interaction_event(self, data: InteractionEventInsert) -> Optional[str]:
        payload: Dict[str, Any] = {
            "event_category": data.event_category,
            "event_name": data.event_name,
            "metadata": data.metadata,
            "metrics": data.metrics,
            "client": data.client,
        }
        if data.user_id is not None:
            payload["user_id"] = data.user_id
        if data.thread_id is not None:
            payload["thread_id"] = data.thread_id
        if data.assistant_id is not None:
            payload["assistant_id"] = data.assistant_id
        if data.session_key is not None:
            payload["session_key"] = data.session_key
        if data.correlation_id is not None:
            payload["correlation_id"] = data.correlation_id
        if data.parent_event_id is not None:
            payload["parent_event_id"] = data.parent_event_id
        if data.http_method is not None:
            payload["http_method"] = data.http_method
        if data.http_path is not None:
            payload["http_path"] = data.http_path
        if data.status_code is not None:
            payload["status_code"] = data.status_code
        if data.duration_ms is not None:
            payload["duration_ms"] = data.duration_ms
        if data.success is not None:
            payload["success"] = data.success
        if data.error_type is not None:
            payload["error_type"] = data.error_type
        if data.error_message is not None:
            payload["error_message"] = data.error_message[:2000]

        try:
            res = self.client.table("interaction_events").insert(payload).execute()
            if res.data and len(res.data) > 0:
                return str(res.data[0].get("id", ""))
        except APIError as e:
            logger.warning("analytics insert_interaction_event: %s", e)
        except Exception as e:
            logger.warning("analytics insert_interaction_event: %s", e)
        return None

    def insert_llm_invocation(self, data: LlmInvocationInsert) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model_name": data.model_name,
            "provider": data.provider,
            "tool_calls_count": data.tool_calls_count,
            "metadata": data.metadata,
            "metrics": data.metrics,
        }
        if data.user_id is not None:
            payload["user_id"] = data.user_id
        if data.thread_id is not None:
            payload["thread_id"] = data.thread_id
        if data.assistant_id is not None:
            payload["assistant_id"] = data.assistant_id
        if data.interaction_event_id is not None:
            payload["interaction_event_id"] = data.interaction_event_id
        if data.input_tokens is not None:
            payload["input_tokens"] = data.input_tokens
        if data.output_tokens is not None:
            payload["output_tokens"] = data.output_tokens
        if data.total_tokens is not None:
            payload["total_tokens"] = data.total_tokens
        if data.latency_ms is not None:
            payload["latency_ms"] = data.latency_ms
        if data.time_to_first_token_ms is not None:
            payload["time_to_first_token_ms"] = data.time_to_first_token_ms
        if data.finish_reason is not None:
            payload["finish_reason"] = data.finish_reason
        if data.tools_used is not None:
            payload["tools_used"] = data.tools_used
        if data.estimated_cost_usd is not None:
            payload["estimated_cost_usd"] = data.estimated_cost_usd

        try:
            res = self.client.table("llm_invocation_metrics").insert(payload).execute()
            if res.data and len(res.data) > 0:
                return str(res.data[0].get("id", ""))
        except APIError as e:
            logger.warning("analytics insert_llm_invocation: %s", e)
        except Exception as e:
            logger.warning("analytics insert_llm_invocation: %s", e)
        return None

    # =========================================================================
    # HELPERS DE MUESTRA LEAN (una sola consulta → múltiples agregados en RAM)
    # =========================================================================

    def _fetch_lean_event_sample(self, since_iso: str, limit: int = 50000) -> List[Dict[str, Any]]:
        """Trae occurred_at, event_name, event_category, user_id para hacer
        todos los agregados de series temporales y distribuciones en Python."""
        try:
            r = (
                self.client.table("interaction_events")
                .select("occurred_at,event_name,event_category,user_id")
                .gte("occurred_at", since_iso)
                .order("occurred_at", desc=False)
                .limit(limit)
                .execute()
            )
            return r.data or []
        except APIError:
            return []

    @staticmethod
    def _date_from_ts(ts: Any) -> Optional[str]:
        try:
            raw = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                from datetime import timezone as tz
                dt = dt.replace(tzinfo=tz.utc)
            return dt.date().isoformat()
        except Exception:
            return None

    @staticmethod
    def _blank_day_row() -> Dict[str, int]:
        return {
            "supervisor": 0, "assistant": 0, "uploads": 0,
            "exports": 0, "sessions": 0, "threads": 0,
            "auth": 0, "storage": 0, "total": 0,
        }

    def _daily_series_from_sample(
        self, sample: List[Dict[str, Any]], days: int
    ) -> List[Dict[str, Any]]:
        utc = timezone.utc
        now = datetime.now(utc)
        day_counts: Dict[str, Dict[str, int]] = {}
        for i in range(days - 1, -1, -1):
            d = (now.date() - timedelta(days=i)).isoformat()
            day_counts[d] = self._blank_day_row()
        NAME_MAP = {
            "api.supervisor.invoke": "supervisor",
            "api.assistant_chat.invoke": "assistant",
            "file.uploaded": "uploads",
            "export.response": "exports",
            "session.started": "sessions",
            "api.threads.create": "threads",
        }
        CAT_MAP = {"auth": "auth", "storage": "storage"}
        for row in sample:
            d = self._date_from_ts(row.get("occurred_at"))
            if not d or d not in day_counts:
                continue
            day_counts[d]["total"] += 1
            name = row.get("event_name", "")
            cat = row.get("event_category", "")
            if name in NAME_MAP:
                day_counts[d][NAME_MAP[name]] += 1
            if cat in CAT_MAP:
                day_counts[d][CAT_MAP[cat]] += 1
        return [{"date": d, **v} for d, v in sorted(day_counts.items())]

    def _user_activity_histogram(
        self, sample: List[Dict[str, Any]], bins: int = 10
    ) -> List[Dict[str, Any]]:
        counts = Counter(
            str(r["user_id"]) for r in sample if r.get("user_id")
        ).values()
        vals = sorted(counts)
        if not vals:
            return []
        mn, mx = vals[0], vals[-1]
        if mn == mx:
            return [{"range": str(mn), "users": len(vals)}]
        step = max(1, (mx - mn + bins) // bins)
        result = []
        b = mn
        while b <= mx:
            hi = b + step - 1
            n = sum(1 for v in vals if b <= v <= hi)
            if n:
                result.append({"range": f"{b}–{hi}", "users": n})
            b = hi + 1
        return result

    def _funnel_from_sample(self, sample: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        steps = [
            ("session.started",          "Sesión iniciada"),
            ("api.threads.create",        "Hilo de conversación"),
            ("api.supervisor.invoke",     "Consulta IA (supervisor)"),
            ("api.assistant_chat.invoke", "Chat con asistente"),
            ("file.uploaded",             "Material subido"),
            ("export.response",           "Contenido exportado"),
        ]
        counts = Counter(r.get("event_name", "") for r in sample)
        return [{"step": label, "count": counts.get(name, 0)} for name, label in steps]

    def _personalization_from_sample(
        self, sample: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        cat_counts = Counter(r.get("event_category", "") for r in sample)
        name_counts = Counter(r.get("event_name", "") for r in sample)
        return {
            "custom_space_updates": cat_counts.get("custom_space", 0),
            "memory_events":        cat_counts.get("memory", 0),
            "assistant_events":     cat_counts.get("assistant", 0),
            "chat_events":          cat_counts.get("chat", 0),
            "storage_events":       cat_counts.get("storage", 0),
            "session_events":       name_counts.get("session.started", 0),
            "personalization_total": (
                cat_counts.get("custom_space", 0)
                + cat_counts.get("memory", 0)
                + cat_counts.get("assistant", 0)
            ),
        }

    def _tool_usage_from_llm(self, since_iso: str, limit: int = 12000) -> List[Dict[str, Any]]:
        try:
            r = (
                self.client.table("llm_invocation_metrics")
                .select("tools_used,tool_calls_count")
                .gte("occurred_at", since_iso)
                .limit(limit)
                .execute()
            )
            rows = r.data or []
        except APIError:
            rows = []
        tool_ctr: Counter = Counter()
        invocations_with_tools = 0
        for row in rows:
            tools = row.get("tools_used")
            if tools and isinstance(tools, list) and len(tools) > 0:
                invocations_with_tools += 1
                for t in tools:
                    if t:
                        tool_ctr[str(t)] += 1
        total = len(rows)
        return {
            "top_tools": [{"tool": t, "count": c} for t, c in tool_ctr.most_common(20)],
            "invocations_with_tool_calls": invocations_with_tools,
            "invocations_without_tools": total - invocations_with_tools,
            "tool_call_rate": round(invocations_with_tools / total, 4) if total else None,
        }

    def _daily_tokens(self, since_iso: str, days: int) -> List[Dict[str, Any]]:
        try:
            r = (
                self.client.table("llm_invocation_metrics")
                .select("occurred_at,total_tokens,input_tokens,output_tokens")
                .gte("occurred_at", since_iso)
                .limit(15000)
                .execute()
            )
            rows = r.data or []
        except APIError:
            rows = []
        utc = timezone.utc
        now = datetime.now(utc)
        day_tok: Dict[str, Dict[str, int]] = {}
        for i in range(days - 1, -1, -1):
            d = (now.date() - timedelta(days=i)).isoformat()
            day_tok[d] = {"total": 0, "input": 0, "output": 0}
        for row in rows:
            d = self._date_from_ts(row.get("occurred_at"))
            if not d or d not in day_tok:
                continue
            day_tok[d]["total"]  += int(row.get("total_tokens")  or 0)
            day_tok[d]["input"]  += int(row.get("input_tokens")   or 0)
            day_tok[d]["output"] += int(row.get("output_tokens")  or 0)
        return [{"date": d, **v} for d, v in sorted(day_tok.items())]

    def _weekly_heatmap_from_sample(
        self, sample: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """7×24 matrix (weekday × hour) of event counts."""
        matrix: List[List[int]] = [[0] * 24 for _ in range(7)]
        for row in sample:
            ts = row.get("occurred_at")
            if not ts:
                continue
            try:
                raw = str(ts).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
                matrix[dt.weekday()][dt.hour] += 1
            except Exception:
                continue
        wd_labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        return [
            {"weekday": i, "label": wd_labels[i], "hours": matrix[i]}
            for i in range(7)
        ]

    def _category_by_day(
        self, sample: List[Dict[str, Any]], days: int
    ) -> List[Dict[str, Any]]:
        """Para cada día: conteo por categorías clave (stacked chart)."""
        KEY_CATS = ["supervisor", "assistant", "chat", "storage", "export",
                    "auth", "thread", "custom_space", "memory", "other"]
        utc = timezone.utc
        now = datetime.now(utc)
        day_cat: Dict[str, Dict[str, int]] = {}
        for i in range(days - 1, -1, -1):
            d = (now.date() - timedelta(days=i)).isoformat()
            day_cat[d] = {k: 0 for k in KEY_CATS}
        CAT_NAME_MAP = {
            "api.supervisor.invoke": "supervisor",
            "api.assistant_chat.invoke": "assistant",
        }
        for row in sample:
            d = self._date_from_ts(row.get("occurred_at"))
            if not d or d not in day_cat:
                continue
            name = row.get("event_name", "")
            cat  = row.get("event_category", "")
            bucket = CAT_NAME_MAP.get(name) or (cat if cat in KEY_CATS else "other")
            day_cat[d][bucket] = day_cat[d].get(bucket, 0) + 1
        return [{"date": d, **v} for d, v in sorted(day_cat.items())]

    EVENT_CATEGORIES = (
        "auth",
        "chat",
        "assistant",
        "supervisor",
        "storage",
        "export",
        "memory",
        "custom_space",
        "thread",
        "user",
        "api",
        "system",
        "security",
        "other",
    )

    def _count_events_since(self, since_iso: str) -> int:
        try:
            r = (
                self.client.table("interaction_events")
                .select("id", count="exact")
                .gte("occurred_at", since_iso)
                .execute()
            )
            return r.count or 0
        except APIError:
            return 0

    def _count_events_category_since(self, category: str, since_iso: str) -> int:
        try:
            r = (
                self.client.table("interaction_events")
                .select("id", count="exact")
                .eq("event_category", category)
                .gte("occurred_at", since_iso)
                .execute()
            )
            return r.count or 0
        except APIError:
            return 0

    def _count_events_range(self, start_iso: str, end_iso: str) -> int:
        try:
            r = (
                self.client.table("interaction_events")
                .select("id", count="exact")
                .gte("occurred_at", start_iso)
                .lt("occurred_at", end_iso)
                .execute()
            )
            return r.count or 0
        except APIError:
            return 0

    def _count_named_event_since(self, name: str, since_iso: str) -> int:
        try:
            r = (
                self.client.table("interaction_events")
                .select("id", count="exact")
                .eq("event_name", name)
                .gte("occurred_at", since_iso)
                .execute()
            )
            return r.count or 0
        except APIError:
            return 0

    def _top_event_names(self, since_iso: str, limit: int = 15) -> List[Dict[str, Any]]:
        try:
            r = (
                self.client.table("interaction_events")
                .select("event_name")
                .gte("occurred_at", since_iso)
                .limit(8000)
                .execute()
            )
            names = [row["event_name"] for row in (r.data or []) if row.get("event_name")]
            ctr = Counter(names)
            return [{"event_name": n, "count": c} for n, c in ctr.most_common(limit)]
        except APIError:
            return []

    def _recent_events(self, limit: int) -> List[Dict[str, Any]]:
        try:
            r = (
                self.client.table("interaction_events")
                .select(
                    "id,occurred_at,event_category,event_name,user_id,http_path,status_code,duration_ms,success"
                )
                .order("occurred_at", desc=True)
                .limit(limit)
                .execute()
            )
            return list(r.data or [])
        except APIError:
            return []

    @staticmethod
    def _percentile_nearest(sorted_vals: List[float], p: float) -> Optional[float]:
        if not sorted_vals:
            return None
        n = len(sorted_vals)
        if n == 1:
            return sorted_vals[0]
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return sorted_vals[idx]

    def _llm_aggregate(self, since_iso: str) -> Dict[str, Any]:
        try:
            r = (
                self.client.table("llm_invocation_metrics")
                .select(
                    "model_name,input_tokens,output_tokens,total_tokens,latency_ms,estimated_cost_usd"
                )
                .gte("occurred_at", since_iso)
                .limit(15000)
                .execute()
            )
            rows = r.data or []
        except APIError:
            rows = []
        invocations = len(rows)
        total_in = sum(int(x.get("input_tokens") or 0) for x in rows)
        total_out = sum(int(x.get("output_tokens") or 0) for x in rows)
        total_tok = sum(int(x.get("total_tokens") or 0) for x in rows)
        if total_tok == 0:
            total_tok = total_in + total_out
        latencies = sorted(float(x["latency_ms"]) for x in rows if x.get("latency_ms") is not None)
        avg_lat = sum(latencies) / len(latencies) if latencies else None
        p50_lat = self._percentile_nearest(latencies, 50) if latencies else None
        p95_lat = self._percentile_nearest(latencies, 95) if latencies else None
        cost = sum(float(x.get("estimated_cost_usd") or 0) for x in rows)
        by_model = Counter((r.get("model_name") or "unknown") for r in rows)
        by_model_list = [{"model": m, "count": c} for m, c in by_model.most_common(16)]
        total_m = sum(by_model.values()) or 1
        herfindahl = round(sum((c / total_m) ** 2 for c in by_model.values()), 4)
        t_per_inv = (total_tok / invocations) if invocations else None
        in_share = (total_in / total_tok) if total_tok else None
        cost_per_1k = (cost / (total_tok / 1000.0)) if total_tok else None
        return {
            "invocations": invocations,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_tok,
            "avg_latency_ms": round(avg_lat, 1) if avg_lat is not None else None,
            "latency_p50_ms": round(p50_lat, 1) if p50_lat is not None else None,
            "latency_p95_ms": round(p95_lat, 1) if p95_lat is not None else None,
            "estimated_cost_usd": round(cost, 6),
            "by_model": by_model_list,
            "tokens_per_invocation_mean": round(t_per_inv, 2) if t_per_inv is not None else None,
            "input_token_share_of_total": round(in_share, 4) if in_share is not None else None,
            "estimated_cost_usd_per_1000_tokens": round(cost_per_1k, 8) if cost_per_1k is not None else None,
            "model_concentration_herfindahl": herfindahl,
            "sample_note": "Hasta 15000 invocaciones LLM en el periodo (muestra para estudios de coste).",
        }

    def _sample_user_ids_since(self, since_iso: str, limit: int = 25000) -> List[str]:
        try:
            r = (
                self.client.table("interaction_events")
                .select("user_id")
                .gte("occurred_at", since_iso)
                .not_.is_("user_id", "null")
                .limit(limit)
                .execute()
            )
            return [str(row["user_id"]) for row in (r.data or []) if row.get("user_id")]
        except APIError:
            return []

    def _success_field_sample(self, since_iso: str, limit: int = 20000) -> Dict[str, Any]:
        try:
            r = (
                self.client.table("interaction_events")
                .select("success")
                .gte("occurred_at", since_iso)
                .limit(limit)
                .execute()
            )
            rows = r.data or []
        except APIError:
            rows = []
        ok = sum(1 for row in rows if row.get("success") is True)
        fail = sum(1 for row in rows if row.get("success") is False)
        denom = ok + fail
        rate = round(ok / denom, 4) if denom else None
        return {
            "success_true": ok,
            "success_false": fail,
            "sample_size_with_boolean": denom,
            "observed_success_rate": rate,
        }

    def _temporal_distribution_since(self, since_iso: str, limit: int = 12000) -> Dict[str, Any]:
        try:
            r = (
                self.client.table("interaction_events")
                .select("occurred_at")
                .gte("occurred_at", since_iso)
                .order("occurred_at", desc=True)
                .limit(limit)
                .execute()
            )
            rows = r.data or []
        except APIError:
            rows = []
        hours = [0] * 24
        weekdays = [0] * 7
        for row in rows:
            ts = row.get("occurred_at")
            if not ts:
                continue
            try:
                raw = str(ts).replace("Z", "+00:00")
                if "+" not in raw and raw.count("-") >= 3:
                    raw = raw + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
                hours[dt.hour] += 1
                weekdays[dt.weekday()] += 1
            except Exception:
                continue
        wd_labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        return {
            "hourly_utc": [{"hour": h, "count": hours[h]} for h in range(24)],
            "weekday_utc": [{"weekday": i, "label": wd_labels[i], "count": weekdays[i]} for i in range(7)],
            "sample_size": len(rows),
        }

    def _duration_sample_named_events(
        self, since_iso: str, event_names: List[str], limit: int = 8000
    ) -> Dict[str, Any]:
        """Latencias observadas en subconjuntos de eventos (calidad percibida)."""
        out: Dict[str, Any] = {}
        for name in event_names:
            try:
                r = (
                    self.client.table("interaction_events")
                    .select("duration_ms")
                    .eq("event_name", name)
                    .gte("occurred_at", since_iso)
                    .not_.is_("duration_ms", "null")
                    .limit(limit)
                    .execute()
                )
                vals = sorted(
                    float(row["duration_ms"])
                    for row in (r.data or [])
                    if row.get("duration_ms") is not None
                )
                out[name] = {
                    "n": len(vals),
                    "p50_ms": round(self._percentile_nearest(vals, 50), 1) if vals else None,
                    "p95_ms": round(self._percentile_nearest(vals, 95), 1) if vals else None,
                    "mean_ms": round(sum(vals) / len(vals), 1) if vals else None,
                }
            except APIError:
                out[name] = {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
        return out

    def get_admin_dashboard(self, days: int = 30) -> Dict[str, Any]:
        days = max(1, min(int(days), 365))
        utc = timezone.utc
        now = datetime.now(utc)
        since = now - timedelta(days=days)
        since_iso = since.isoformat()

        total_events = self._count_events_since(since_iso)
        by_cat = {cat: self._count_events_category_since(cat, since_iso) for cat in self.EVENT_CATEGORIES}

        daily: List[Dict[str, Any]] = []
        for i in range(days - 1, -1, -1):
            d = now.date() - timedelta(days=i)
            start = datetime(d.year, d.month, d.day, tzinfo=utc)
            end = start + timedelta(days=1)
            c = self._count_events_range(start.isoformat(), end.isoformat())
            daily.append({"date": d.isoformat(), "events": c})

        login_ok = self._count_named_event_since("auth.login.success", since_iso)
        login_fail = self._count_named_event_since("auth.login.failure", since_iso)
        top_names = self._top_event_names(since_iso, limit=28)
        recent = self._recent_events(60)
        llm = self._llm_aggregate(since_iso)

        # Muestra lean para todos los agregados en Python (una sola query)
        lean_sample = self._fetch_lean_event_sample(since_iso, limit=50000)
        daily_series = self._daily_series_from_sample(lean_sample, days)
        user_histogram = self._user_activity_histogram(lean_sample)
        funnel = self._funnel_from_sample(lean_sample)
        personalization = self._personalization_from_sample(lean_sample)
        heatmap = self._weekly_heatmap_from_sample(lean_sample)
        category_by_day = self._category_by_day(lean_sample, days)
        tools_data = self._tool_usage_from_llm(since_iso)
        daily_tok = self._daily_tokens(since_iso, days)

        supervisor_n = self._count_named_event_since("api.supervisor.invoke", since_iso)
        assistant_n = self._count_named_event_since("api.assistant_chat.invoke", since_iso)
        uploads = self._count_named_event_since("file.uploaded", since_iso)
        downloads = self._count_named_event_since("file.downloaded", since_iso)
        exports = self._count_named_event_since("export.response", since_iso)
        session_started = self._count_named_event_since("session.started", since_iso)
        threads_create = self._count_named_event_since("api.threads.create", since_iso)
        threads_list = self._count_named_event_since("api.threads.list", since_iso)
        messages_read = self._count_named_event_since("api.threads.messages.read", since_iso)
        admin_template_dl = self._count_named_event_since("admin.users.template_downloaded", since_iso)
        bulk_import = self._count_named_event_since("admin.users.bulk_import.completed", since_iso)

        uid_sample = self._sample_user_ids_since(since_iso)
        unique_users = len(set(uid_sample))
        mean_ev_per_active = round(total_events / unique_users, 2) if unique_users else None

        login_denom = login_ok + login_fail
        login_rate = round(login_ok / login_denom, 4) if login_denom else None

        success_s = self._success_field_sample(since_iso)
        temporal = self._temporal_distribution_since(since_iso)

        ped_total = supervisor_n + assistant_n
        sup_share = round(supervisor_n / ped_total, 4) if ped_total else None
        asst_share = round(assistant_n / ped_total, 4) if ped_total else None

        learning_artefacts = uploads + downloads + exports
        artefact_intensity = round(learning_artefacts / total_events, 6) if total_events else None

        chat_cat = by_cat.get("chat", 0) + by_cat.get("supervisor", 0) + by_cat.get("assistant", 0)
        chat_share = round(chat_cat / total_events, 4) if total_events else None

        duration_key_events = self._duration_sample_named_events(
            since_iso,
            ["api.supervisor.invoke", "api.assistant_chat.invoke", "auth.login.success"],
        )

        academic = {
            "document_title_suggestion": "Indicadores descriptivos de uso de IA generativa en entorno educativo",
            "measurement_timezone": "UTC",
            "definitions": {
                "active_user_proxy": "Usuarios distintos con al menos un evento con user_id en la muestra (hasta 25000 filas).",
                "pedagogical_dialogue_proxy": "Eventos api.supervisor.invoke + api.assistant_chat.invoke (interacción tutorial / asistente).",
                "learning_artefact_proxy": "Subidas + descargas + exportaciones (materiales y producción escrita).",
                "reliability_proxy": "Proporción success=true entre eventos con campo success no nulo (muestra).",
            },
        }

        adoption_reach = {
            "unique_active_users_in_sample": unique_users,
            "user_id_sample_rows": len(uid_sample),
            "mean_events_per_distinct_user_in_sample": mean_ev_per_active,
            "login_success_rate": login_rate,
            "login_attempts_observed": login_denom,
        }

        pedagogical_mediation = {
            "supervisor_invocations": supervisor_n,
            "assistant_chat_invocations": assistant_n,
            "pedagogical_dialogue_events_total": ped_total,
            "supervisor_share_of_dialogue": sup_share,
            "assistant_share_of_dialogue": asst_share,
            "session_started_events": session_started,
            "threads_created": threads_create,
            "threads_listed": threads_list,
            "thread_messages_read": messages_read,
            "learning_resources_uploaded": uploads,
            "learning_resources_downloaded": downloads,
            "written_outputs_exported": exports,
            "learning_artefact_events_total": learning_artefacts,
            "artefact_events_share_of_all": artefact_intensity,
            "chat_related_category_weight": chat_share,
        }

        administration = {
            "admin_import_template_downloads": admin_template_dl,
            "bulk_import_runs_completed": bulk_import,
        }

        reliability = {
            "login_success": login_ok,
            "login_failure": login_fail,
            "login_success_rate": login_rate,
            **success_s,
        }

        return {
            "period_days": days,
            "period_start": since_iso,
            "period_end": now.isoformat(),
            "academic": academic,
            "totals": {
                "interaction_events": total_events,
            },
            "adoption_reach": adoption_reach,
            "pedagogical_mediation": pedagogical_mediation,
            "administration_metrics": administration,
            "events_by_category": by_cat,
            "daily_activity": daily,
            "auth": {"login_success": login_ok, "login_failure": login_fail},
            "product_highlights": {
                "supervisor_invocations": supervisor_n,
                "assistant_chat_invocations": assistant_n,
                "file_uploads": uploads,
                "file_downloads": downloads,
                "exports": exports,
            },
            "temporal_patterns": temporal,
            "latency_by_key_event": duration_key_events,
            "top_event_names": top_names,
            "recent_events": recent,
            "llm": llm,
            "reliability": reliability,
            # Nuevas series y distribuciones para charts
            "daily_series": daily_series,
            "user_histogram": user_histogram,
            "funnel": funnel,
            "personalization": personalization,
            "heatmap": heatmap,
            "category_by_day": category_by_day,
            "tools": tools_data,
            "daily_tokens": daily_tok,
            "lean_sample_rows": len(lean_sample),
        }


def safe_record_interaction_event(data: InteractionEventInsert) -> None:
    """Registra un evento sin propagar errores (telemetría best-effort)."""
    try:
        AnalyticsRepository().insert_interaction_event(data)
    except Exception as e:
        logger.debug("safe_record_interaction_event skipped: %s", e)


def safe_record_llm_invocation(data: LlmInvocationInsert) -> None:
    try:
        AnalyticsRepository().insert_llm_invocation(data)
    except Exception as e:
        logger.debug("safe_record_llm_invocation skipped: %s", e)


def new_correlation_id() -> str:
    return str(uuid.uuid4())
