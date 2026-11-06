"""Read-only ledger extracted from the production backend; no model calls.
See docs/integration.md for globals, auth, endpoint wiring, and scope semantics.
"""
import json, os, sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(os.environ.get('LIUSHA_RUNTIME_ROOT','./runtime')).resolve()
CLAUDE_RUNTIME_USAGE_LOG=ROOT/'logs/token_usage.jsonl'
CLAUDE_RUNTIME_SESSION_STATE=ROOT/'state/claude_session.json'
DB_PATH=Path(os.environ.get('LIUSHA_RELAY_DB',str(ROOT/'relay.db')))
TRANSCRIPT_DIR=Path(os.environ.get('LIUSHA_TRANSCRIPT_DIR',str(ROOT/'transcripts')))

def _usage_ledger(period: str = "24h", source: str = "all", offset: int = 0) -> dict:
    """Read the complete bridge ledger; never invoke a model or return chat text."""
    period = period if period in ("today", "24h", "7d", "all") else "24h"
    source = source if source in ("all", "chat", "bubble", "cc_wakeup", "cc_wakeup_setup", "cache_keepalive", "other") else "all"
    offset = max(0, min(int(offset), 1000000))
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    cutoff = {"today": local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc),
              "24h": now - timedelta(hours=24), "7d": now - timedelta(days=7)}.get(period)
    rows = []
    raw_rows = []
    try:
        with CLAUDE_RUNTIME_USAGE_LOG.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    ts = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
                    if cutoff and ts < cutoff:
                        continue
                    raw_rows.append(row)
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass

    # Only inspect the message IDs already present in usage telemetry. No text leaves the server.
    message_ids = [int(row["relay_message_id"]) for row in raw_rows
                   if str(row.get("relay_message_id") or "").isdigit()]
    bubble_ids = set()
    if message_ids:
        try:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                for start in range(0, len(message_ids), 500):
                    batch = message_ids[start:start + 500]
                    marks = ",".join("?" for _ in batch)
                    for msg_id, msg_text in conn.execute(
                            f"SELECT id, text FROM messages WHERE id IN ({marks}) AND direction='in'", batch):
                        if (msg_text or "").startswith(("（自动保活）", "（自动早安）", "（自动晚安）", "（自动消息）")):
                            bubble_ids.add(msg_id)
        except (OSError, sqlite3.Error):
            pass

    for row in raw_rows:
        read = int(row.get("cache_read_input_tokens") or 0)
        created = int(row.get("cache_creation_input_tokens") or 0)
        direct = int(row.get("input_tokens") or 0)
        total = read + created + direct
        kind = row.get("source") or "chat"
        msg_id = row.get("relay_message_id")
        if kind == "chat" and str(msg_id or "").isdigit() and int(msg_id) in bubble_ids:
            kind = "bubble"
        if kind not in ("chat", "bubble", "cc_wakeup", "cc_wakeup_setup", "cache_keepalive"):
            kind = "other"
        rows.append({
            "timestamp": row.get("timestamp"), "source": kind,
            "model": row.get("model_used") or row.get("model_requested") or "",
            "effort": row.get("effort") or "", "message_id": msg_id,
            "wake_id": row.get("wake_id"), "session_id": row.get("claude_session_id") or "",
            "num_turns": int(row.get("num_turns") or 0),
            "duration_ms": int(row.get("duration_ms") or 0),
            "input_tokens": direct, "output_tokens": int(row.get("output_tokens") or 0),
            "cache_read_tokens": read, "cache_creation_tokens": created,
            "context_tokens": int(row.get("context_input_tokens") or total),
            "cache_read_ratio": round(read / total, 4) if total else 0,
            "cost_usd": round(float(row.get("total_cost_usd") or 0), 6),
            "is_error": bool(row.get("is_error")),
            "usage_partial": bool(row.get("usage_partial")),
        })
    rows.reverse()

    def totals(items):
        result = {"calls": len(items), "errors": 0, "turns": 0, "input_tokens": 0,
                  "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                  "cost_usd": 0.0, "duration_ms": 0}
        for item in items:
            result["errors"] += int(item["is_error"])
            for key in ("turns", "input_tokens", "output_tokens", "cache_read_tokens",
                        "cache_creation_tokens", "duration_ms"):
                result[key] += item["num_turns"] if key == "turns" else item[key]
            result["cost_usd"] += item["cost_usd"]
        result["cost_usd"] = round(result["cost_usd"], 4)
        return result

    grouped = {name: totals([item for item in rows if item["source"] == name])
               for name in ("chat", "bubble", "cc_wakeup", "cc_wakeup_setup", "cache_keepalive", "other")}
    compactions = []
    try:
        state = json.loads(CLAUDE_RUNTIME_SESSION_STATE.read_text(encoding="utf-8"))
        session_id = str(state.get("session_id") or "")
        if session_id and all(ch.isalnum() or ch == "-" for ch in session_id):
            transcript = (TRANSCRIPT_DIR /
                          f"{session_id}.jsonl")
            with transcript.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if '"compact_boundary"' not in line:
                        continue
                    entry = json.loads(line)
                    if entry.get("type") != "system" or entry.get("subtype") != "compact_boundary":
                        continue
                    ts = entry.get("timestamp")
                    if cutoff and ts:
                        try:
                            if datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                                continue
                        except ValueError:
                            continue
                    meta = entry.get("compactMetadata") or {}
                    compactions.append({"timestamp": ts, "trigger": meta.get("trigger"),
                                        "before_tokens": meta.get("preTokens"),
                                        "after_tokens": meta.get("postTokens"),
                                        "duration_ms": meta.get("durationMs")})
    except (OSError, ValueError, TypeError):
        pass
    compactions.reverse()
    models = {}
    for item in rows:
        name = item["model"] or "unknown"
        if name not in models:
            models[name] = []
        models[name].append(item)
    filtered = rows if source == "all" else [item for item in rows if item["source"] == source]
    page = filtered[offset:offset + 15]
    # Claude transcript tool_use records have timestamps but no bridge-log ID.
    # Attribute only events inside this call's measured time interval and session.
    by_session = {}
    for item in page:
        item["tools"] = []
        item["tools_available"] = False
        sid = item["session_id"]
        if sid and all(ch.isalnum() or ch == "-" for ch in sid):
            by_session.setdefault(sid, []).append(item)
    for sid, calls in by_session.items():
        transcript = (TRANSCRIPT_DIR /
                      f"{sid}.jsonl")
        try:
            with transcript.open("r", encoding="utf-8") as handle:
                for item in calls:
                    item["tools_available"] = True
                intervals = []
                for item in calls:
                    try:
                        end = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
                        start = end - timedelta(milliseconds=item["duration_ms"]) - timedelta(seconds=2)
                        intervals.append((start, end + timedelta(seconds=1), item))
                    except (TypeError, ValueError):
                        pass
                for line in handle:
                    if '"tool_use"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") != "assistant":
                            continue
                        at = datetime.fromisoformat(str(event.get("timestamp") or "").replace("Z", "+00:00"))
                        target = next((item for start, end, item in intervals if start <= at <= end), None)
                        if target is None:
                            continue
                        content = (event.get("message") or {}).get("content") or []
                        if not isinstance(content, list):
                            continue
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "tool_use":
                                name = str(part.get("name") or "")[:120]
                                if name:
                                    target["tools"].append(name)
                    except (ValueError, TypeError):
                        continue
        except OSError:
            pass
    safe_page = [{key: value for key, value in item.items()
                  if key not in ("message_id", "wake_id", "session_id")}
                 for item in page]
    return {"ok": True, "period": period, "source": source, "total": len(filtered),
            "offset": offset, "page_size": 15, "items": safe_page,
            "summary": totals(rows), "by_source": grouped,
            "by_model": {name: totals(items) for name, items in models.items()},
            "compactions": compactions[:30], "compaction_count": len(compactions),
            "compaction_scope": "当前 Claude 会话",
            "oldest_timestamp": rows[-1]["timestamp"] if rows else None,
            "newest_timestamp": rows[0]["timestamp"] if rows else None,
            "read_only": True}
