"""Refresh the existing cache while the native chat runtime is stopped.

No relay messages, transcript writes, scheduled wakeups, or automatic cold starts.
The process is stopped at message_start, like the previously working refresher.
"""
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(os.environ.get('LIUSHA_RUNTIME_ROOT', './runtime')).resolve()
STATE = ROOT / 'state/silent-cache-keepalive.json'
USAGE = ROOT / 'logs/token_usage.jsonl'
SESSION = ROOT / 'state/claude_session.json'
# Refresh well before the one-hour cache boundary.  The previous 45-minute
# cadence left too little margin for timer drift and occasional delayed cache
# refreshes; one such run rebuilt the whole prefix.  A miss still disables the
# refresher immediately below, so it can never loop on an expensive rebuild.
DUE = 35 * 60
EXPIRED = 50 * 60


def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save(state):
    state['checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def protocol():
    return Path(os.environ['LIUSHA_PROTOCOL_FILE']).read_text(encoding='utf-8').strip()


def command(sid, model, effort, prompt, wake_enabled):
    allowed = os.environ.get('LIUSHA_ALLOWED_TOOLS', 'CronList,CronDelete')
    if wake_enabled:
        allowed = 'ScheduleWakeup,' + allowed
    cmd = [os.environ.get('LIUSHA_CLAUDE_BIN', 'claude'), '--resume', sid, '--model', model, '--effort', effort,
           '--append-system-prompt', prompt, '-p', '--input-format', 'stream-json',
           '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
           '--replay-user-messages', '--mcp-config', str(Path(os.environ.get('LIUSHA_MCP_CONFIG', str(ROOT / 'chat-mcp.json')))),
           '--strict-mcp-config', '--allowedTools', allowed,
           '--add-dir=' + os.environ.get('LIUSHA_UPLOAD_DIR', str(ROOT / 'uploads'))]
    if not wake_enabled:
        cmd.extend(['--disallowedTools', 'ScheduleWakeup'])
    return cmd + ['--no-session-persistence']


def latest_activity(sid):
    # Read only successful real/native calls. Our refreshes have a separate anchor.
    with USAGE.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 262144))
        lines = stream.read().splitlines()
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
            if row.get('source') == 'cache_keepalive' or row.get('is_error'):
                continue
            if row.get('claude_session_id') != sid:
                continue
            if not sum(int(row.get(k) or 0) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')):
                continue
            stamp = dt.datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
            return row, stamp
        except (ValueError, KeyError, TypeError):
            continue
    return None, 0


def latest_chat_settings(sid):
    """Return only settings explicitly carried by a real user chat turn."""
    with USAGE.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 262144))
        lines = stream.read().splitlines()
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
            if row.get('source') != 'chat' or row.get('is_error'):
                continue
            if row.get('claude_session_id') != sid:
                continue
            model = row.get('model_used') or row.get('model_requested') or ''
            effort = row.get('effort') or ''
            if model.startswith('claude-') and effort in ('low', 'medium', 'high', 'xhigh', 'max'):
                return model, effort
        except (ValueError, KeyError, TypeError):
            continue
    return '', ''


def decision(now, activity, state, key):
    if activity <= 0 or activity > now:
        return 'no_verified_activity'
    same = state.get('activity_key') == key
    if same and state.get('disabled'):
        return 'paused_after_failure'
    anchor = max(activity, float(state.get('last_refresh') or 0) if same else 0)
    if now - anchor >= EXPIRED:
        return 'expired_wait_for_chat'
    if now - anchor < DUE:
        return 'not_due'
    return 'ready'


