"""Generate synthetic examples. No production data, credentials, or model calls."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def create(root):
    root = Path(root);root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    sources = ("chat", "cc_wakeup", "chat", "cache_keepalive", "bubble", "cc_wakeup_setup")
    rows = []
    for i in range(42):
        source = sources[i % len(sources)]
        read = 0 if i in (6, 30) else 64000 + (i % 9) * 830
        created = 62000 if not read else 400 + (i % 7) * 170
        rows.append({"timestamp": (now-timedelta(minutes=i*18)).isoformat(), "source": source,
                     "model_used": "demo-model", "effort": "high", "num_turns": 1,
                     "duration_ms": 11000+i*120, "cache_read_input_tokens": read,
                     "cache_creation_input_tokens": created, "input_tokens": 24,
                     "output_tokens": None if source == "cache_keepalive" else 180+i,
                     "total_cost_usd": None if i==12 else round((read*.5+created*6.25+24*5)/1000000,6),
                     "usage_partial": source=="cache_keepalive", "is_error": False,
                     "context_input_tokens": read+created+24})
    (root/'usage.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
    state = {"demo": True,"status":"refreshed","checked_at":now.isoformat(),
             "last_refresh":now.timestamp()-600,"next_due":now.timestamp()+1500,
             "last_cache_read":68420,"last_cache_created":320,"disabled":False}
    (root/'runtime.json').write_text(json.dumps(state),encoding='utf-8')
    return root/'usage.jsonl'
