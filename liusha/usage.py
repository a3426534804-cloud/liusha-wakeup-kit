"""Telemetry only. This module never reads prompts or invokes a model."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math

SOURCES = ("chat", "bubble", "cc_wakeup", "cc_wakeup_setup", "cache_keepalive", "other")


def number(value):
    try:
        result = float(value)
        return max(0, result) if math.isfinite(result) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def normalize(row):
    read = int(number(row.get("cache_read_input_tokens")))
    created = int(number(row.get("cache_creation_input_tokens")))
    direct = int(number(row.get("input_tokens")))
    total = read + created + direct
    partial = bool(row.get("usage_partial"))
    cost = row.get("total_cost_usd")
    # Labels are descriptive heuristics, never proof of the cause of a miss.
    status = ("partial_hit" if read and created >= 10000 else "hit" if read
              else "new_context" if created else "uncached")
    source = row.get("source", "chat")
    return {
        "timestamp": row.get("timestamp"), "source": source if source in SOURCES else "other",
        "model": str(row.get("model_used") or row.get("model_requested") or "unknown"),
        "effort": str(row.get("effort") or ""), "input_tokens": direct,
        "cache_read_tokens": read, "cache_creation_tokens": created,
        "context_tokens": int(number(row.get("context_input_tokens"))) or total,
        "output_tokens": None if partial else int(number(row.get("output_tokens"))),
        "cache_read_ratio": round(read / total, 6) if total else 0,
        "cache_status": status, "cost_usd": None if cost is None else number(cost),
        "usage_partial": partial, "is_error": bool(row.get("is_error")),
        "duration_ms": int(number(row.get("duration_ms"))),
        "num_turns": int(number(row.get("num_turns"))),
    }


def totals(items):
    items = list(items)
    read = sum(x["cache_read_tokens"] for x in items)
    total = sum(x["input_tokens"] + x["cache_read_tokens"] + x["cache_creation_tokens"] for x in items)
    return {
        "calls": len(items), "errors": sum(x["is_error"] for x in items),
        "turns": sum(x["num_turns"] for x in items),
        **{key: sum(x[key] or 0 for x in items) for key in
           ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "duration_ms")},
        "cost_usd": round(sum(x["cost_usd"] or 0 for x in items), 6),
        "unknown_cost_calls": sum(x["cost_usd"] is None for x in items),
        "partial_calls": sum(x["usage_partial"] for x in items),
        "cache_read_ratio": read / total if total else 0,
    }


def ledger(path, *, period="24h", source="all", offset=0, page_size=15, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(hours=8)))
    cutoff = {"today": local.replace(hour=0, minute=0, second=0, microsecond=0),
              "24h": now - timedelta(hours=24), "7d": now - timedelta(days=7)}.get(period)
    rows, dropped = [], 0
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    raw = json.loads(line)
                    at = timestamp(raw.get("timestamp")) if isinstance(raw, dict) else None
                    if at is None:
                        dropped += 1
                        continue
                    if cutoff is None or at >= cutoff:
                        rows.append(normalize(raw))
                except (ValueError, TypeError, AttributeError):
                    dropped += 1
    except FileNotFoundError:
        pass
    rows.sort(key=lambda x: timestamp(x["timestamp"]), reverse=True)
    filtered = rows if source == "all" else [x for x in rows if x["source"] == source]
    offset = max(0, min(int(offset), 1000000))
    return {"ok": True, "read_only": True, "period": period, "source": source,
            "total": len(filtered), "offset": offset, "page_size": page_size,
            "items": filtered[offset:offset + page_size], "summary": totals(filtered),
            "period_summary": totals(rows), "dropped_rows": dropped,
            "by_source": {key: totals(x for x in rows if x["source"] == key) for key in SOURCES}}


class ResultUsage:
    """CLI result token fields are per turn; cost may be process-cumulative."""
    def __init__(self):
        self.cumulative_cost = 0.0

    def record(self, result, *, at, source="chat", model="unknown", effort="", last_assistant_usage=None):
        usage = result.get("usage") or {}
        reported = result.get("total_cost_usd")
        cost = None if reported is None else max(0, number(reported) - self.cumulative_cost)
        if reported is not None:
            self.cumulative_cost = number(reported)
        return {"timestamp": at, "source": source, "model_used": model, "effort": effort,
                "total_cost_usd": cost, "num_turns": result.get("num_turns", 0),
                "duration_ms": result.get("duration_ms", 0), "is_error": bool(result.get("is_error")),
                "context_input_tokens": sum(int(number((last_assistant_usage or {}).get(k))) for k in
                                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                **{k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}}