def probe(cmd, env):
    proc = subprocess.Popen(cmd, cwd=os.environ.get('LIUSHA_PROJECT_DIR', str(ROOT / 'project')), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        message = {'type': 'user', 'uuid': str(uuid.uuid4()),
                   'message': {'role': 'user', 'content': 'Reply with exactly: ok'}}
        proc.stdin.write((json.dumps(message) + '\n').encode())
        proc.stdin.flush()
        deadline = time.monotonic() + 25
        buf = b''
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(timeout=min(1, max(0, deadline-time.monotonic()))):
                    if proc.poll() is not None:
                        break
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    event = obj.get('event', {}) if obj.get('type') == 'stream_event' else obj
                    if event.get('type') == 'message_start':
                        return (event.get('message') or {}).get('usage') or {}
        return None
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        proc.stdin.close()
        proc.stdout.close()


def main(dry_run=False):
    session = load(SESSION)
    sid = session.get('session_id')
    if session.get('status') != 'established' or not sid:
        print('invalid_session'); return
    row, activity = latest_activity(sid)
    if not row:
        print('no_verified_activity'); return
    model, effort = latest_chat_settings(sid)
    if not model or not model.startswith('claude-'):
        print('unknown_model'); return
    wake_enabled = load(ROOT / 'state/auto-wakeup-preference.json').get('enabled') is not False
    cmd = command(sid, model, effort, protocol(), wake_enabled)
    config_hash = hashlib.sha256((json.dumps(cmd) + Path(os.environ.get('LIUSHA_MCP_CONFIG', str(ROOT / 'chat-mcp.json'))).read_text()).encode()).hexdigest()
    key = f'{sid}:{activity}:{config_hash}'
    state = load(STATE)
    reason = decision(time.time(), activity, state, key)
    # A configuration edit can invalidate a warm prefix even before its TTL.
    # Let the next real chat establish the new prefix rather than probing it.
    # The exact runtime command, protocol prompt, and MCP contents are already
    # covered by config_hash.  Source-code mtimes alone do not alter the prompt
    # prefix and must not pause a still-warm cache after a logic-only deploy.
    configs = [Path(os.environ.get('LIUSHA_MCP_CONFIG', str(ROOT / 'chat-mcp.json'))), ROOT/'state/auto-wakeup-preference.json',
               Path(os.environ.get('LIUSHA_ENV_FILE', str(ROOT / '.env'))),
               Path(os.environ.get('LIUSHA_FLAGS_FILE', str(ROOT / 'flags.env')))]
    if any(path.exists() and path.stat().st_mtime > activity for path in configs):
        reason = 'configuration_changed_wait_for_chat'
    if dry_run:
        print(json.dumps({'decision': reason, 'dry_run': True, 'model': model,
                          'activity_age_seconds': int(time.time()-activity)})); return
    if state.get('activity_key') != key:
        state = {'activity_key': key, 'session_id': sid, 'disabled': False}
    state['status'] = reason
    if reason != 'ready':
        save(state); print(reason); return
    from liusha.locking import GlobalClaudeLock
    lock = GlobalClaudeLock(process='silent-cache-keepalive', operation='cache_refresh', session_id=sid)
    if not lock.acquire(blocking=False):
        state['status'] = 'native_chat_owns_session'; save(state); print(state['status']); return
    try:
        # Recheck after acquiring the same lock used by ordinary chat.
        new_row, new_activity = latest_activity(sid)
        if new_activity != activity or load(SESSION).get('session_id') != sid:
            state['status'] = 'activity_changed'; save(state); return
        started = time.monotonic()
        env = {**os.environ, 'ENABLE_CLAUDEAI_MCP_SERVERS': 'false', 'CLAUDE_CODE_ENABLE_TASKS': 'false'}
        try:
            usage = probe(cmd, env)
        except (OSError, ValueError, subprocess.SubprocessError):
            usage = None
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if usage is None:
            state.update(disabled=True, status='no_usage_wait_for_chat')
            record = {'timestamp': now, 'source': 'cache_keepalive', 'claude_session_id': sid,
                      'model_used': model, 'effort': effort, 'is_error': True,
                      'usage_partial': True, 'num_turns': 0, 'total_cost_usd': None}
        else:
            read = int(usage.get('cache_read_input_tokens') or 0)
            created = int(usage.get('cache_creation_input_tokens') or 0)
            direct = int(usage.get('input_tokens') or 0)
            creation = usage.get('cache_creation') or {}
            # This is a lower-bound API estimate: generation stops at message_start.
            estimate = None
            if model == 'claude-opus-4-6':
                one_h = int(creation.get('ephemeral_1h_input_tokens') or 0)
                five_m = int(creation.get('ephemeral_5m_input_tokens') or 0)
                estimate = (read * .5 + direct * 5 + one_h * 10 + five_m * 6.25) / 1_000_000
            record = {'timestamp': now, 'source': 'cache_keepalive', 'claude_session_id': sid,
                      'model_used': model, 'effort': effort, 'is_error': False,
                      'num_turns': 1, 'input_tokens': direct, 'output_tokens': None,
                      'cache_read_input_tokens': read, 'cache_creation_input_tokens': created,
                      'context_input_tokens': read+created+direct, 'cache_creation': creation,
                      'total_cost_usd': estimate, 'usage_partial': True}
            state.update(last_cache_read=read, last_cache_created=created, last_model=model)
            if read <= 0 or created >= 10000:
                state.update(disabled=True, status='unexpected_rebuild_wait_for_chat')
            else:
                state.update(last_refresh=time.time(), next_due=time.time()+DUE,
                             disabled=False, status='refreshed')
        record['duration_ms'] = round((time.monotonic()-started)*1000)
        with USAGE.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        save(state)
        print(json.dumps({'status': state['status'], 'read': state.get('last_cache_read'),
                          'created': state.get('last_cache_created')}))
    finally:
        lock.release()


if __name__ == '__main__':
    main('--check' in sys.argv)
