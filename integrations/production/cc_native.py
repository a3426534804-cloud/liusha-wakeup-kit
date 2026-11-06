"""One persistent Claude Code stream, including its own ScheduleWakeup turns.

No Python timer submits model work. Only stdin user messages and CC's native
scheduler start turns. The bridge forwards visible protocol output as before.
"""
import datetime as dt
import json
import os
import re
import signal
import threading
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('LIUSHA_RUNTIME_ROOT', './runtime')).resolve()
STATE = ROOT / 'state/awakening.json'
WAKE_PREFERENCE = ROOT / 'state/auto-wakeup-preference.json'
RECORDS = ROOT / 'logs/awakening.jsonl'
USAGE = ROOT / 'logs/token_usage.jsonl'
MCP_CONFIG = Path(os.environ.get('LIUSHA_MCP_CONFIG', str(ROOT / 'chat-mcp.json')))
ZONE = ZoneInfo('Asia/Shanghai')
WAKE_PROMPT = ('你醒了，接着当前聊天，尽量主动给对方发消息，自然就好。'
               '只用一次 ScheduleWakeup，自选下次时间；北京时间01:00至09:00不要安排唤醒，夜间等用户或早安消息。reason 简短，prompt 原样用本段，不加 /loop。'
               '正文沿用聊天格式，只发一次。')
ARM_PROMPT = ('更新自主醒来：取消之前保持安静、不打扰和固定间隔的要求。'
              '本次只用一次 ScheduleWakeup，自选下次时间，reason 简短，prompt 用下面这段，不加 /loop。'
              '设置完成只输出 [[SILENT]]。\n'+WAKE_PROMPT)

def iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def quiet_hours():
    return 1 <= dt.datetime.now(ZONE).hour < 9

def wake_preference_enabled():
    try:
        return json.loads(WAKE_PREFERENCE.read_text(encoding='utf-8')).get('enabled') is not False
    except (OSError, ValueError, AttributeError):
        return True

def append(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        os.chmod(path,0o600)
        f.write(json.dumps(obj, ensure_ascii=False)+'\n')
        f.flush()

def recent_cache_activity(max_age_seconds=50*60):
    """Avoid a full history rebuild when arming after an idle restart."""
    try:
        with USAGE.open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell()-65536))
            for raw in reversed(f.read().splitlines()):
                try: row=json.loads(raw)
                except ValueError: continue
                if row.get('is_error'): continue
                stamp=dt.datetime.fromisoformat(row['timestamp'].replace('Z','+00:00')).timestamp()
                return 0 <= time.time()-stamp < max_age_seconds
    except (OSError,KeyError,TypeError,ValueError):
        pass
    return False


def recent_chat_settings(session_id):
    """Use the latest user chat as the only authority for model and effort.

    Autonomous wake/setup rows merely inherit these settings.  Treating them as
    preferences can make a stale background turn flip the next process back to
    low and force another full cache rebuild when the user sends at high.
    """
    try:
        with USAGE.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell()-65536))
            for raw in reversed(stream.read().splitlines()):
                try: row=json.loads(raw)
                except ValueError: continue
                if row.get('claude_session_id') != session_id or row.get('is_error'):
                    continue
                if row.get('source') != 'chat':
                    continue
                model=row.get('model_used') or ''
                effort=row.get('effort') or ''
                if model.startswith('claude-') and effort in ('low','medium','high','xhigh','max'):
                    return model,effort
    except OSError:
        pass
    return 'claude-opus-4-6','low'

