(function initAwakeningPage(){
  const page=$("#awakeningPage"),list=$("#awakeningList"),refresh=$("#awakeningRefresh"),status=$("#awakeningState"),toggle=$("#awakeningSwitch"),toggleNote=$("#awakeningSwitchNote");
  let chatTop=0,loading=false,generation=0;
  const stamp=v=>{const d=new Date(v||"");return isNaN(d)?"":d.toLocaleString("zh-CN",{timeZone:TZ,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})};
  const decisions={outside_window:"夜间静默，按计划跳过",recent_message:"刚有消息，无需冒泡",recent_model_call:"CC 刚调用过，无需冒泡",wait_for_user_today:"等待今天的第一条用户消息",cache_too_old:"缓存已过期，跳过冒泡",chat_active:"聊天进行中，跳过",night_cache_too_old:"夜间缓存已过期，跳过晚安消息",session_changed:"会话已变化，跳过",no_usage:"暂无调用记录",sent:"已发出自动消息",ready:"准备发送",night_ready:"准备发送晚安消息"};
  let wakeSelected=new Date().toLocaleDateString('en-CA',{timeZone:TZ}),wakeMonth=wakeSelected.slice(0,7),wakeDates={};
  const wakeCal=document.getElementById('wakeCalendarGrid'),wakeTitle=document.getElementById('wakeCalendarMonth');
  function drawWakeCalendar(){
    const [y,m]=wakeMonth.split('-').map(Number),start=(new Date(y,m-1,1).getDay()+6)%7,days=new Date(y,m,0).getDate(),today=new Date().toLocaleDateString('en-CA',{timeZone:TZ});
    wakeTitle.textContent=`${y}年 ${m}月`;wakeCal.replaceChildren();
    for(const text of ['一','二','三','四','五','六','日']){const s=document.createElement('span');s.className='wg-week';s.textContent=text;wakeCal.append(s)}
    for(let i=0;i<start;i++)wakeCal.append(document.createElement('span'));
    for(let n=1;n<=days;n++){const key=`${wakeMonth}-${String(n).padStart(2,'0')}`,b=document.createElement('button');b.type='button';b.className='wg-day'+(wakeDates[key]?' has':'');b.textContent=n;b.disabled=key>today;b.setAttribute('aria-pressed',String(key===wakeSelected));b.setAttribute('aria-label',`${m}月${n}日${wakeDates[key]?`，${wakeDates[key]}次唤醒`:''}`);b.onclick=()=>{wakeSelected=key;drawWakeCalendar();load(true)};wakeCal.append(b)}
    document.getElementById('wakeNextMonth').disabled=wakeMonth>=today.slice(0,7);
  }
  function changeWakeMonth(step){const [y,m]=wakeMonth.split('-').map(Number),d=new Date(y,m-1+step,1);wakeMonth=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;wakeSelected=wakeMonth+'-01';drawWakeCalendar();load(true)}
  document.getElementById('wakePrevMonth').onclick=()=>changeWakeMonth(-1);document.getElementById('wakeNextMonth').onclick=()=>changeWakeMonth(1);drawWakeCalendar();

  async function load(force=false){
    if(loading&&!force)return;loading=true;const token=++generation;refresh.disabled=true;
    try{
      const r=await fetch(`${API_BASE}/app/awakening?day=${encodeURIComponent(wakeSelected)}&month=${encodeURIComponent(wakeMonth)}`,{headers:authHeaders(),cache:"no-store"});
      if(r.status===401){handle401();throw Error("401")}if(!r.ok)throw Error(String(r.status));
      const d=await r.json();if(token!==generation)return;
      const wanted=!!d.desired_enabled,script=d.script||{},lastCheck=script.last_check?new Date(script.last_check):null,checkFresh=!!lastCheck&&!isNaN(lastCheck)&&(Date.now()-lastCheck.getTime())<180000;
      toggle.disabled=false;toggle.setAttribute("aria-checked",String(wanted));
      toggleNote.textContent=!wanted?(d.status==="user_disabled"?"已关闭；CC 不再安排自主唤醒。":"已关闭；现有唤醒会在约 20 秒内停止。"):d.enabled?"已开启，CC 可自行安排下一次唤醒。":d.status==="night_quiet"?"已开启；01:00–09:00 夜间静默。":"已开启；下次白天聊天后恢复安排。";
      status.textContent=!wanted?"自主唤醒已关闭":d.enabled?(d.next_wakeup?`下次醒来 · ${stamp(d.next_wakeup)}`:"自主唤醒已连接，等待安排"):d.status==="night_quiet"?"夜间静默，不会自动醒来":"等待下一条聊天启动自主唤醒";
      $("#awakeningScriptTitle").textContent=!script.timer_active?"自动冒泡脚本 · 定时器未运行":checkFresh?"自动冒泡脚本 · 每分钟检查正常":"自动冒泡脚本 · 检查延迟";
      $("#awakeningScriptDetail").textContent=`最近检查 ${script.last_check?stamp(script.last_check):"暂无"} · ${decisions[script.last_decision]||script.last_decision||"暂无结果"}。${script.last_auto_message?`上次自动消息 ${stamp(script.last_auto_message)}（${script.last_auto_kind||"保活"}）。`:"尚未记录到自动消息。"}脚本与上方 CC 自主唤醒分别运行。`;
      wakeDates=d.dates||{};drawWakeCalendar();document.getElementById("wakeSelectedLabel").textContent=`${wakeSelected.replaceAll("-",".")} · ${(d.items||[]).length} 次醒来`;
      list.replaceChildren();
      if(!d.items?.length){const empty=document.createElement("div");empty.className="usage-empty";empty.textContent="这一天还没有唤醒记录。";list.append(empty);return}
      for(const item of d.items){
        const article=document.createElement("article");article.className="awakening-record";
        const meta=document.createElement("div");meta.className="awakening-meta";meta.textContent=stamp(item.timestamp)+(item.outcome==="silent"?" · 安静待了一会儿":item.outcome==="error"?" · 这次未完成":"");article.append(meta);
        const base={id:`wake-${item.id}`,ts:item.timestamp,from:"ai",status:"sent",meta:{}};
        if(item.summary||item.thinking){article.append(makeThinking({message:{...base,_key:`wake-think:${item.id}`,kind:"thinking",text:item.thinking||item.summary,thinking:item.thinking||item.summary,thinkingSummary:item.summary||"醒来时的念头"}}))}
        if(item.answer){article.append(makeMessage({message:{...base,_key:`wake-reply:${item.id}`,kind:"reply",text:item.answer},grouped:false,tail:true}))}
        list.append(article);
      }
    }catch(_){if(token===generation){list.innerHTML='<div class="usage-empty">读取失败，请点右上角重试</div>'}}
    finally{loading=false;refresh.disabled=false}
  }
  function hide(){generation++;page.classList.add("hidden");page.setAttribute("aria-hidden","true");requestAnimationFrame(()=>{scrollEl.scrollTop=chatTop})}
  toggle.onclick=async()=>{if(toggle.disabled)return;const enabled=toggle.getAttribute("aria-checked")!=="true";toggle.disabled=true;try{const r=await fetch(`${API_BASE}/app/awakening/control`,{method:"POST",headers:{...authHeaders(),"Content-Type":"application/json"},body:JSON.stringify({enabled})});if(r.status===401){handle401();throw Error("401")}if(!r.ok)throw Error(String(r.status));toggle.setAttribute("aria-checked",String(enabled));toggleNote.textContent=enabled?"已保存；下次白天聊天后恢复安排。":"已保存；约 20 秒内停止现有唤醒。";showToast(enabled?"已开启自主唤醒":"已关闭自主唤醒");await load();setTimeout(()=>{if(!page.classList.contains("hidden"))load()},23000)}catch(_){showToast("切换失败，请重试");toggle.disabled=false}};
  $("#awakeningOpen").onclick=()=>{wakeSelected=new Date().toLocaleDateString("en-CA",{timeZone:TZ});wakeMonth=wakeSelected.slice(0,7);drawWakeCalendar();window.closeChatSidebar?.();chatTop=scrollEl.scrollTop;page.classList.remove("hidden");page.setAttribute("aria-hidden","false");load(true)};
  $("#awakeningBack").onclick=hide;refresh.onclick=()=>load(true);window.addEventListener("chatroutecommit",hide);
})();
