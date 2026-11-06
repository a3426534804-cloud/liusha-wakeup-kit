"""Production day-first wake history reader. Private endpoint; never expose publicly.
The caller supplies _wakeup_preference and _read_idle_script_status from its app.
"""
import json, os
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
ROOT=Path(os.environ.get('LIUSHA_RUNTIME_ROOT','./runtime')).resolve()
def _wakeup_preference():
    try:return json.loads((ROOT/'state/auto-wakeup-preference.json').read_text()).get('enabled') is not False
    except (OSError, ValueError, AttributeError):return False
def _read_idle_script_status():
    return {'timer_active':None,'last_decision':'adapter_required'}
def _read_awakening_records(limit: int = 60, day: str = "", month: str = "") -> dict:
    root = ROOT
    state = {}
    try:
        state = json.loads((root / "state/awakening.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    items, dates = [], {}
    tz = ZoneInfo("Asia/Shanghai")
    try:
        # Filter the complete log before applying any legacy caller's limit.
        # The date browser must not be limited to the newest 60 records.
        with (root / "logs/awakening.jsonl").open("rb") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                    if not isinstance(row, dict) or not row.get("id"):
                        continue
                    stamp = row.get("timestamp")
                    parsed = datetime.fromtimestamp(stamp, timezone.utc) if isinstance(stamp, (int, float)) else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=tz)
                    key = parsed.astimezone(tz).strftime("%Y-%m-%d")
                except (ValueError, TypeError, UnicodeError, OverflowError, OSError):
                    continue
                if month and key.startswith(month + "-"):
                    dates[key] = dates.get(key, 0) + 1
                if day and key != day:
                    continue
                item = {key: row.get(key) for key in
                        ("id", "timestamp", "summary", "thinking", "answer", "outcome", "next_wakeup", "source")}
                items.append((parsed.timestamp(), item))
        items.sort(key=lambda pair: pair[0], reverse=True)
        items = [item for _, item in (items if day else items[:limit])]
    except OSError:
        items = []
    return {"items": items, "day": day, "dates": dates,
            "enabled": bool(state.get("enabled")),
            "desired_enabled": _wakeup_preference(), "script": _read_idle_script_status(),
            "next_wakeup": state.get("next_wakeup"), "status": state.get("status", "not_started")}