def _dedup_text(text):
    """If CC's 'no visible output' retry doubled the answer, return one copy."""
    t = text.strip()
    if not t:
        return t
    n = len(t)
    for offset in range(n // 2 - 3, n // 2 + 4):
        if 0 < offset < n:
            a, b = t[:offset].strip(), t[offset:].strip()
            if a and a == b:
                return a
    return t

def wake_visible_output(raw, fallback_summary='', fallback_answer=''):
    """Use the first complete protocol answer, ignoring commentary/replays."""
    match = re.search(
        r'<<<THINKING_SUMMARY>>>(.*?)<<<END_THINKING_SUMMARY>>>\s*'
        r'<<<ANSWER>>>(.*?)<<<END_ANSWER>>>', raw, re.S)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return fallback_summary, _dedup_text(fallback_answer)

class Turn:
    def __init__(self, bridge, kind='wake', api_session='', relay_id='', model='', effort='low'):
        self.id = str(uuid.uuid4())
        self.kind, self.api_session, self.relay_id = kind, api_session, relay_id
        self.model, self.effort = model, effort
        self.started, self.clock = iso(), time.monotonic()
        self.parser = bridge.VisibleOutputParser()
        self.summary, self.answer, self.thinking = '', '', ''
        self.summary_closed = False
        self.done = threading.Event()
        self.ok = False
        self.tool_calls = {}
        self.last_usage = {}
        self.last_model = model
        self.watchdog = None

class Runtime:
    def __init__(self, bridge):
        self.b = bridge
        self.process = None
        self.active = None
        self.pending = {}
        self.mutex = threading.RLock()
        self.submit_lock = threading.Lock()
        self.model, self.effort = '', 'low'
        self.api_session = ''
        self.state = {'enabled': False, 'status': 'starting', 'next_wakeup': None}
        self.cumulative = {}
        self.cumulative_cost = 0
        self.controls = {}
        self.reader_thread = None
        self.waiting_users = 0
        self.arm_retry_attempts = 0
        self.write_state()
        threading.Thread(target=self.night_watch, daemon=True).start()

    def night_watch(self):
        """Hard stop scheduled wakeups after the night message, even if CC forgot."""
        while True:
            now=dt.datetime.now(ZONE)
            if not wake_preference_enabled():
                if self.submit_lock.acquire(blocking=False):
                    try:
                        if self.process and self.process.poll() is None:
                            if self.active is None and not self.pending:self.stop('user_disabled')
                        elif self.state.get('status') != 'user_disabled':
                            self.state.update(enabled=False,status='user_disabled',next_wakeup=None)
                            self.write_state()
                    finally:self.submit_lock.release()
                time.sleep(5)
                continue
            if 1 <= now.hour < 9 and (now.hour > 1 or now.minute >= 3):
                if self.submit_lock.acquire(blocking=False):
                    try:
                        if self.process and self.process.poll() is None and self.active is None and not self.pending:
                            self.stop('night_quiet')
                    finally:self.submit_lock.release()
            time.sleep(20)

    def write_state(self):
        with self.mutex:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps({**self.state, 'updated_at': iso()}, ensure_ascii=False), encoding='utf-8')
            os.chmod(tmp,0o600)
            os.replace(tmp, STATE)

    def start(self, model, effort):
        b=self.b
        if self.process and self.process.poll() is None:
            return
        if self.reader_thread and self.reader_thread is not threading.current_thread():
            self.reader_thread.join(timeout=15)
        if not b.CLAUDE_SESSION_ID:
            raise RuntimeError('No verified existing Claude session')
        if not b.CLAUDE_RUN_LOCK.locked() and not b.CLAUDE_RUN_LOCK.acquire(blocking=True):
            raise RuntimeError('Claude session is busy')
        self.model,self.effort=model,effort
        self.cumulative={};self.cumulative_cost=0
        allowed_tools=os.environ.get('LIUSHA_ALLOWED_TOOLS', 'CronList,CronDelete')
        if wake_preference_enabled():allowed_tools='ScheduleWakeup,'+allowed_tools
        cmd=[os.environ.get('LIUSHA_CLAUDE_BIN', 'claude'),'--resume',b.CLAUDE_SESSION_ID,'--model',model,'--effort',effort,
             '--append-system-prompt',b.OUTPUT_PROTOCOL_PROMPT,'-p','--input-format','stream-json',
             '--output-format','stream-json','--verbose','--include-partial-messages','--replay-user-messages',
             '--mcp-config',str(MCP_CONFIG),'--strict-mcp-config',
             '--allowedTools',allowed_tools,
             f'--add-dir={b.UPLOAD_DIR}']
        if not wake_preference_enabled():
            cmd.extend(['--disallowedTools','ScheduleWakeup'])
        env={**os.environ,'ENABLE_CLAUDEAI_MCP_SERVERS':'false',
             'CLAUDE_CODE_ENABLE_TASKS':'false'}
        self.process=b.subprocess.Popen(cmd,cwd=b._CLAUDE_PROJECT_CWD,env=env,stdin=b.subprocess.PIPE,
            stdout=b.subprocess.PIPE,stderr=b.subprocess.PIPE,text=True,bufsize=1,start_new_session=True)
        proc=self.process
        self.reader_thread=threading.Thread(target=self.read,args=(proc,),daemon=True)
        self.reader_thread.start()
        threading.Thread(target=self.drain_errors,args=(proc,),daemon=True).start()

    def drain_errors(self,proc):
        for line in proc.stderr:
            # Keep stderr in server journal without echoing payloads or credentials.
            if line.strip(): print('CC_NATIVE_STDERR',self.b._safe_work_text(line,240),flush=True)

    def enqueue(self, text, turn):
        with self.mutex:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError('Claude process is unavailable')
            self.pending[turn.id]=turn
            self.process.stdin.write(json.dumps({'type':'user','uuid':turn.id,
                'message':{'role':'user','content':text}},ensure_ascii=False)+'\n')
            self.process.stdin.flush()

    def request(self,*args,**kwargs):
        self.waiting_users+=1
        try:return self._request(*args,**kwargs)
        finally:self.waiting_users-=1

    def _request(self,prompt,attachments=None,api_session='',ts='',relay_msg_id='',model_requested='',effort_requested='',**_):
        b=self.b
        with self.submit_lock:
            active=self.active
            if active and not active.done.wait(600):
                self.stop('timeout');return False
            model=b.MODEL_MAP.get(model_requested,b._DEFAULT_MODEL)
            effort=effort_requested if effort_requested in ('low','medium','high','xhigh','max') else 'low'
            self.api_session=api_session
            fresh=not self.process or self.process.poll() is not None
            if fresh: self.start(model,effort)
            # A model/effort switch uses documented stream control messages,
            # retaining the same process and conversation.
            if model!=self.model:
                self.control('set_model',model=model);self.model=model
            if effort!=self.effort:
                self.control('apply_flag_settings',settings={'effortLevel':effort});self.effort=effort
            ctx=b.ambient.get()
            text=(f'[{ctx}]\n' if ctx else '')+(f'用户消息时间：{ts}\n' if ts else '')+b._build_prompt_with_attachments(prompt,attachments)
            prefix,ids=b.bridge_support.pending_context(api_session,b.CLAUDE_SESSION_ID or '')
            if wake_preference_enabled() and not quiet_hours() and not self.state.get('next_wakeup'):
                text += '\n\n[恢复自主醒来：只用一次 ScheduleWakeup，自选下次时间，prompt 原样用下段，不加 /loop。]\n'+WAKE_PROMPT
            turn=Turn(b,'chat',api_session,relay_msg_id,model,effort)
            self.enqueue(prefix+text,turn)
            if not turn.done.wait(600):
                self.stop('timeout');return False
            if turn.ok:b.bridge_support.mark_synced(ids)
            if '（自动晚安）' in prompt:
                self.stop('night_quiet')
            return turn.ok

    def control(self,subtype,wait=True,**data):
        # Replies are consumed by the reader; a rejected control fails visibly.
        ident=str(uuid.uuid4())
        event=threading.Event();result={}
        self.controls[ident]=(event,result)
        self.process.stdin.write(json.dumps({'type':'control_request','request_id':ident,'request':{'subtype':subtype,**data}})+'\n')
        self.process.stdin.flush()
        if not wait:return
        if not event.wait(15):raise RuntimeError('Claude control timeout: '+subtype)
        if result.get('subtype')!='success':raise RuntimeError('Claude control rejected: '+subtype)

    def arm(self):
        with self.submit_lock:
            if self.state.get('next_wakeup'):
                return True
            if not wake_preference_enabled():
                self.state.update(enabled=False,status='user_disabled',next_wakeup=None)
                self.write_state()
                return False
            if quiet_hours():
                self.state.update(enabled=False,status='night_quiet',next_wakeup=None)
                self.write_state()
                return False
            if not recent_cache_activity():
                self.state.update(enabled=False,status='waiting_for_chat_to_warm_cache')
                self.write_state()
                return False
            model,effort=recent_chat_settings(self.b.CLAUDE_SESSION_ID)
            self.start(model,effort)
            turn=Turn(self.b,'setup',model=self.model,effort=self.effort)
            self.enqueue(ARM_PROMPT,turn)
            if not turn.done.wait(180):
                self.stop('arming_timeout');return False
            ok=turn.ok and bool(self.state.get('next_wakeup'))
            if not ok:
                self.state.update(enabled=False,status='arming_failed');self.write_state()
                # A subscription-limit response is synthetic and has no token
                # usage. Retry once after the short reset window while the
                # preceding real turn can still be cached.
                if (turn.last_model == '<synthetic>' and self.arm_retry_attempts < 1
                        and wake_preference_enabled() and not quiet_hours()):
                    self.arm_retry_attempts += 1
                    retry=threading.Timer(19*60,self.arm)
                    retry.daemon=True;retry.start()
            else:
                self.arm_retry_attempts = 0
            return ok

    def handoff(self, engine, prompt, metadata):
        from continuity_compaction.handoff import load_state, should_handoff
        _,state=load_state(engine.state_root)
        if not should_handoff(state,metadata,state_root=engine.state_root):
            return {'outcome':'existing_chat_path','claude_calls':0}
        with self.submit_lock:
            active=self.active
            if active and not active.done.wait(600):
                return {'outcome':'claude_global_lock_busy_recoverable'}
            self.stop('session_handoff')
            if self.process:
                self.process.wait(timeout=15)
            if self.reader_thread:self.reader_thread.join(timeout=15)
            if self.b.CLAUDE_RUN_LOCK.locked():self.b.CLAUDE_RUN_LOCK.release()
            return engine.execute(prompt,metadata)

    def emit(self,t,kind,text='',done=False,summary=''):
        if t.kind=='chat':
            self.b.send(kind,text,t.id,done=done,api_session=t.api_session,thinking_summary=summary)

    def visible(self,t,events):
        after_answer_end = False
        for kind,text in events:
            if kind=='error' and text=='text_after_answer_end':
                # Claude Code can ask for a visible continuation after a
                # protocol-formatted answer. That continuation repeats the
                # answer, but it is not a second chat message.
                after_answer_end = True
                continue
            if kind=='summary':
                t.summary=text
                if not t.thinking:self.emit(t,'thinking_delta',text)
                self.emit(t,'thinking_delta',done=True,summary=text);t.summary_closed=True
            elif kind=='answer' and not after_answer_end:
                t.answer+=text
                self.emit(t,'reply_delta',text)

    def begin(self,t):
        self.active=t
        if t.kind=='chat':
            Path(self.b._ACTIVE_PID_FILE).write_text(str(self.process.pid))
            self.b.send_work_status(t.id,t.api_session,t.started,phase='thinking',action='正在处理当前请求')
        elif t.kind=='wake':
            self.state.update(status='awake',next_wakeup=None);self.write_state()
            t.watchdog=threading.Timer(180,lambda:self.stop('wake_timeout') if self.active is t else None)
            t.watchdog.daemon=True;t.watchdog.start()

    def read(self,proc):
        try:
            for line in proc.stdout:
                try: e=json.loads(line)
                except ValueError:continue
                typ=e.get('type')
                if e.get('parent_tool_use_id'):
                    continue
                if os.environ.get('CC_NATIVE_TRACE') and typ!='stream_event':
                    append(Path(os.environ['CC_NATIVE_TRACE']), {'type':typ,'subtype':e.get('subtype'),
                        'uuid':e.get('uuid'),'status':e.get('status'),'isReplay':e.get('isReplay'),
                        'content_type':type(e.get('message',{}).get('content')).__name__,
                        'pending':list(self.pending),'active':self.active.id if self.active else None})
                if typ=='control_response':
                    response=e.get('response',{})
                    control=self.controls.pop(response.get('request_id'),None)
                    if control:
                        event,result=control;result.update(response);event.set()
                    continue
                if typ=='system' and e.get('subtype')=='init':
                    if e.get('session_id')!=self.b.CLAUDE_SESSION_ID:
                        raise RuntimeError('Unexpected Claude session change')
                    if wake_preference_enabled() and 'ScheduleWakeup' not in e.get('tools',[]):raise RuntimeError('ScheduleWakeup unavailable')
                    self.state.update(enabled=wake_preference_enabled(),status='connected' if wake_preference_enabled() else 'chat_only');self.write_state()
                if typ=='user':
                    with self.mutex:
                        t=self.pending.pop(e.get('uuid'),None)
                    if t:self.begin(t)
                # Replayed stdin acknowledgments precede request events. Native
                # scheduler turns have no pending user acknowledgment.
                if self.active is None and (typ=='stream_event' or (typ=='system' and e.get('status')=='requesting')):
                    # Requesting can arrive before the replay acknowledgment.
                    # An outstanding stdin request is a chat/setup, not a wake.
                    with self.mutex:
                        pending_turn=next(iter(self.pending.values()),None)
                    self.begin(pending_turn or Turn(self.b,'wake',self.api_session,model=self.model,effort=self.effort))
                t=self.active
                if not t:continue
                if typ=='stream_event':
                    event=e.get('event') or {};delta=event.get('delta') or {}
                    if delta.get('type')=='text_delta':self.visible(t,t.parser.feed(delta.get('text','')))
                    elif delta.get('type')=='thinking_delta':
                        text=delta.get('thinking','');t.thinking+=text
                        hit=self.b._check_runaway(t.thinking[-4096:],len(t.thinking),time.monotonic()-t.clock)
                        if hit:raise RuntimeError('Thinking watchdog triggered')
                        self.emit(t,'thinking_delta',text)
                elif typ=='assistant':
                    t.last_usage=e.get('message',{}).get('usage') or t.last_usage
                    t.last_model=e.get('message',{}).get('model') or t.last_model
                    for block in e.get('message',{}).get('content',[]):
                        if block.get('type')=='tool_use':
                            t.tool_calls[block['id']]=block.get('name')
                            if t.kind=='chat':
                                tool,target=self.b._work_tool_details(block.get('name'),block.get('input'))
                                self.b.send_work_status(t.id,t.api_session,t.started,phase='tool',tool=tool,action='tool',target=target)
                elif typ=='user' and isinstance(e.get('tool_use_result'),dict):
                    result=e['tool_use_result']
                    if 'scheduledFor' in result:
                        value=result.get('scheduledFor')
                        next_at=dt.datetime.fromtimestamp(value/1000,dt.timezone.utc).isoformat() if value else None
                        self.state.update(next_wakeup=next_at,status='waiting' if value else 'stopped');self.write_state()
                elif typ=='result':
                    self.finish(t,e)
                    self.active=None
        except Exception as exc:
            print('CC_NATIVE_FAILED',type(exc).__name__,str(exc)[:120],flush=True)
            self.stop('runtime_error')
        finally:
            if self.state.get('status') not in ('night_quiet','user_disabled'):
                self.state.update(enabled=False,status='disconnected')
            else:self.state.update(enabled=False)
            self.write_state()
            for t in [self.active,*self.pending.values()]:
                if t and not t.done.is_set():
                    if t.kind=='chat':
                        self.emit(t,'reply_delta','连接中断，请重试这条消息。');self.emit(t,'reply_delta',done=True)
                        self.b.send_work_status(t.id,t.api_session,t.started,status='error',phase='error',action='连接中断')
                    t.done.set()
            self.pending.clear();self.active=None
            Path(self.b._ACTIVE_PID_FILE).unlink(missing_ok=True)
            if self.b.CLAUDE_RUN_LOCK.locked():self.b.CLAUDE_RUN_LOCK.release()

    def finish(self,t,e):
        if t.watchdog:t.watchdog.cancel()
        self.visible(t,t.parser.finish())
        if t.kind=='wake':
            t.summary,t.answer=wake_visible_output(t.parser.raw,t.summary,t.answer)
        t.ok=not e.get('is_error')
        usage=e.get('usage') or {}
        # Claude stream result usage is per turn; total_cost_usd is cumulative.
        cost=float(e.get('total_cost_usd') or 0)
        log={'timestamp':iso(),'relay_message_id':t.relay_id or None,'api_session':t.api_session or None,
             'claude_session_id':self.b.CLAUDE_SESSION_ID,'model_used':t.last_model,'effort':t.effort,
             'source':'cc_wakeup' if t.kind=='wake' else 'cc_wakeup_setup' if t.kind=='setup' else 'chat',
             'wake_id':t.id if t.kind=='wake' else None,'num_turns':e.get('num_turns'),
             'duration_ms':e.get('duration_ms'),'is_error':not t.ok,'total_cost_usd':max(0,cost-self.cumulative_cost)}
        self.cumulative_cost=cost
        for key in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens'):
            log[key]=usage.get(key)
        if t.last_usage:
            log['context_input_tokens']=sum(int(t.last_usage.get(key) or 0) for key in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens'))
        self.b._log_usage(log)
        if t.kind=='wake':
            # Silence is handled before sending anything to the chat relay.
            answer=t.answer.strip()
            if '[[SILENT]]' in answer or not t.ok:answer=''
            should_deliver=bool(answer) and not self.pending and not self.waiting_users
            record={'id':t.id,'timestamp':t.started,'summary':t.summary,'thinking':t.thinking or t.summary,
                    'answer':answer,'outcome':'error' if not t.ok else 'sent' if should_deliver else 'kept' if answer else 'silent',
                    'next_wakeup':self.state.get('next_wakeup'),'source':'cc_native'}
            append(RECORDS,record)
            # A user who returned while the wake was running gets their normal
            # reply next. The wake remains in 醒迹 without an extra interruption.
            if should_deliver:
                extra={'proactive':True,'self_initiated':True,'source':'cc_native','wake_id':t.id}
                if t.thinking or t.summary:
                    self.b.send('thinking_delta',t.thinking or t.summary,t.id,api_session=t.api_session,extra=extra)
                    self.b.send('thinking_delta','',t.id,done=True,api_session=t.api_session,thinking_summary=t.summary,extra=extra)
                self.b.send('reply_delta',answer,t.id,api_session=t.api_session,extra=extra)
                self.b.send('reply_delta','',t.id,done=True,api_session=t.api_session,extra=extra)
        elif t.kind=='chat':
            if not t.summary_closed:self.emit(t,'thinking_delta',done=True,summary=t.summary)
            if not t.ok:self.emit(t,'reply_delta','本次回复未完成，请稍后重试。')
            self.emit(t,'reply_delta',done=True)
            self.b.send_work_status(t.id,t.api_session,t.started,status='done' if t.ok else 'error',phase='complete',action='任务完成' if t.ok else '请求遇到异常')
            Path(self.b._ACTIVE_PID_FILE).unlink(missing_ok=True)
        t.done.set()

    def stop(self,reason):
        self.state.update(enabled=False,status=reason)
        if reason in ('night_quiet','user_disabled'):self.state['next_wakeup']=None
        self.write_state()
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid,signal.SIGTERM)
