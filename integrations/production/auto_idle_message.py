"""Keep a user-started CC chat warm by day and stop its wakeups at night."""
import ast
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path(os.environ.get('LIUSHA_RUNTIME_ROOT', './runtime')).resolve()
DB=Path(os.environ.get('LIUSHA_RELAY_DB', str(ROOT / 'relay.db')))
LOG=ROOT/'logs/token_usage.jsonl'
LOCK=ROOT/'state/auto-idle-message.lock'
STATE=ROOT/'state/auto-idle-message.json'
MESSAGE='（自动保活）冒泡。请只简短回复“在”。'
PREVIOUS_MESSAGE='（自动消息）你有什么想跟我说的吗？'
MORNING_MESSAGE='（自动早安）早上好，冒泡一下。请简短回复，并恢复白天的 ScheduleWakeup。'
NIGHT_MESSAGE='（自动晚安）现在是夜里，请取消已设的 ScheduleWakeup，今晚不要再定新的闹钟。简短回复“晚安”。'
AUTO_MESSAGES=(MESSAGE,PREVIOUS_MESSAGE,MORNING_MESSAGE,NIGHT_MESSAGE)
ZONE=ZoneInfo('Asia/Shanghai')

def info(reason, **kw):
    print(json.dumps({'decision':reason,**kw},ensure_ascii=False),flush=True)

def latest_usage():
    with LOG.open('rb') as f:
        f.seek(0,os.SEEK_END)
        size=f.tell();f.seek(max(0,size-8192))
        lines=f.read().splitlines()
    for line in reversed(lines):
        try: row=json.loads(line)
        except ValueError: continue
        if row.get('is_error'):continue
        return row
    return None

def load_state():
    try:return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def save_state(state):
    tmp=STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    os.chmod(tmp,0o600)
    os.replace(tmp,STATE)

def same_session(usage):
    try:
        sid=json.loads((ROOT/'state/claude_session.json').read_text())['session_id']
        return bool(sid) and (usage.get('claude_session_id') or '')==sid
    except (OSError,KeyError,ValueError):return False

def usage_age(now,usage):
    used_at=dt.datetime.fromisoformat(usage['timestamp'].replace('Z','+00:00')).astimezone(ZONE)
    return (now-used_at).total_seconds()

def payload(text, usage=None):
    usage=usage or {}
    model=usage.get('model_used') or 'claude-opus-4-6'
    effort=usage.get('effort') or 'low'
    if not isinstance(model,str) or not model.startswith('claude-'):
        model='claude-opus-4-6'
    if effort not in ('low','medium','high','xhigh','max'):
        effort='low'
    return {'model':model,'effort':effort,'text':text}

def relay_token():
    token=os.environ.get('LIUSHA_RELAY_TOKEN', '')
    if not token: raise RuntimeError('LIUSHA_RELAY_TOKEN is required')
    return token

def check(now):
    active=Path(os.environ.get('LIUSHA_ACTIVE_PID_FILE', str(ROOT / 'state/active.pid')))
    if active.exists():
        try:
            pid=int(active.read_text().strip())
            cmdline=(Path('/proc')/str(pid)/'cmdline').read_bytes()
            if b'claude' in cmdline:return 'chat_active',None
        except (OSError,ValueError):pass
        # A killed bridge may leave this marker behind. It is not a running call.
        active.unlink(missing_ok=True)
    state=load_state()
    if 1<=now.hour<9:
        if now.hour==1 and now.minute<4 and state.get('night_day')!=(now-dt.timedelta(days=1)).date().isoformat():
            usage=latest_usage()
            if not usage or not same_session(usage) or usage_age(now,usage)>=55*60:
                return 'night_cache_too_old',None
            return 'night_ready',payload(NIGHT_MESSAGE,usage)
        return 'outside_window',None
    with sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True) as con:
        last=con.execute("SELECT id,ts,text,meta FROM messages WHERE direction='in' ORDER BY id DESC LIMIT 1").fetchone()
        real=con.execute("SELECT id,ts,text,meta FROM messages WHERE direction='in' AND text NOT IN (?,?,?,?) ORDER BY id DESC LIMIT 1",AUTO_MESSAGES).fetchone()
    if not last:return 'no_user_activity',None
    real_at=dt.datetime.fromisoformat(real[1].replace('Z','+00:00')).astimezone(ZONE) if real else None
    active_day=now.date() if now.hour>=9 else (now-dt.timedelta(days=1)).date()
    active_user=bool(real_at) and ((real_at.date()==active_day and real_at.hour>=9) or (now.hour<1 and real_at.date()==now.date() and real_at.hour<1))
    if not active_user:return 'wait_for_user_today',None
    latest_at=dt.datetime.fromisoformat(last[1].replace('Z','+00:00')).astimezone(ZONE)
    if (now-latest_at).total_seconds()<50*60:return 'recent_message',None
    usage=latest_usage()
    if not usage:return 'no_usage',None
    age=usage_age(now,usage)
    if age<50*60:return 'recent_model_call',None
    if age>=55*60:return 'cache_too_old',None
    if not same_session(usage):return 'session_changed',None
    return 'ready',payload(MESSAGE,usage)

def main():
    LOCK.parent.mkdir(parents=True,exist_ok=True)
    with LOCK.open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return info('already_running')
        now=dt.datetime.now(ZONE)
        reason,payload=check(now)
        if payload is None:return info(reason)
        if '--check' in sys.argv:return info(reason+'_dry_run')
        req=urllib.request.Request(os.environ.get('LIUSHA_RELAY_URL', 'http://127.0.0.1:3011') + '/app/send',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+relay_token(),'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=15) as response:
            result=json.load(response)
            if response.status!=200:raise RuntimeError('send failed')
        if reason=='night_ready':
            state=load_state();state['night_day']=(now-dt.timedelta(days=1)).date().isoformat();save_state(state)
        info('sent',kind=reason,message_id=result.get('id'))

if __name__=='__main__':main()
