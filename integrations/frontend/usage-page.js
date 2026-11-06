(function initUsagePage(){
  const page=$("#usagePage"),open=$("#usageOpen"),back=$("#usageBack"),refresh=$("#usageRefresh"),recent=$("#usageRecent");
  if(!page||!open)return;
  const cacheNames={hit:"命中",partial_hit:"部分命中",cold_rebuild:"大量缓存创建",uncached:"未缓存"};
  let chatTop=0,loading=false;
  const number=n=>Number(n||0).toLocaleString("zh-CN");
  const modelName=m=>String(m||"未知模型").replace(/^claude-/,"").replaceAll("-"," ");
  const timeLabel=v=>{const d=new Date(v||"");return isNaN(d)?"时间未知":d.toLocaleString("zh-CN",{timeZone:TZ,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})};
  const money=n=>`$${Number(n||0).toFixed(4)}`;
  const sourceNames={chat:"对话回复",bubble:"自动冒泡",cc_wakeup:"自主唤醒",cc_wakeup_setup:"唤醒设置",cache_keepalive:"静默保活",other:"其他"};
  const cacheLabel=row=>row?.cache_status==="cold_rebuild"&&Number(row?.cache_read_tokens||0)>0?"部分命中 / 大量创建":cacheNames[row?.cache_status]||"调用";
  function render(d){
    const latest=d.latest||{},context=Number(latest.context_tokens||0),ratio=Math.max(0,Math.min(1,Number(latest.cache_read_ratio||0)));
    $("#usageModel").textContent=modelName(latest.model)+(latest.effort?` · effort ${latest.effort}`:"");
    $("#usageContext").textContent=context?`${formatK(context)} tokens`:"暂无记录";
    $("#usageLimit").textContent="最近一次调用的输入量";
    $("#usageCacheState").textContent=latest.timestamp?cacheLabel(latest):"暂无记录";
    $("#usageCacheRatio").textContent=latest.timestamp?`${Math.round(ratio*100)}%`:"—";
    $("#usageCacheRead").textContent=latest.timestamp?formatK(latest.cache_read_tokens):"—";
    $("#usageCacheCreated").textContent=latest.timestamp?formatK(latest.cache_creation_tokens):"—";
    const sid=String(d.active_session_id||"");
    $("#usageSummary").textContent=latest.timestamp?`最近调用 ${timeLabel(latest.timestamp)}。这里的 ${formatK(context)} 是这一次处理的输入 token，包含缓存读取、缓存创建和少量新输入；不是剩余额度，也不是压缩进度。是否压缩由 Claude Code 判断，可在下方查看实际压缩记录。`:"还没有可显示的调用记录。打开本页不会调用 Claude。";
    const k=d.keepalive||{},keep=$("#usageKeepalive"),hasRefresh=!!k.last_refresh&&(Number(k.last_cache_read||0)>0||Number(k.last_cache_created||0)>0);
    keep.classList.toggle("is-disabled",!!k.disabled);keep.classList.toggle("is-waiting",!!k.waiting_for_real_activity||!hasRefresh);
    $("#usageKeepaliveState").textContent=k.disabled?"已停用":k.waiting_for_real_activity?"等待下一次聊天":hasRefresh?"运行中":"等待下一次保活";
    $("#usageKeepaliveNext").textContent=!k.disabled&&!k.waiting_for_real_activity&&k.next_due?`下次 ${timeLabel(k.next_due)}`:"";
    $("#usageKeepaliveDetail").innerHTML=hasRefresh?`上次 ${escapeHtml(timeLabel(k.last_refresh))}${k.last_model?` · ${escapeHtml(modelName(k.last_model))}`:""}<br>读取 ${escapeHtml(number(k.last_cache_read))} · 重建 ${escapeHtml(number(k.last_cache_created))}`:k.disabled?`安全保护已停止保活${k.disabled_reason?` · ${escapeHtml(k.disabled_reason)}`:""}`:k.waiting_for_real_activity?"额度不足或缓存已过期，发出下一条真实消息后会重新安排。":"尚未产生保活记录；打开本页不会调用 Claude。";
    if(k.managed_by==="cc_native"){
      $("#usageKeepaliveState").textContent="自主唤醒可用";
      $("#usageKeepaliveNext").textContent=k.next_due?`下次 ${timeLabel(k.next_due)}`:"";
      $("#usageKeepaliveDetail").textContent="自主唤醒与自动冒泡是两类调用；各自的实际 token 消耗请看下方账本。";
    }
    const sk=d.silent_keepalive||{};
    if(sk.checked_at){
      const fresh=Date.now()-Date.parse(sk.checked_at)<180000;
      const waiting=["expired_wait_for_chat","no_usage_wait_for_chat","unexpected_rebuild_wait_for_chat","paused_after_failure","configuration_changed_wait_for_chat"].includes(sk.status);
      $("#usageKeepaliveState").textContent=!fresh?"静默保活 · 检查延迟":waiting?"静默保活 · 等待聊天恢复":"静默保活 · 检查正常";
      $("#usageKeepaliveNext").textContent=sk.status==="native_chat_owns_session"?"当前由聊天接管":sk.next_due&&!waiting?`下次 ${timeLabel(Number(sk.next_due)*1000)}`:"";
      $("#usageKeepaliveDetail").textContent=(sk.last_refresh?`上次 ${timeLabel(Number(sk.last_refresh)*1000)} · 读取 ${number(sk.last_cache_read)} · 创建 ${number(sk.last_cache_created)}。`:"尚未完成首次静默刷新。")+"夜间仅刷新缓存，不发消息、不自主唤醒；实际用量列在下方。";
    }
  }
  const metric=(label,value)=>`<div class="usage-ledger-line"><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value))}</span></div>`;
  function renderLedger(d,append=false){
    const s=d.summary||{},names=sourceNames;
    if(!append){
      $("#usageLedgerSummary").innerHTML=`<strong>${escapeHtml($("#usagePeriod").selectedOptions[0].textContent)} · ${number(s.calls)} 次调用</strong>`+
        metric("CLI 费用估算",money(s.cost_usd))+metric("缓存读取",number(s.cache_read_tokens))+metric("缓存创建",number(s.cache_creation_tokens))+
        metric("非缓存输入",number(s.input_tokens))+metric("输出",number(s.output_tokens))+metric("累计调用轮次",number(s.turns))+
        metric("累计耗时",`${(Number(s.duration_ms||0)/60000).toFixed(1)} 分钟`)+metric("错误调用",number(s.errors));
      const groups=Object.entries(d.by_source||{}).filter(([,v])=>v.calls);
      $("#usageLedgerSources").innerHTML=`<strong>来源构成</strong>${groups.map(([key,v])=>metric(`${names[key]||key} · ${number(v.calls)} 次`,`${money(v.cost_usd)} · 创建 ${number(v.cache_creation_tokens)}`)).join("")}`+
        `<div class="usage-muted" style="margin-top:7px">模型：${Object.entries(d.by_model||{}).map(([key,v])=>`${escapeHtml(modelName(key))} ${number(v.calls)} 次 / ${money(v.cost_usd)}`).join("；")||"暂无"}</div>`;
      const comp=d.compactions||[];
      $("#usageCompactions").innerHTML=`<strong>自动压缩 · 当前 Claude 会话 ${number(d.compaction_count)} 次</strong>`+(comp.length?comp.slice(0,8).map(c=>metric(timeLabel(c.timestamp),`${number(c.before_tokens)} → ${number(c.after_tokens)} tokens · ${(Number(c.duration_ms||0)/1000).toFixed(0)} 秒`)).join(""):`<div class="usage-muted">所选时段未记录到压缩事件</div>`)+`<div class="usage-muted">压缩事件取自当前会话记录；压缩后的缓存创建可见于逐次调用，不能单凭“创建”判断为冷重建。</div>`;
    }
    $("#usageLedgerCount").textContent=`所选时段共 ${number(d.total)} 条 · 只显示最近 ${number(d.items.length)} 条`;
    const cards=(d.items||[]).map(row=>{const status=row.cache_read_tokens?row.cache_creation_tokens>=10000?"部分命中 / 大量创建":"命中":"未命中 / 新建";const tools=Array.isArray(row.tools)?row.tools:[];const counts={};tools.forEach(name=>{counts[name]=(counts[name]||0)+1});const toolLabel=!row.tools_available?"工具记录不可用":tools.length?Object.entries(counts).map(([name,count])=>`${escapeHtml(name)} ×${number(count)}`).join(" · "):"无工具调用";
      return `<article class="usage-card usage-record"><div class="usage-record-top"><span class="usage-record-state">${escapeHtml(names[row.source]||row.source)} · ${escapeHtml(status)}</span><span class="usage-record-time">${escapeHtml(timeLabel(row.timestamp))}</span></div><div class="usage-record-detail">${escapeHtml(modelName(row.model))}${row.effort?` · effort ${escapeHtml(row.effort)}`:""} · ${row.usage_partial?"≥ ":""}${money(row.cost_usd)}${row.is_error?" · 出错":""}<br>读 ${number(row.cache_read_tokens)} · 建 ${number(row.cache_creation_tokens)} · 入 ${number(row.input_tokens)} · 出 ${row.usage_partial?"未完整回报":number(row.output_tokens)}</div><details><summary>查看完整明细</summary><div class="usage-record-grid"><span>本轮输入 ${number(row.context_tokens)}</span><span>读取比例 ${Math.round(Number(row.cache_read_ratio||0)*100)}%</span><span>轮次 ${number(row.num_turns)}</span><span>耗时 ${(Number(row.duration_ms||0)/1000).toFixed(1)} 秒</span></div><div class="usage-tool-line">工具：${toolLabel}</div>${row.usage_partial?'<div class="usage-muted">静默刷新在确认缓存读取后停止，输出未完整回报，费用为已知输入的下限估算。</div>':""}</details></article>`}).join("");
    recent.innerHTML=append?recent.innerHTML+cards:cards||'<div class="usage-empty">这个时间段没有调用记录</div>';
  }
  async function fetchJson(path){const r=await fetch(`${API_BASE}${path}`,{headers:authHeaders(),cache:"no-store"});if(r.status===401){handle401();throw Error("401")}if(!r.ok)throw Error(String(r.status));return r.json()}
  async function loadLedger(offset=0){const p=encodeURIComponent($("#usagePeriod").value),s=encodeURIComponent($("#usageSource").value);const d=await fetchJson(`/app/usage_ledger?period=${p}&source=${s}&offset=${offset}`);renderLedger(d,offset>0)}
  async function load({notify=false}={}){if(loading)return;loading=true;if(refresh){refresh.disabled=true;refresh.classList.add("loading");refresh.setAttribute("aria-busy","true")}try{const [runtime]=await Promise.all([fetchJson("/app/claude_runtime"),loadLedger()]);render(runtime);if(notify)showToast("用量已刷新")}catch(_){recent.innerHTML='<div class="usage-empty">读取失败，请稍后刷新</div>';if(notify)showToast("刷新失败，请稍后重试")}finally{loading=false;if(refresh){refresh.disabled=false;refresh.classList.remove("loading");refresh.removeAttribute("aria-busy")}}}
  function show(){window.closeChatSidebar?.();chatTop=scrollEl.scrollTop;page.classList.remove("hidden");page.setAttribute("aria-hidden","false");load()}
  function hide(){page.classList.add("hidden");page.setAttribute("aria-hidden","true");requestAnimationFrame(()=>scrollEl.scrollTop=chatTop)}
  open.onclick=show;back.onclick=hide;refresh.onclick=()=>load({notify:true});$("#usagePeriod").onchange=()=>load();$("#usageSource").onchange=()=>load();window.addEventListener("chatroutecommit",hide);
})();
