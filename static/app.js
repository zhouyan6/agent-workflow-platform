/* 科研工作台 v3.0 — 论文复现流水线前端逻辑 */
'use strict';
const $=id=>document.getElementById(id);
/* 产品内轻提示（替代浏览器 alert/confirm） */
function toast(msg,type){
  const t=document.createElement('div');
  t.className='toast'+(type?' '+type:'');
  t.textContent=msg;
  document.body.appendChild(t);
  requestAnimationFrame(()=>t.classList.add('on'));
  setTimeout(()=>{t.classList.remove('on');setTimeout(()=>t.remove(),300)},3000);
}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const shortCmd=c=>{c=String(c).replace(/\s+-+/g,' -').replace(/\s+/g,' ');return c.length>90?c.slice(0,90)+'…':c};
const D8=86400000;
const fmtDur=sec=>{sec=Math.max(0,Math.floor(sec));const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;return h?`${h}时${m}分`:(m?`${m}分${s}秒`:`${s}秒`)};
const fmtIv=sec=>sec<60?sec+'秒':(sec%3600?(sec/60)+'分钟':(sec/3600)+'小时');
const fmtN=v=>v==null?'—':(Math.abs(v)>=100?v.toFixed(1):v.toPrecision(4));
const fmtT=t=>new Date(t*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const fmtD=t=>new Date(t*1000).toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'});
const overlapSec=(a,b,c,d)=>Math.max(0,Math.min(b,d)-Math.max(a,c));
const isRun=i=>i.end>Date.now()/1000-120;
const cssVar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ================= 登录与角色 ================= */
window.__user=null;
const _rawFetch=window.fetch.bind(window);
window.fetch=(u,o={})=>{
  const url=typeof u==='string'?u:(u&&u.url)||'';
  if(localStorage.wb_token&&url.startsWith('/api')){
    o=Object.assign({},o,{headers:Object.assign({},o.headers,
      {Authorization:'Bearer '+localStorage.wb_token})});
  }
  return _rawFetch(u,o).then(r=>{
    if(r.status===401&&!url.includes('/api/login'))showLogin();
    return r;
  });
};
function showLogin(){
  window.__user=null;
  $('login-page').classList.add('on');
  $('login-err').textContent='';
}
async function doLogin(){
  const u=$('login-user').value.trim(),p=$('login-pass').value;
  if(!u||!p){$('login-err').textContent='请输入用户名和密码';return}
  const r=await fetch('/api/login',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:u,password:p})});
  if(!r.ok){$('login-err').textContent='用户名或密码错误';return}
  const me=await r.json();
  localStorage.wb_token=me.token;
  window.__user=me;
  $('login-page').classList.remove('on');
  applyUser();
  bootStart();
}
async function doLogout(){
  await fetch('/api/logout',{method:'POST'});
  localStorage.removeItem('wb_token');
  location.reload();
}
function toggleUserMenu(){$('user-menu').classList.toggle('on')}
document.addEventListener('click',e=>{
  const m=$('user-menu');
  if(m&&m.classList.contains('on')&&!m.contains(e.target)&&e.target.id!=='avatar')
    m.classList.remove('on');
});
function openChangePw(){
  $('user-menu').classList.remove('on');
  ['pw-old','pw-new','pw-new2'].forEach(id=>{const el=$(id);if(el)el.value=''});
  $('pw-err').textContent='';
  $('pw-modal').classList.add('on');
  setTimeout(()=>$('pw-old')&&$('pw-old').focus(),50);
}
function closeChangePw(){$('pw-modal').classList.remove('on')}
async function submitChangePw(){
  const oldP=$('pw-old').value, neu=$('pw-new').value, neu2=$('pw-new2').value;
  const err=$('pw-err');
  if(!oldP||!neu){err.textContent='请填写当前密码和新密码';return}
  if(neu.length<6){err.textContent='新密码至少 6 位';return}
  if(neu!==neu2){err.textContent='两次新密码不一致';return}
  if(neu===oldP){err.textContent='新密码不能与当前密码相同';return}
  err.textContent='';
  const r=await fetch('/api/me/password',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({old_password:oldP,new_password:neu})});
  if(!r.ok){err.textContent=await errText(r);return}
  closeChangePw();
  localStorage.removeItem('wb_token');
  toast('密码已更新，请重新登录','ok');
  setTimeout(()=>{location.reload()},600);
}
function applyUser(){
  const u=window.__user;
  if(!u)return;
  document.body.classList.toggle('is-advisor',u.role==='advisor');
  $('avatar').textContent=(u.username[0]||'?').toUpperCase();
  $('user-menu-name').textContent=u.username;
  $('user-menu-role').textContent=u.role==='advisor'?'导师 · 管理员':'学生';
  document.querySelector('.nav-item[data-page="设置"]').style.display='';
  if(u.role==='advisor'){
    const of=$('owner-filter');
    if(of)of.innerHTML=['','zhouyan','lihan','jiyang'].map(o=>
      `<span class="chip ${(__ownerFilter===o)?'on':''}" onclick="setOwnerFilter('${o}')">`
      +(o||'全部')+'</span>').join('');
  }
}
let __ownerFilter='';
function setOwnerFilter(o){__ownerFilter=o;applyUser();loadTasks()}

/* ================= 路由与上下文 ================= */
const PAGES=['欢迎','调研','执行','日志','对照','助手','设置'];
let curPage='欢迎';
window.__ctx={paper:null,run:null};
try{const s=JSON.parse(localStorage.getItem('wb3_ctx')||'{}');if(s.paper)window.__ctx.paper=s.paper;if(s.run)window.__ctx.run=s.run}catch(e){}
function saveCtx(){localStorage.setItem('wb3_ctx',JSON.stringify(window.__ctx))}
function showPage(name){
  curPage=name;
  PAGES.forEach(p=>$('page-'+p).classList.toggle('on',p===name));
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('on',b.dataset.page===name));
  if(name==='调研')renderPaper();
  if(name==='执行')renderExecPrefill();
  if(name==='对照')renderComparePage();
  if(name==='设置'){loadChatConfig();loadSettings();loadFeishu();loadUsers()}
  if(name==='欢迎')renderFocus();
  if(name==='日志'){syncCurveFromFocus();refreshChart()}
  if(name==='助手')agentOpen();
}
function openLab(){
  showPage('设置');
  setTimeout(()=>{
    const el=$('chat-config-card');
    if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
  },50);
}
document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>showPage(b.dataset.page));

/* ================= GPU ================= */
let gpuEs=null;
function startGpuStream(){
  if(gpuEs)gpuEs.close();
  gpuEs=new EventSource('/api/gpu/stream');
  gpuEs.onopen=()=>{$('top-conn').innerHTML='<span class="dot ok"></span> Runtime';
    $('runtime-dot').className='dot ok';$('runtime-txt').textContent='GPU Runtime 已连接';};
  gpuEs.onerror=()=>{$('top-conn').innerHTML='<span class="dot err"></span> Runtime';
    $('runtime-dot').className='dot err';$('runtime-txt').textContent='GPU Runtime 未连接';};
  gpuEs.onmessage=e=>renderGpu(JSON.parse(e.data));
}
const GPU_NAME='RTX 4090';
function gpuProcLabel(p){
  if(!p)return '';
  const cmd=p.cmd||p.name||'';
  if(/gpu_guard\.py\b/.test(cmd)){
    const m=cmd.match(/gpu_guard\.py\s+(\d+(?:\.\d+)?)/);
    return m?`占卡守卫 ${m[1]}G`:'占卡守卫';
  }
  const py=cmd.match(/([^\s/]+\.py\b)/);
  if(py)return py[1];
  const base=(p.name||cmd).split('/').pop();
  return (base||p.user||'进程').slice(0,28);
}
function renderGpu(snap){
  window.__gpuSnap=snap;
  const byPid=window.__liveByPid||{};
  const box=$('gpu-tiles');
  if(snap.error){box.innerHTML=`<div class="empty" style="grid-column:1/-1">${esc(snap.error)}</div>`;return}
  let idleN=0,sharedN=0;
  box.innerHTML=snap.gpus.map(g=>{
    const idle=!g.error&&g.util<10&&g.mem_used<2000;
    const shared=!idle&&!g.error&&g.util<10&&(g.mem_total-g.mem_used)>=(snap.idle_rule.shared_spare_mb||12000);
    if(idle)idleN++;else if(shared)sharedN++;
    const mine=g.procs.find(p=>p.user===snap.me);
    const top=mine||g.procs[0];
    const isGuard=top&&/gpu_guard\.py\b/.test(top.cmd||'');
    // 只有真正训练才标「训练中」；纯占卡守卫标「占卡」
    const stCls=g.error?'err':(mine?(isGuard?'warn':'blue'):(idle?'ok':(shared?'warn':'gray')));
    const stTxt=g.error?'异常':(mine?(isGuard?'占卡中':'训练中'):(idle?'空闲':(shared?'共用':'占用')));
    const barColor=mine&&!isGuard?'var(--blue)':(idle?'var(--green)':(g.error?'#E5E7EB':'#9CA3AF'));
    const util=Math.round(g.util||0);
    const memPct=g.error?0:Math.min(100,Math.round(g.mem_used/Math.max(g.mem_total,1)*100));
    let exp='空闲';
    if(top){
      if(mine&&byPid[mine.pid])exp=esc(shortCmd((byPid[mine.pid].name||'').split(' / ')[0]));
      else{
        const who=top.user||'?';
        const what=gpuProcLabel(top);
        exp=esc(mine?what:`${who} · ${what}`);
      }
    }
    const eta=mine&&byPid[mine.pid]&&byPid[mine.pid].eta;
    const temp=(g.temp!=null&&!g.error)?Math.round(g.temp)+'°C ':'';
    const tip=[`GPU${g.index} ${GPU_NAME}`,top?`pid ${top.pid} ${top.user||''}`:'',top&&top.cmd?top.cmd:''].filter(Boolean).join('\n');
    return `<div class="gpu-tile ${g.error?'err':''} ${mine&&!isGuard?'mine':''}" title="${esc(tip)}">
      <div class="hd"><b>GPU ${g.index}</b><span class="tag ${stCls}">${stTxt}</span></div>
      <div class="gpu-bar"><i style="width:${util}%;background:${barColor}"></i></div>
      <div class="lbl"><span>利用 ${util}%</span><span>${temp}${g.error?'—':(g.mem_used/1024).toFixed(1)+'/'+(g.mem_total/1024).toFixed(0)+'G'}</span></div>
      <div class="gpu-bar"><i style="width:${memPct}%;background:${mine&&!isGuard?'#93BEF7':'#C2C7CE'}"></i></div>
      <div class="ft" title="${esc(tip)}">${exp}${eta?` <span class="eta">⏳r${eta.round+1}/${eta.total}·${fmtDur(eta.eta_sec)}后</span>`:''}</div>
    </div>`;
  }).join('');
  $('gpu-summary').textContent=`空闲 ${idleN} · 共用 ${sharedN} · 故障 ${snap.gpus.filter(g=>g.error).length} / ${snap.gpus.length}`;
}

/* ================= live experiments ================= */
let liveExps=[],actItems=[];
let calCur=new Date(),calSelDay=new Date();
function loadLiveExps(){
  return fetch('/api/liveexps').then(r=>r.json()).then(ls=>{
    liveExps=ls;
    window.__liveByPid={};ls.forEach(l=>window.__liveByPid[l.pid]=l);
    renderRunList();renderMyExps();buildChatExps();
    if(window.__ctx.run&&window.__ctx.run.type==='live'){
      const still=ls.find(l=>l.jsonl===window.__ctx.run.jsonl);
      if(!still)window.__ctx.run.ended=true;
    }
  }).catch(()=>{});
}
let __sel=new Map(); // key -> {type,key,name,jsonl,color} 曲线多选
function findRunMeta(type,key){
  if(type==='live')return liveExps.find(x=>'l'+x.pid===key);
  return (window.__tasks||[]).find(x=>'t'+x.id===key);
}
function toggleRun(type,key){
  const meta=findRunMeta(type,key);
  if(!meta)return;
  if(__sel.has(key)){
    __sel.delete(key);
  }else{
    if(__sel.size>=8)__sel.delete(__sel.keys().next().value);
    __sel.set(key,{type,key,name:meta.name,
      jsonl:type==='live'?meta.jsonl:(meta.jsonl||null),
      log:type==='live'?meta.log:(meta.log_file||meta.log||null),
      color:chartColors[__sel.size%8]});
    selectRun(type,key); // 日志/架构跟随最新加入的实验
  }
  renderRunList();refreshChart();
}
function renderRunList(){
  const box=$('run-list');
  const livePids=new Set(liveExps.map(l=>l.pid));
  const items=[
    ...liveExps.map(l=>({key:'l'+l.pid,type:'live',name:l.name,gpu:l.gpu,live:l,
      status:'running',run:l.run||0,owner:l.owner})),
    ...(window.__tasks||[]).filter(t=>{
      if(!['running','pending','done','failed'].includes(t.status))return false;
      // 已有 live 条目（带 jsonl）的终端进程不再重复
      if(isExtTask(t)&&livePids.has(t.pid))return false;
      return true;
    }).map(t=>({key:'t'+t.id,type:'task',name:t.name,gpu:t.gpu_assigned,t,owner:t.owner,
        source:t.source||'workbench',pid:t.pid}))
  ];
  if(!items.length){box.innerHTML='<div class="empty">暂无实验运行</div>';return}
  const stTag=s=>s==='running'?'<span class="dot run"></span>':s==='pending'?'<span class="dot"></span>':s==='done'?'<span class="dot ok"></span>':'<span class="dot err"></span>';
  box.innerHTML=items.slice(0,30).map(r=>{
    const sel=window.__ctx.run&&window.__ctx.run.key===r.key;
    const curve=__sel.get(r.key);
    const st=r.status||(r.t?r.t.status:'running');
    const eta=r.live&&r.live.eta;
    const ownerTag=(window.__user&&window.__user.role==='advisor'&&r.owner)
      ?` · ${esc(r.owner)}`:'';
    const curveTag=curve?`<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${curve.color};margin-left:5px" title="曲线已叠加,点击取消"></span>`:'';
    const srcTag=r.type==='live'?' · 终端':(r.type==='task'?(r.source==='external'?' · 终端':' · 工作台'):'');
    return `<div class="run-item ${sel?'sel':''}" onclick="toggleRun('${r.type}','${r.key}')" title="点击叠加/取消曲线">
      <div class="t">${stTag(st)}${esc((r.name||'').slice(0,22))}${curveTag}</div>
      <div class="s">GPU${r.gpu||'—'}${eta?` · r${eta.round+1}/${eta.total} · ${fmtDur(eta.eta_sec)}`:''}${srcTag}${r.status==='pending'?' · 排队中':''}${ownerTag}</div>
    </div>`}).join('');
}
function selectRun(type,key){
  if(type==='live'){
    const l=liveExps.find(x=>'l'+x.pid===key);if(!l)return;
    window.__ctx.run={type:'live',key,jsonl:l.jsonl,log:l.log,name:l.name,pid:l.pid};
  }else{
    const t=(window.__tasks||[]).find(x=>'t'+x.id===key);if(!t)return;
    window.__ctx.run={type:'task',key,id:t.id,name:t.name,log_file:t.log_file,status:t.status,
      source:t.source||'workbench',pid:t.pid};
  }
  saveCtx();renderRunList();renderMyExps();
  showPage('日志');
  $('arch-card').style.display='block';
  // keep paper-driven metric; only default loss field if none chosen
  if(!$('chart-metric')||!$('chart-metric').value){
    selFields=new Set(['task']);cmpMetric='task';$('chart-mode').value='vl';
  }
  refreshChart();
  startRunLog();
  loadLossCard();
  pushRecent('run',window.__ctx.run.name);
}
function renderMyExps(){
  const tb=$('myexps');
  const livePids=new Set(liveExps.map(l=>l.pid));
  const rows=[
    ...liveExps.map(l=>({name:l.name,gpu:l.gpu,eta:l.eta,type:'live',key:'l'+l.pid,pid:l.pid})),
    ...(window.__tasks||[]).filter(t=>{
      if(!['running','pending'].includes(t.status))return false;
      if(isExtTask(t)&&livePids.has(t.pid))return false;
      return true;
    }).map(t=>({name:t.name,gpu:t.gpu_assigned,type:'task',key:'t'+t.id,id:t.id,status:t.status}))
  ];
  if(!rows.length){tb.innerHTML='<tr><td colspan="5" class="empty">暂无实验 —— 所有静默时间都是空闲卡</td></tr>';return}
  tb.innerHTML=rows.slice(0,10).map(r=>`<tr>
    <td style="font-size:12px">${esc((r.name||'').slice(0,34))}</td>
    <td class="num">GPU${r.gpu||'—'}</td>
    <td class="num">${r.eta?`r${r.eta.round+1}/${r.eta.total}`:(r.status==='pending'?'排队中':'—')}</td>
    <td class="num" style="font-size:11px">${r.eta?(new Date(Date.now()+r.eta.eta_sec*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})+' ('+fmtDur(r.eta.eta_sec)+')'):'—'}</td>
    <td><button class="sm" onclick="selectRun('${r.type}','${r.key}')">日志</button></td></tr>`).join('');
}

/* ================= 刷新周期(沿用 mon-interval) ================= */
let monTimer=null,logTimer=null,curFetcher=null,logEs=null;
function monIvSec(){return +($('mon-interval').value||30)}
function startMonTimer(){
  if(monTimer)clearInterval(monTimer);
  monTimer=setInterval(tick,monIvSec()*1000);
  tick();
}
let ticking=false;
async function tick(){
  if(ticking)return;ticking=true;
  try{await Promise.all([loadTasks(),loadLiveExps(),loadLossCard()])}catch(e){}
  ticking=false;
}

/* ================= tasks ================= */
const TASK_ST={
  pending:['warn','排队中'],
  running:['blue','运行中'],
  done:['ok','已完成'],
  failed:['err','失败'],
  killed:['err','已停止'],
  cancelled:['gray','已取消'],
};
function taskStatusTag(st){
  const [cls,lab]=TASK_ST[st]||['gray',st||'未知'];
  return `<span class="tag ${cls}">${lab}</span>`;
}
function shortPath(p,n=28){
  p=String(p||'');
  if(!p)return '—';
  return p.length<=n?p:('…'+p.slice(-(n-1)));
}
function taskDuration(t){
  const s=t.started_at;
  if(!s)return '—';
  const end=(t.status==='running')?(Date.now()/1000):(t.finished_at||s);
  return fmtDur(Math.max(0,end-s));
}
function isTodayTs(ts){
  if(!ts)return false;
  const d=new Date(ts*1000),n=new Date();
  return d.getFullYear()===n.getFullYear()&&d.getMonth()===n.getMonth()&&d.getDate()===n.getDate();
}
function isExtTask(t){return t&&(t.source==='external'||t.id<0)}
async function loadTasks(){
  try{
    const q=__ownerFilter?`?owner=${encodeURIComponent(__ownerFilter)}`:'';
    const ts=await (await fetch('/api/tasks'+q)).json();
    window.__tasks=ts;
    renderRunList();renderMyExps();
    const body=$('task-body');
    if(!ts.length){body.innerHTML='<tr><td colspan="11" class="empty">无任务 — 工作台启动或终端 GPU 进程会出现在这里</td></tr>';}
    else body.innerHTML=ts.map(t=>{
      const ext=isExtTask(t);
      const act=t.status==='running'
        ?(ext
          ?`<button class="sm" onclick="openExtLog(${t.pid})">日志</button> <button class="sm danger" onclick="stopExtProc(${t.pid},this)">停止</button>`
          :`<button class="sm" onclick="openLog(${t.id})">日志</button> <button class="sm danger" onclick="stopTask(${t.id},this)">停止</button>`)
        :t.status==='pending'?`<button class="sm danger" onclick="cancelTask(${t.id})">取消</button>`
        :(t.ai_summary?`<button class="sm" onclick="showAI(${t.id})">复盘</button> `:'')+(t.log_file?`<button class="sm" onclick="openLog(${t.id})">日志</button>`:'');
      const wd=t.workdir||'';
      const idCell=ext?`<span title="终端/SSH 进程 pid=${t.pid}">外</span>`:t.id;
      const nameCell=ext?`${esc(t.name)} <span class="tag gray" style="font-size:10px">终端</span>`:esc(t.name);
      return `<tr>
        <td class="num">${idCell}</td>
        <td>${taskStatusTag(t.status)}</td>
        <td class="num col-owner" style="font-size:11px">${esc(t.owner||'')}</td>
        <td style="font-size:12px">${nameCell}</td>
        <td class="num path-cell" title="${esc(wd)}">${esc(shortPath(wd))}</td>
        <td class="num" style="font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.command)}">${esc(t.command)}</td>
        <td class="num">${t.gpu_assigned||(t.gpus_pinned?'锁'+t.gpus_pinned:'需'+t.gpus_needed)}</td>
        <td class="num" style="font-size:11px;white-space:nowrap">${t.created_at?fmtT(t.created_at):'—'}</td>
        <td class="num" style="font-size:11px;white-space:nowrap">${t.started_at?fmtT(t.started_at):'—'}</td>
        <td class="num" style="font-size:11px;white-space:nowrap">${taskDuration(t)}</td>
        <td>${act}</td></tr>`}).join('');
    const runN=ts.filter(t=>t.status==='running').length;
    const pendN=ts.filter(t=>t.status==='pending').length;
    const extN=ts.filter(t=>isExtTask(t)&&t.status==='running').length;
    const doneToday=ts.filter(t=>t.status==='done'&&isTodayTs(t.finished_at||t.created_at)).length;
    $('task-summary').textContent=`运行中 ${runN}`+(extN?`（含终端 ${extN}）`:'')+` · 排队 ${pendN} · 今日完成 ${doneToday}`;
  }catch(e){}
}
async function submitTask(){
  const name=$('f-name').value.trim(),command=$('f-cmd').value.trim();
  const pin=$('f-pin').value.trim()||null,dir=$('f-dir').value.trim()||null;
  const chain=$('f-chain').value.trim();
  if(!name){toast('任务名必填');return}
  let r;
  if(chain){
    const cmds=chain.split('\n').map(s=>s.trim()).filter(Boolean);
    if(command)cmds.unshift(command);
    r=await fetch('/api/tasks/chain',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name_prefix:name,commands:cmds,gpus_needed:+$('f-gpus').value,workdir:dir,gpus_pinned:pin})});
  }else{
    if(!command){toast('命令必填(或填写任务链)');return}
    r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name,command,gpus_needed:+$('f-gpus').value,workdir:dir,gpus_pinned:pin})});
  }
  if(!r.ok){toast(await errText(r));return}
  $('f-name').value='';$('f-cmd').value='';$('f-chain').value='';loadTasks();
}
async function errText(r){try{const j=await r.json();return j.detail||JSON.stringify(j)}catch(e){return r.statusText}}
async function stopTask(id,btn){
  if(btn&&!btn.dataset.armed){  // 两次点击确认, 不用浏览器弹窗
    btn.dataset.armed='1';btn.textContent='确认停止';
    setTimeout(()=>{if(btn.isConnected){delete btn.dataset.armed;btn.textContent='停止'}},4000);
    return;
  }
  await fetch(`/api/tasks/${id}/stop`,{method:'POST'});
  toast('已发送停止信号','ok');
  setTimeout(loadTasks,500);
}
async function stopExtProc(pid,btn){
  if(btn&&!btn.dataset.armed){
    btn.dataset.armed='1';btn.textContent='确认停止';
    setTimeout(()=>{if(btn.isConnected){delete btn.dataset.armed;btn.textContent='停止'}},4000);
    return;
  }
  const r=await fetch(`/api/procs/${pid}/stop`,{method:'POST'});
  if(!r.ok){toast(await errText(r));return}
  toast('已发送停止信号','ok');
  setTimeout(loadTasks,800);
}
function openExtLog(pid){
  const t=(window.__tasks||[]).find(x=>isExtTask(x)&&x.pid===pid);
  $('modal-title').textContent=`终端进程 pid=${pid}`+(t?` · ${t.name}`:'');
  $('modal-log').textContent='连接中…';
  $('modal-meta').textContent='';
  $('modal').classList.add('on');
  stopLogPoll();if(logEs){logEs.close();logEs=null}
  logEs=new EventSource('/api/procs/'+pid+'/attach');
  logEs.onopen=()=>{$('modal-meta').textContent='tail -f 已连接'};
  logEs.onerror=()=>{
    if(logEs&&logEs.readyState===EventSource.CLOSED){
      $('modal-log').textContent+=('\n[日志流已断开]');
      $('modal-meta').textContent='日志流断开';
    }
  };
  logEs.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d&&typeof d==='object'&&d.__file__){$('modal-meta').textContent='tail -f '+String(d.__file__).split('/').pop();return}
    if(d&&d.__error__){$('modal-log').textContent=d.__error__;logEs.close();logEs=null;return}
    const box=$('modal-log');
    if(box.textContent==='连接中…')box.textContent='';
    const near=box.scrollHeight-box.scrollTop-box.clientHeight<80;
    box.textContent+=d+'\n';
    if(box.textContent.length>200000)box.textContent=box.textContent.slice(-100000);
    if(near)box.scrollTop=box.scrollHeight;
  };
}
async function cancelTask(id){await fetch(`/api/tasks/${id}/cancel`,{method:'POST'});loadTasks()}
async function stopSelRun(){
  const r=window.__ctx.run;if(!r)return;
  if(r.type==='live'||(r.type==='task'&&(r.source==='external'||isExtTask(r)))){
    if(!r.pid){toast('无进程可停');return}
    await fetch(`/api/procs/${r.pid}/stop`,{method:'POST'});
  }else if(r.type==='task'){
    await fetch(`/api/tasks/${r.id}/stop`,{method:'POST'});
  }
  loadTasks();
}
function showAI(id){
  const t=(window.__tasks||[]).find(x=>x.id===id);
  if(!t||!t.ai_summary){toast('复盘尚未生成');return}
  openModalText(`AI 复盘 · #${t.id} ${t.name}`,`exit=${t.exit_code}`,t.ai_summary);
}

/* ================= 曲线 ================= */
let chart=null,selFields=new Set(['task']),cmpMetric='task',lastSeries=null;
const chartColors=['#3D5AF1','#22C55E','#F5A623','#EF4444','#8B5CF6','#14B8A6','#EC4899','#64748B'];
const LOSS_FIELDS=['task','diff_noise','diff_condition_recon','diff_condition_contrast','gen_answer_i2t','generator_i2t_grad_norm'];

function focusPaperMetrics(){
  const card=window.__card||{};
  const list=((card['复现要点']||{})['对照指标'])||[];
  return list.filter(m=>m&&m['指标名']);
}
/** 对照只读这篇论文自己抽的指标/设定，不套固定协议。 */
function paperMetricBlob(m){
  if(!m)return '';
  if(typeof m==='string')return m;
  return [m['指标名'],m['设定'],m['说明'],m['论文表号'],m['数据集']].filter(Boolean).join(' ');
}
function paperMetricProtocol(m){
  if(!m)return '';
  return [m['设定'],m['数据集']].map(x=>String(x||'').replace(/\s+/g,' ').trim())
    .filter(x=>x&&x!=='未在论文中找到').join(' · ');
}
function pickScoreSeries(scores, m){
  if(!scores)return null;
  const keys=Object.keys(scores).filter(k=>scores[k]&&scores[k].values&&scores[k].values.length);
  if(!keys.length)return null;
  const blob=paperMetricBlob(m).toLowerCase();
  const tokens=(blob.match(/[a-z][a-z0-9]+/g)||[]).filter(t=>t.length>=3);
  let best=null,bestN=-1;
  for(const k of keys){
    const kl=k.toLowerCase();
    let n=0;
    if(blob&&kl.length>=2&&blob.includes(kl))n+=3;
    for(const t of tokens){if(kl.includes(t)||t.includes(kl))n++;}
    const len=scores[k].values.length;
    if(n>bestN||(n===bestN&&best&&len>best.values.length)){
      bestN=n;best=Object.assign({key:k},scores[k]);
    }
  }
  if(bestN>0)return best;
  return keys.map(k=>Object.assign({key:k},scores[k])).sort((a,b)=>b.values.length-a.values.length)[0];
}
function seriesBest(s, higher){
  if(!s)return null;
  if(higher===false)return s.min!=null?s.min:s.best;
  return s.max!=null?s.max:s.best;
}
function paperMetricLabel(m){
  if(!m)return '';
  const nm=m['指标名']||'指标';
  const tb=m['论文表号']?` · ${m['论文表号']}`:'';
  const val=(m['数值']!=null&&m['数值']!=='')?` · 论文 ${m['数值']}`:'';
  const tip=m['说明']?` — ${String(m['说明']).slice(0,40)}`:'';
  return nm+tb+val+tip;
}
async function ensureFocusCard(){
  const p=window.__ctx.paper;
  if(!p||!p.id){window.__card=null;return null}
  if(window.__card&&window.__focusCardId===p.id)return window.__card;
  try{
    const full=await (await fetch('/api/papers/'+p.id)).json();
    window.__paperFull=full;
    window.__focusCardId=p.id;
    if(full.card_json){
      try{window.__card=JSON.parse(full.card_json)}catch(e){window.__card={}}
    }else window.__card={};
    return window.__card;
  }catch(e){return window.__card||null}
}
function rebuildChartMetricSelect(keepValue){
  const sel=$('chart-metric');if(!sel)return;
  const prev=keepValue!=null?keepValue:sel.value;
  const metrics=focusPaperMetrics();
  const paper=window.__ctx.paper;
  let html='';
  if(!paper){
    html='<option value="">先在 Home「当前焦点」选择要复现的论文</option>';
  }else if(!metrics.length){
    html='<option value="">焦点论文暂无对照指标（去调研抽取「复现要点」）</option>';
  }else{
    html+=`<optgroup label="论文对照指标（${esc((paper.title||'').slice(0,18))}）">`;
    metrics.forEach((m,i)=>{
      html+=`<option value="paper:${i}">${esc(paperMetricLabel(m))}</option>`;
    });
    html+='</optgroup>';
  }
  html+=`<optgroup label="训练损失 / 中间量">`;
  const extra=((lastSeries&&lastSeries.fields)||[]).filter(f=>f&&!LOSS_FIELDS.includes(f)&&!['round','epoch','step','mode','scope'].includes(f));
  [...LOSS_FIELDS,...extra.slice(0,12)].forEach(f=>{html+=`<option value="field:${f}">${LOSS_FIELDS.includes(f)?'损失':'字段'} · ${f}</option>`});
  html+='</optgroup>';
  html+=`<optgroup label="日志评测得分">`;
  html+=`<option value="score:auto">自动（按当前论文指标选序列）</option>`;
  html+='</optgroup>';
  sel.innerHTML=html;
  if(prev&&[...sel.options].some(o=>o.value===prev))sel.value=prev;
  else if(metrics.length)sel.value='paper:0';
  else sel.value='field:task';
  applyChartMetricValue(sel.value,false);
}
function applyChartMetricValue(v,refresh){
  if(!v){cmpMetric='task';selFields=new Set(['task']);return}
  if(v.startsWith('paper:')){
    const m=focusPaperMetrics()[+v.slice(6)];
    cmpMetric='score:auto';
    selFields=new Set(['task']);
    window.__paperMetric=m||null;
  }else if(v.startsWith('field:')){
    const f=v.slice(6);
    cmpMetric=f;selFields=new Set([f]);
    window.__paperMetric=null;
  }else if(v.startsWith('score:')){
    cmpMetric=v;selFields=new Set(['task']);
    const md=v.slice(6);
    if($('chart-mode')&&md&&md!=='auto')$('chart-mode').value=md;
    window.__paperMetric=null;
  }
  if(refresh!==false){
    if(String(cmpMetric).startsWith('score:'))compareAll();
    else refreshChart();
  }
}
function onChartMetricChange(){
  applyChartMetricValue($('chart-metric').value,true);
  renderCurvePaperBar();
}
function renderCurvePaperBar(){
  const bar=$('curve-paper-bar');if(!bar)return;
  const p=window.__ctx.paper;
  if(!p){
    bar.innerHTML='<span class="sub">曲线指标跟随 Home「当前焦点」论文。请先在焦点中选择要复现的论文。</span>';
    return;
  }
  const metrics=focusPaperMetrics();
  const cur=window.__paperMetric;
  const chips=metrics.slice(0,8).map((m,i)=>{
    const on=cur&&cur['指标名']===m['指标名']&&cur['论文表号']===m['论文表号'];
    const tip=[paperMetricProtocol(m),m['说明']].filter(Boolean).join(' · ')||paperMetricLabel(m);
    return `<span class="chip ${on?'on':''}" title="${esc(tip)}" onclick="$('chart-metric').value='paper:${i}';onChartMetricChange()">${esc(m['指标名']||'')}${m['论文表号']?` <span style="opacity:.7">${esc(m['论文表号'])}</span>`:''}</span>`;
  }).join('');
  bar.innerHTML=`<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">
    <span class="sub" style="margin-right:4px">焦点 · ${esc((p.title||'').slice(0,36))}${metrics.length?` · ${metrics.length} 项对照`:''}</span>
    ${chips||'<span class="tag warn">无对照指标</span>'}
    ${cur?`<span class="sub" title="设定来自这篇论文的抽取，系统不按固定模板检查">${paperMetricProtocol(cur)?`这篇的设定：${esc(paperMetricProtocol(cur))}`:'（该条未填设定，按论文原文自行对齐）'}</span>`:''}
    <button class="sm" onclick="showPage('欢迎')">回 Home 换论文</button>
  </div>`;
}
async function syncCurveFromFocus(){
  await ensureFocusCard();
  rebuildChartMetricSelect();
  renderCurvePaperBar();
}
function drawDatasets(ds){
  if(chart)chart.destroy();
  chart=new Chart($('chart'),{type:'line',data:{datasets:ds},options:{
    animation:false,responsive:true,maintainAspectRatio:false,
    plugins:{legend:{labels:{color:'#8A9099',font:{size:10},boxWidth:12}}},
    scales:{x:{type:'linear',ticks:{color:'#8A9099',maxTicksLimit:10},grid:{color:'#EDEFF2'}},
            y:{ticks:{color:'#8A9099'},grid:{color:'#EDEFF2'}}}}});
}
function drawChart(r){
  const ds=Object.entries(r.data).filter(([f])=>selFields.has(f))
    .map(([f,vals],i)=>({label:f,data:vals,borderColor:chartColors[i%8],backgroundColor:chartColors[i%8],
      pointRadius:0,borderWidth:1.6,tension:.2}));
  // paper claimed reference (horizontal) when viewing a paper metric mapped to loss
  const pm=window.__paperMetric;
  if(pm&&pm['数值']!=null&&!String(cmpMetric).startsWith('score:')&&ds.length){
    const y=+pm['数值'];
    if(!Number.isNaN(y)){
      const xs=ds[0].data.map(pt=>typeof pt==='object'?pt.x:null).filter(x=>x!=null);
      const xmin=xs.length?Math.min(...xs):0,xmax=xs.length?Math.max(...xs):1;
      ds.push({label:`论文 ${pm['指标名']}=${pm['数值']}`,data:[{x:xmin,y},{x:xmax,y}],
        borderColor:'#F5A623',backgroundColor:'#F5A623',pointRadius:0,borderWidth:1.4,
        borderDash:[6,4],tension:0});
    }
  }
  drawDatasets(ds);
}
async function refreshChart(){
  const mode=$('chart-mode').value;
  const entries=[...__sel.values()];
  if(!entries.length){ // 未叠加曲线时回退到当前日志 run
    const run=window.__ctx.run;
    if(!run)return;
    if(run.type!=='live'){$('curve-sub').textContent='工作台任务曲线需 JSONL — 点击左侧实验叠加曲线';return}
    const r=await (await fetch(`/api/series?jsonl=${encodeURIComponent(run.jsonl)}${mode?'&mode='+mode:''}`)).json();
    lastSeries=r;drawChart(r);
    $('curve-sub').textContent=run.name.slice(0,40);
    return;
  }
  const liveEntries=entries.filter(e=>e.jsonl);
  if(!liveEntries.length){$('curve-sub').textContent='所选 run 无 JSONL 曲线';return}
  if(liveEntries.length===1){ // 单选：保持多指标视图
    const r=await (await fetch(`/api/series?jsonl=${encodeURIComponent(liveEntries[0].jsonl)}${mode?'&mode='+mode:''}`)).json();
    lastSeries=r;drawChart(r);
    $('curve-sub').textContent=liveEntries[0].name.slice(0,40);
    return;
  }
  // 多选：同一指标各 run 一条曲线
  const field=cmpMetric&&!cmpMetric.startsWith('score:')?cmpMetric:([...selFields][0]||'task');
  const ds=[];
  for(const e of liveEntries){
    try{
      const r=await (await fetch(`/api/series?jsonl=${encodeURIComponent(e.jsonl)}${mode?'&mode='+encodeURIComponent(mode):''}`)).json();
      const vals=(r.data||{})[field]||[];
      if(vals.length)ds.push({label:`${e.name.slice(0,24)} · ${field}`,data:vals,
        borderColor:e.color,backgroundColor:e.color,pointRadius:0,borderWidth:1.6,tension:.2});
    }catch(err){}
  }
  if(!ds.length){$('curve-sub').textContent='所选实验暂无 '+field+' 数据';return}
  drawDatasets(ds);
  $('curve-sub').textContent=`${ds.length} 条曲线对比 · ${field}${mode?'['+mode+']':''}`;
}
async function loadLossCard(){
  const box=$('loss-body');if(!box)return;
  const scBox=$('loss-scores');
  const run=window.__ctx.run;
  if(!run||run.type!=='live'||!run.jsonl){
    box.innerHTML='<tr><td colspan="4" class="empty">点击左侧实验显示损失记录</td></tr>';
    if(scBox){
      const metrics=focusPaperMetrics();
      scBox.innerHTML=metrics.length
        ?`<div class="sub" style="margin-bottom:6px">焦点论文对照指标（选曲线上的指标查看）</div>`+
          metrics.map(m=>`<span class="tag gray" style="margin:0 6px 6px 0;font-size:12px;padding:4px 10px">${esc(m['指标名']||'')} <b>${m['数值']??'—'}</b> <span style="opacity:.7">${esc(m['论文表号']||'')}</span></span>`).join('')
        :'<span class="sub">暂无焦点论文对照指标</span>';
    }
    return}
  try{
    const m=await (await fetch(`/api/metrics?jsonl=${encodeURIComponent(run.jsonl)}${run.log?'&log='+encodeURIComponent(run.log):''}`)).json();
    if(m.error||!m.tiers||!m.tiers.length){
      box.innerHTML=`<tr><td colspan="4" class="empty">${esc(m.error||'暂无损失记录')}</td></tr>`;
    }
    if(scBox){
      const sc=m.scores||{};
      const metrics=focusPaperMetrics();
      let tags='';
      if(metrics.length){
        tags+=metrics.map(pm=>{
          const s=pickScoreSeries(sc, pm);
          const higher=pm['越高越好']!==false;
          const best=seriesBest(s, higher);
          const bestTxt=best!=null?(+best).toFixed(4):'—';
          const claim=pm['数值']!=null?pm['数值']:'—';
          const sk=s&&s.key?s.key:'';
          return `<span class="tag blue" style="margin:0 6px 6px 0;font-size:12px;padding:4px 12px" title="${esc(paperMetricProtocol(pm)||sk)}">${esc(pm['指标名']||'')} 论文 <b>${claim}</b> · 本次 <b style="font-size:14px;margin:0 3px">${bestTxt}</b> · ${esc(pm['论文表号']||'')}</span>`;
        }).join('');
      }else{
        tags=Object.keys(sc).filter(md=>sc[md]&&sc[md].best!=null).slice(0,8).map(md=>{
          const s=sc[md];
          const rIdx=(s.values||[]).indexOf(s.best);
          const at=(rIdx>=0&&s.rounds&&s.rounds[rIdx]!=null)?` · 第 ${s.rounds[rIdx]} 轮`:'';
          return `<span class="tag blue" style="margin:0 6px 6px 0;font-size:12px;padding:4px 12px">得分[${esc(md)}] 最佳 <b style="font-size:14px;margin:0 3px">${s.best.toFixed(2)}</b>${at}</span>`;
        }).join('');
      }
      scBox.innerHTML=tags||'<span class="sub">暂无得分记录（日志中未解析到评测行）</span>';
    }
    if(m.tiers&&m.tiers.length){
      const fN=v=>v==null?'—':(+v).toPrecision(4);
      box.innerHTML=m.tiers.map(t=>
        `<tr><td colspan="4" style="font:600 11px var(--sans);background:var(--soft);color:var(--dim)">${esc(t.name)}</td></tr>`
        +t.rows.map(r=>`<tr>
          <td class="num" style="font-size:11px">${esc(r.field)}<span style="color:var(--faint)">[${esc(r.mode)}]</span></td>
          <td class="num">${fN(r.first)}</td>
          <td class="num" style="font-weight:600">${fN(r.latest)}</td>
          <td class="num" style="color:${r.prev!=null&&r.latest<r.first?'var(--green)':'var(--dim)'}">${r.prev!=null?((r.latest-r.prev>=0?'+':'')+(r.latest-r.prev).toPrecision(3)):'—'}</td></tr>`).join('')).join('');
    }
    if(m.round!=null)$('loss-round').textContent=`round ${m.round} · ${m.records||0} 条记录${m.nan_count?' · 近期 NaN×'+m.nan_count:''}`;
  }catch(e){}
}
function archPick(field,mode){
  selFields=new Set([field]);cmpMetric=field;
  if(mode!==null&&mode!==undefined)$('chart-mode').value=mode;
  window.__paperMetric=null;
  const sel=$('chart-metric');
  if(sel){const v='field:'+field;if([...sel.options].some(o=>o.value===v))sel.value=v}
  renderCurvePaperBar();
  refreshChart();
}
async function compareAll(){
  const ds=[];
  const pm=window.__paperMetric;
  if(String(cmpMetric).startsWith('score:')){
    const md=cmpMetric.slice(6);
    const src=__sel.size
      ?[...__sel.values()].filter(e=>e.jsonl||e.log)
      :(window.__ctx.run&&(window.__ctx.run.jsonl||window.__ctx.run.log)
        ?[window.__ctx.run]
        :liveExps.map(l=>({name:l.name,jsonl:l.jsonl,log:l.log,color:null})));
    let i=0;
    for(const e of src){
      try{
        const q=`/api/metrics?jsonl=${encodeURIComponent(e.jsonl||'')}${e.log?'&log='+encodeURIComponent(e.log):''}`;
        const m=await (await fetch(q)).json();
        const s=(md&&md!=='auto'&&m.scores&&m.scores[md]&&m.scores[md].values&&m.scores[md].values.length)
          ?Object.assign({key:md},m.scores[md])
          :pickScoreSeries(m.scores||{}, pm);
        if(s&&s.values&&s.values.length){
          const label=pm?`${e.name.slice(0,20)} · ${pm['指标名']}`:`${e.name.slice(0,26)} · ${s.key||md}`;
          ds.push({label,data:s.values.map((v,j)=>({x:s.rounds[j],y:v})),
            borderColor:e.color||chartColors[i%8],backgroundColor:e.color||chartColors[i%8],pointRadius:3,borderWidth:2,tension:.25});
          i++;
        }
      }catch(err){}
    }
    if(pm&&pm['数值']!=null&&ds.length){
      const y=+pm['数值'];
      if(!Number.isNaN(y)){
        const xs=ds.flatMap(d=>d.data.map(p=>p.x));
        const xmin=Math.min(...xs),xmax=Math.max(...xs);
        ds.push({label:`论文 ${pm['指标名']}=${pm['数值']}${pm['论文表号']?' ('+pm['论文表号']+')':''}`,
          data:[{x:xmin,y},{x:xmax,y}],borderColor:'#F5A623',backgroundColor:'#F5A623',
          pointRadius:0,borderWidth:1.6,borderDash:[6,4],tension:0});
      }
    }
  }else{
    const field=cmpMetric||[...selFields][0]||'task';
    const mode=$('chart-mode').value||'';
    const src=__sel.size?[...__sel.values()].filter(e=>e.jsonl):liveExps.map(l=>({name:l.name,jsonl:l.jsonl,color:null}));
    let i=0;
    for(const e of src){
      try{
        const r=await (await fetch(`/api/series?jsonl=${encodeURIComponent(e.jsonl)}${mode?'&mode='+mode:''}`)).json();
        const vals=(r.data||{})[field]||[];
        if(vals.length)ds.push({label:`${e.name.slice(0,26)} · ${field}`,data:vals,
          borderColor:e.color||chartColors[i%8],backgroundColor:e.color||chartColors[i%8],pointRadius:0,borderWidth:1.6,tension:.2});
        i++;
      }catch(err){}
    }
  }
  if(!ds.length){
    const proto=pm?paperMetricProtocol(pm):'';
    let hint='当前日志里还没有扫到可画的评测得分行。请点左侧带日志的 run。';
    if(pm){
      hint=`暂无日志得分可对照「${pm['指标名']||'该指标'}」`+(pm['数值']!=null?` · 论文 ${pm['数值']}`:'')+(pm['论文表号']?` · ${pm['论文表号']}`:'')+'。请点左侧带日志的 run。'
        +(proto?` 这篇的设定（供你自己对齐）：${proto}`:' 设定以该论文抽取为准，系统不按固定模板检查。');
    }
    $('curve-sub').textContent=hint;
    toast(hint);
    return;
  }
  drawDatasets(ds);
  const pmHint=pm?` · 对照 ${pm['指标名']}=${pm['数值']}`:'';
  $('curve-sub').textContent=`${ds.filter(d=>!(d.borderDash)).length} 条曲线${pmHint}`;
}
async function pickScore(){
  // compat: pick first paper metric or vl score
  const metrics=focusPaperMetrics();
  if(metrics.length){
    const sel=$('chart-metric');
    if(sel){sel.value='paper:0';onChartMetricChange();return}
  }
  cmpMetric='score:auto';compareAll();
}
setInterval(()=>{if(curPage==='日志')refreshChart()},15000);

/* ================= run 日志 (interval 轮询, 复用既有节奏) ================= */
function stopLogPoll(){if(logTimer){clearInterval(logTimer);logTimer=null}}
function startRunLog(){
  stopLogPoll();if(logEs){logEs.close();logEs=null}
  const run=window.__ctx.run;
  const box=$('log-box');box.textContent='连接中…';
  if(!run){$('log-meta').textContent='选择 run 查看';return}
  $('log-meta').textContent=run.name.slice(0,50);
  $('log-stop').style.display=(run.type==='task'||run.type==='live')&&(
    run.type==='live'||run.source==='external'||((window.__tasks||[]).find(t=>t.id===run.id)||{}).status==='running'
  )?'':'none';
  if(run.type==='live'||(run.type==='task'&&run.source==='external'&&run.pid)){
    const attachUrl=run.type==='live'
      ?(run.log?('/api/logs/attach?path='+encodeURIComponent(run.log)):null)
      :('/api/procs/'+run.pid+'/attach');
    if(!attachUrl){box.textContent='该实验未找到文本日志(可用监视行「问AI」分析)';return}
    logEs=new EventSource(attachUrl);
    logEs.onopen=()=>{$('log-meta').textContent='tail -f 已连接';};
    logEs.onerror=()=>{
      if(logEs&&logEs.readyState===EventSource.CLOSED){
        box.textContent+='\\n[日志流已断开 — 点击左侧实验重连]';
        $('log-meta').textContent='日志流断开';
      }
    };
    logEs.onmessage=e=>{
      const d=JSON.parse(e.data);
      if(d&&typeof d==='object'&&d.__file__){$('log-meta').textContent='tail -f '+d.__file__.split('/').pop();return}
      if(d&&d.__error__){box.textContent=d.__error__;logEs.close();logEs=null;return}
      const near=box.scrollHeight-box.scrollTop-box.clientHeight<60;
      box.textContent+=d+'\n';
      if(box.textContent.length>200000)box.textContent=box.textContent.slice(-100000);
      if(near)box.scrollTop=box.scrollHeight;
    };
  }else{
    curFetcher=async()=>{
      const r=await (await fetch(`/api/tasks/${run.id}/log?lines=200`)).json();
      const t=(window.__tasks||[]).find(x=>x.id===run.id);
      return {lines:r.lines||[],done:t&&!['running','pending'].includes(t.status)};
    };
    const iv=monIvSec()*1000;
    const poll=async()=>{
      try{
        const r=await curFetcher();
        box.textContent=(r.lines||[]).join('\n');box.scrollTop=box.scrollHeight;
        $('log-meta').textContent=`每${fmtIv(iv/1000)}更新 · ${new Date().toLocaleTimeString()}${r.done?' · 已结束，停止刷新':''}`;
        if(r.done)stopLogPoll();
      }catch(e){}
    };
    poll();logTimer=setInterval(poll,iv);
  }
}

/* ================= 弹窗日志(工作台任务) ================= */
function openLog(id){
  $('modal-title').textContent=`任务 #${id} 日志`;
  $('modal-log').textContent='';
  $('modal-meta').textContent='';
  $('modal').classList.add('on');
  stopLogPoll();if(logEs){logEs.close();logEs=null}
  curFetcher=async()=>{
    const r=await (await fetch(`/api/tasks/${id}/log?lines=200`)).json();
    const t=(window.__tasks||[]).find(x=>x.id===id);
    return {lines:r.lines||[],done:t&&!['running','pending'].includes(t.status)};
  };
  const iv=monIvSec()*1000;
  const poll=async()=>{
    try{
      const r=await curFetcher();
      $('modal-log').textContent=(r.lines||[]).join('\n');
      $('modal-log').scrollTop=$('modal-log').scrollHeight;
      $('modal-meta').textContent=`每${fmtIv(iv/1000)}更新 · ${new Date().toLocaleTimeString()}${r.done?' · 已结束':''}`;
      if(r.done)stopLogPoll();
    }catch(e){}
  };
  poll();logTimer=setInterval(poll,iv);
}
function closeLog(){$('modal').classList.remove('on');stopLogPoll();if(logEs){logEs.close();logEs=null}}
function openModalText(title,meta,content,asHtml){
  $('modal-title').textContent=title;$('modal-meta').textContent=meta||'';
  if(asHtml)$('modal-log').innerHTML=content||'';
  else $('modal-log').textContent=content==null?'':String(content);
  $('modal').classList.add('on');
  stopLogPoll();if(logEs){logEs.close();logEs=null}
}

/* ================= 论文 ================= */
let papers=[];
let batchQueue=[];   // [{file,name,status,err,paperId,title}]
let batchRunning=false;
let batchCancel=false;

const EXTRACT_SECTIONS=[
  '文献信息','代码与资源','实验架构','参数设计','数据集','结论与局限','复现要点'
];
const EXTRACT_PRESETS={
  fast:['文献信息','结论与局限','复现要点'],
  std:['文献信息','代码与资源','实验架构','结论与局限','复现要点'],
  full:EXTRACT_SECTIONS.slice(),
};
function loadExtractSections(){
  try{
    const s=JSON.parse(localStorage.getItem('wb_extract_sections')||'null');
    if(Array.isArray(s)&&s.length)return EXTRACT_SECTIONS.filter(x=>s.includes(x));
  }catch(e){}
  return EXTRACT_SECTIONS.slice();
}
function saveExtractSections(secs){
  localStorage.setItem('wb_extract_sections',JSON.stringify(secs));
}
function selectedExtractSections(){
  const boxes=[...document.querySelectorAll('#extract-secs input[type=checkbox]')];
  if(!boxes.length)return loadExtractSections();
  let secs=boxes.filter(b=>b.checked).map(b=>b.value);
  if(!secs.length){toast('至少选一个抽取板块');secs=['文献信息']}
  saveExtractSections(secs);
  return secs;
}
function renderExtractSettings(){
  const box=$('extract-secs');if(!box)return;
  const cur=new Set(loadExtractSections());
  box.innerHTML=EXTRACT_SECTIONS.map(s=>
    `<label><input type="checkbox" value="${s}" ${cur.has(s)?'checked':''} onchange="onExtractSecChange()"> ${s}</label>`
  ).join('');
  updateExtractHint();
}
function onExtractSecChange(){
  const secs=selectedExtractSections();
  updateExtractHint(secs);
}
function updateExtractHint(secs){
  secs=secs||loadExtractSections();
  const el=$('extract-hint');if(!el)return;
  const n=secs.length;
  el.textContent=n<=2
    ?`快速 · ${n} 块逐块抽取（每块约 10–40 秒）`
    :(n<=4?`标准 · ${n} 块逐块抽取，完成一块先显示一块`
    :`完整 · ${n} 块逐块抽取，完成一块先显示一块（总时长更长）`);
}

const SEC_SHORT={
  '文献信息':'文','代码与资源':'码','实验架构':'架','参数设计':'参',
  '数据集':'数','结论与局限':'结','复现要点':'复'
};
let __extractState={}; // section -> wait|run|done|err
let __extractErrs={};  // section -> message
let __extractCancel=false;

function initExtractState(secs){
  __extractState={};__extractErrs={};__extractCancel=false;
  (secs||[]).forEach(s=>{__extractState[s]='wait'});
}
function renderExtractProgress(){
  const box=$('extract-progress');if(!box)return;
  const secs=Object.keys(__extractState);
  if(!secs.length){box.style.display='none';box.innerHTML='';return}
  box.style.display='flex';
  box.innerHTML=secs.map(s=>{
    const st=__extractState[s]||'wait';
    const err=__extractErrs[s]||'';
    const tip=err||({wait:'待抽',run:'抽取中',done:'完成',err:'失败'}[st]||st);
    const retry=st==='err'
      ?` onclick="retryExtractSection('${s}')" style="cursor:pointer"`
      :'';
    return `<span class="ex-chip ${st}" title="${esc(tip)}"${retry}>${esc(SEC_SHORT[s]||s.slice(0,2))} · ${esc(s)}`
      +(st==='run'?' …':st==='done'?' ✓':st==='err'?' ✗ 点此重试':'')
      +`</span>`;
  }).join('');
}
function extractProgressSummary(secs){
  const done=secs.filter(s=>__extractState[s]==='done').length;
  const fail=secs.filter(s=>__extractState[s]==='err').length;
  const run=secs.find(s=>__extractState[s]==='run');
  if(run)return `正在抽取：${run}（${done+1}/${secs.length}）· 每块约 10–40 秒`;
  if(done+fail>=secs.length){
    if(!fail)return `抽取完成 ${done}/${secs.length}`;
    if(!done)return `全部失败 ${fail}/${secs.length}`;
    const first=secs.find(s=>__extractState[s]==='err');
    return `完成 ${done}/${secs.length}，失败 ${fail}（如 ${first}：${(__extractErrs[first]||'').slice(0,60)}）`;
  }
  return `待抽取 ${secs.length} 块`;
}
async function extractOneSection(paperId, sec){
  if(isBrowserAi()){
    const prepR=await fetch(`/api/papers/${paperId}/extract/prepare`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sections:[sec]})});
    if(!prepR.ok)throw new Error(await errText(prepR));
    const prep=await prepR.json();
    let messages=[...(prep.messages||[])];
    let content='', lastErr=null;
    for(let attempt=0;attempt<2;attempt++){
      const j=await browserChatCompletions(messages,{temperature:prep.temperature??0.1});
      content=((j.choices||[])[0]||{}).message?.content||'';
      try{
        const partial=parseExtractJson(content, prep.sections||[sec]);
        const applyR=await fetch(`/api/papers/${paperId}/extract/apply`,{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({card:partial,sections:prep.sections||[sec]})});
        if(!applyR.ok)throw new Error(await errText(applyR));
        return await applyR.json();
      }catch(e){
        lastErr=e;
        messages=messages.concat([
          {role:'assistant',content},
          {role:'user',content:`上面的输出不是合法 JSON（错误：${e.message||e}）。请重新输出**仅一个**合法 JSON 对象，不要任何其他文字。`}
        ]);
      }
    }
    throw lastErr||new Error('抽取结果不是合法 JSON');
  }
  const r=await fetch(`/api/papers/${paperId}/extract`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sections:[sec]})});
  if(!r.ok)throw new Error(await errText(r));
  return await r.json();
}
/** Sequential per-section extract. Continues after per-section errors unless cancel(). */
async function extractSectionsSequential(paperId, sections, hooks={}){
  const secs=(sections&&sections.length)?sections:['文献信息'];
  initExtractState(secs);
  renderExtractProgress();
  let ok=0,fail=0,lastCard=null;
  for(let i=0;i<secs.length;i++){
    if(__extractCancel||(hooks.shouldCancel&&hooks.shouldCancel()))break;
    const sec=secs[i];
    __extractState[sec]='run';
    renderExtractProgress();
    if(hooks.onStart)hooks.onStart(sec,i,secs);
    try{
      const exj=await extractOneSection(paperId, sec);
      const card=exj.card||exj;
      lastCard=card;
      __extractState[sec]='done';
      delete __extractErrs[sec];
      ok++;
      if(hooks.onDone)hooks.onDone(sec,card,exj,i,secs);
    }catch(e){
      const msg=String(e.message||e).slice(0,240);
      __extractState[sec]='err';
      __extractErrs[sec]=msg;
      fail++;
      if(hooks.onErr)hooks.onErr(sec,msg,i,secs);
    }
    renderExtractProgress();
  }
  return {ok,fail,card:lastCard,cancelled:!!__extractCancel,states:{...__extractState},errs:{...__extractErrs}};
}
async function retryExtractSection(sec){
  const p=window.__ctx.paper;if(!p||!p.id)return;
  if(!EXTRACT_SECTIONS.includes(sec))return;
  __extractState[sec]=__extractState[sec]||'wait';
  __extractState[sec]='run';
  delete __extractErrs[sec];
  renderExtractProgress();
  if($('paper-status'))$('paper-status').textContent=`重试抽取：${sec}…`;
  try{
    const exj=await extractOneSection(p.id, sec);
    const card=exj.card||exj;
    __extractState[sec]='done';
    window.__card=card;
    if(exj.ccf)window.__paperFull=Object.assign(window.__paperFull||{},{ccf:exj.ccf});
    const t=(card['文献信息']||{})['标题'];
    if(t&&t!==MISS){
      window.__ctx.paper.title=String(t).slice(0,80);saveCtx();
      if($('paper-title'))$('paper-title').textContent=window.__ctx.paper.title;
    }
    renderPaperMeta();
    renderCard(card);
    if($('paper-status'))$('paper-status').textContent=`已重抽完成：${sec}`;
    toast(sec+' 重抽完成','ok');
  }catch(e){
    __extractState[sec]='err';
    __extractErrs[sec]=String(e.message||e).slice(0,240);
    if($('paper-status'))$('paper-status').textContent=`重抽失败：${sec} — ${__extractErrs[sec]}`;
    toast('重抽失败: '+__extractErrs[sec],'err');
  }
  renderExtractProgress();
}
function sectionStatusLine(states,errs){
  return EXTRACT_SECTIONS.filter(s=>states&&states[s]).map(s=>{
    const st=states[s];
    const mark=st==='done'?'✓':(st==='err'?'✗':(st==='run'?'…':'·'));
    return (SEC_SHORT[s]||'?')+mark;
  }).join(' ');
}
function setExtractPreset(name){
  const secs=EXTRACT_PRESETS[name]||EXTRACT_PRESETS.full;
  saveExtractSections(secs);
  renderExtractSettings();
}
function extractBody(){
  return JSON.stringify({sections:selectedExtractSections()});
}

function focusArxiv(){$('e-arxiv').focus();uzOn('uz-arxiv');clearPdf()}
function focusText(){$('e-text').focus();uzOn('uz-text');clearPdf()}
function uzOn(id){document.querySelectorAll('.uz').forEach(b=>b.classList.remove('on'));$(id).classList.add('on')}
function setPdfMeta(j){
  const el=$('paper-pdf-meta'); if(!el)return;
  if(!j || typeof j.pdf_tables!=='number'){el.style.display='none';return}
  const n=j.pdf_tables|0;
  const ocr=j.ocr_used?' · 含OCR':'';
  el.style.display='block';
  el.style.color=n>0?'var(--green)':'var(--amber)';
  el.textContent=n>0
    ?`PDF 表格抽取：${n} 张${ocr} · ${j.raw_chars||''} 字（对照指标应优先用表内绝对数值）`
    :`PDF 表格抽取：0 张${ocr} · 未读到格子数字（表可能是图且 OCR 失败）`;
}
async function reparsePaperPdf(inp){
  const p=window.__ctx&&window.__ctx.paper;
  if(!p||!p.id){toast('请先打开一篇论文','err');if(inp)inp.value='';return}
  const f=inp&&inp.files&&inp.files[0];
  if(!f){toast('请选择 PDF');return}
  if($('paper-status'))$('paper-status').textContent='正在重抽 PDF（含表格 OCR，可能需 30–90 秒）…';
  try{
    const fd=new FormData();fd.append('file',f);
    const r=await fetch(`/api/papers/${p.id}/reparse-pdf`,{method:'POST',body:fd});
    if(!r.ok)throw new Error(await errText(r));
    const j=await r.json();
    setPdfMeta(j);
    const msg=`PDF 文本已刷新 · 表格 ${j.pdf_tables ?? 0} 张`
      +(j.ocr_used?' · OCR':'')
      +` · ${j.raw_chars||0} 字`;
    if($('paper-status'))$('paper-status').textContent=msg;
    toast(msg, j.pdf_tables>0?'ok':'err');
  }catch(e){
    if($('paper-status'))$('paper-status').textContent='刷新 PDF 失败: '+(e.message||e);
    toast('刷新 PDF 失败: '+(e.message||e),'err');
  }finally{
    if(inp)inp.value='';
  }
}
function uploadPdf(inp){
  const files=[...(inp.files||[])].filter(f=>/\.pdf$/i.test(f.name)||f.type==='application/pdf');
  if(!files.length)return;
  uzOn('uz-pdf');
  const label=files.length===1
    ?('已选 '+files[0].name.slice(0,26)+(files[0].name.length>26?'…':''))
    :('已选 '+files.length+' 篇 PDF');
  $('uz-pdf').lastChild.textContent=label;
  // preview queue (waiting) before start
  batchQueue=files.map(f=>({file:f,name:f.name,status:'wait',err:'',paperId:null,title:'',sections:{},secErrs:{}}));
  batchCancel=false;
  renderBatchPanel();
}
function clearPdf(){
  $('pdf-file').value='';
  $('uz-pdf').lastChild.textContent='上传 PDF（可多选）';
  if(!batchRunning){batchQueue=[];renderBatchPanel()}
}
async function loadPapers(){
  try{papers=await (await fetch('/api/papers')).json()}catch(e){papers=[]}
}
function inputMode(){
  if($('pdf-file').files.length)return 'pdf';
  if($('e-arxiv').value.trim())return 'arxiv';
  if($('e-text').value.trim().length>=50)return 'text';
  return null;
}
function renderBatchPanel(){
  const panel=$('batch-panel');
  if(!panel)return;
  if(!batchQueue.length){panel.style.display='none';return}
  panel.style.display='block';
  const finished=batchQueue.filter(x=>['done','partial','err'].includes(x.status)).length;
  const run=batchQueue.find(x=>x.status==='run');
  const total=batchQueue.length;
  const pct=total?Math.round(finished/total*100):0;
  $('batch-bar').style.width=pct+'%';
  $('batch-title').textContent=batchRunning?'批量调研进行中':'批量调研队列';
  $('batch-sub').textContent=run
    ?(`第 ${finished+1}/${total} 篇 · ${run.title||run.name} · ${sectionStatusLine(run.sections,run.secErrs)||'上传中'}`)
    :(batchRunning?`${finished}/${total} 完成`:`待开始 ${total} 篇 · 将逐篇上传并按模块抽取`);
  $('btn-batch-cancel').style.display=batchRunning?'inline-flex':'none';
  $('batch-list').innerHTML=batchQueue.map((x,i)=>{
    const stLabel={wait:'排队中',run:'抽取中…',done:'完成',partial:'部分完成',err:'失败'}[x.status]||x.status;
    const secLine=sectionStatusLine(x.sections,x.secErrs);
    const errBit=x.err?`<div class="batch-err">${esc(x.err.slice(0,160))}</div>`:'';
    const tip=[x.title,secLine,x.err].filter(Boolean).join(' | ');
    return `<div class="batch-item ${x.status}" title="${esc(tip)}">
      <div class="batch-main"><span class="nm">${i+1}. ${esc(x.title||x.name)}</span><span class="st">${stLabel}</span></div>
      ${secLine?`<div class="batch-secs">${esc(secLine)}</div>`:''}
      ${errBit}
    </div>`;
  }).join('');
}
function cancelBatchQueue(){
  batchCancel=true;
  __extractCancel=true;
  toast('将在当前模块结束后停止剩余队列');
}
async function startResearch(){
  const mode=inputMode();
  if(!mode){toast('请上传 PDF、填写 arXiv 链接或粘贴至少 50 字文本');return}
  if(mode==='pdf'&&($('pdf-file').files||[]).length>=1){
    await startBatchQueue();
    return;
  }
  const btn=$('btn-research');const old=btn.textContent;
  btn.textContent='上传解析中…';btn.disabled=true;
  try{
    let r;
    if(mode==='pdf'){
      const fd=new FormData();fd.append('file',$('pdf-file').files[0]);
      if($('e-arxiv').value.trim())fd.append('title',$('e-arxiv').value.trim().slice(0,80));
      r=await fetch('/api/papers/pdf',{method:'POST',body:fd});
    }else{
      r=await fetch('/api/papers',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(mode==='arxiv'?{arxiv_url:$('e-arxiv').value.trim()}:{text:$('e-text').value.trim()})});
    }
    if(!r.ok){toast(await errText(r));return}
    const p=await r.json();
    window.__ctx.paper={id:p.id,title:p.title};
    window.__ctx.run=null;saveCtx();
    pushRecent('paper',p.title);
    if(typeof p.pdf_tables==='number'){
      toast(`上传完成 · 表格 ${p.pdf_tables} 张`, p.pdf_tables>0?'ok':'err');
    }
    showPage('调研');
    await extractPaper(true);
  }catch(e){
    toast('请求失败（网络或服务不可达）: '+e+'\n若是刚刚断过链路，请稍候重试');
  }finally{
    btn.textContent=old;btn.disabled=false;
  }
}
async function startBatchQueue(){
  if(batchRunning){toast('队列正在跑，请稍候');return}
  const files=[...($('pdf-file').files||[])];
  if(!files.length&&!batchQueue.length){toast('请先选择 PDF');return}
  if(files.length){
    batchQueue=files.map(f=>({file:f,name:f.name,status:'wait',err:'',paperId:null,title:'',sections:{},secErrs:{}}));
  }
  const sections=selectedExtractSections();
  batchRunning=true;batchCancel=false;__extractCancel=false;
  const btn=$('btn-research');const old=btn.textContent;
  btn.disabled=true;btn.textContent='队列处理中…';
  renderBatchPanel();
  showPage('欢迎');
  let firstDone=null,ok=0,fail=0,partial=0;
  try{
    for(const item of batchQueue){
      if(batchCancel){if(item.status==='wait')item.status='wait';continue}
      item.status='run';item.err='';item.sections={};item.secErrs={};
      renderBatchPanel();
      try{
        const fd=new FormData();fd.append('file',item.file);
        const up=await fetch('/api/papers/pdf',{method:'POST',body:fd});
        if(!up.ok)throw new Error(await errText(up));
        const p=await up.json();
        item.paperId=p.id;item.title=p.title||item.name;
        const nTab=p.pdf_tables|0;
        item.title=`${item.title} · 表${nTab}`;
        toast(`${item.name}: PDF 解析完成 · 表格 ${nTab} 张`+(nTab? (p.ocr_used?' · OCR':''):'（未抽到格子）'), nTab?'ok':'err');
        if(window.__ctx.paper&&window.__ctx.paper.id===p.id){
          setPdfMeta(p);
          if($('paper-status'))
            $('paper-status').textContent=`上传完成 · 表格 ${nTab} 张 · ${p.raw_chars||''} 字`;
        }
        const focusThis=!firstDone;
        if(focusThis){
          firstDone={id:p.id,title:item.title};
          window.__ctx.paper=firstDone;window.__ctx.run=null;saveCtx();
          showPage('调研');
        }
        const result=await extractSectionsSequential(p.id,sections,{
          shouldCancel:()=>batchCancel,
          onStart:(sec)=>{
            item.sections[sec]='run';
            renderBatchPanel();
            if($('paper-status')&&window.__ctx.paper&&window.__ctx.paper.id===p.id)
              $('paper-status').textContent=extractProgressSummary(sections);
          },
          onDone:(sec,card,exj)=>{
            item.sections[sec]='done';
            if(sec==='文献信息'){
              const t=(card['文献信息']||{})['标题'];
              if(t&&t!==MISS){
                item.title=String(t).slice(0,120);
                if(window.__ctx.paper&&window.__ctx.paper.id===p.id){
                  window.__ctx.paper.title=item.title;saveCtx();
                  if($('paper-title'))$('paper-title').textContent=item.title;
                }
              }
            }
            if(window.__ctx.paper&&window.__ctx.paper.id===p.id){
              window.__card=card;
              if(exj.ccf)window.__paperFull=Object.assign(window.__paperFull||{},{ccf:exj.ccf,saved:0});
              renderPaperMeta();
              renderCard(card);
              if($('paper-status'))$('paper-status').textContent=extractProgressSummary(sections);
            }
            renderBatchPanel();
          },
          onErr:(sec,msg)=>{
            item.sections[sec]='err';
            item.secErrs[sec]=msg;
            item.err=Object.entries(item.secErrs).map(([k,v])=>k+': '+v).join('；').slice(0,300);
            renderBatchPanel();
            if($('paper-status')&&window.__ctx.paper&&window.__ctx.paper.id===p.id)
              $('paper-status').textContent=extractProgressSummary(sections);
          }
        });
        item.sections=result.states;
        item.secErrs=result.errs;
        if(result.ok===0){
          item.status='err';
          item.err=item.err||'全部模块抽取失败';
          fail++;
        }else if(result.fail>0){
          item.status='partial';
          partial++;
          pushRecent('paper',item.title);
        }else{
          item.status='done';
          ok++;
          pushRecent('paper',item.title);
        }
        if(firstDone&&firstDone.id===p.id)firstDone.title=item.title;
      }catch(e){
        item.status='err';item.err=String(e.message||e).slice(0,200);fail++;
      }
      renderBatchPanel();
    }
  }finally{
    batchRunning=false;
    btn.disabled=false;btn.textContent=old;
    $('pdf-file').value='';
    $('uz-pdf').lastChild.textContent='上传 PDF（可多选）';
    renderBatchPanel();
  }
  if(firstDone){
    window.__ctx.paper=firstDone;saveCtx();
    showPage('调研');
    await extractPaper(); // cached card — instant refresh
  }
  const msg=batchCancel
    ?`已停止。成功 ${ok}，部分 ${partial}，失败 ${fail}`
    :`批量完成：成功 ${ok}，部分 ${partial}，失败 ${fail}`;
  toast(msg, fail&&!ok?undefined:'ok');
}
async function extractPaper(force){
  const p=window.__ctx.paper;if(!p)return;
  window.__infoZh=null;
  if(!force){
    const full=await (await fetch(`/api/papers/${p.id}`)).json();
    window.__paperFull={ccf:full.ccf,saved:full.saved,venue:full.venue||''};
    setPdfMeta(full);
    if(!window.__paperFull.venue){
      try{
        const info=(typeof full.card_json==='string'?JSON.parse(full.card_json):full.card_json)||{};
        window.__paperFull.venue=((info['文献信息']||{})['期刊或会议'])||'';
      }catch(e){}
    }
    renderPaperMeta();
    if(full.card_json){
      if(full.info_zh){try{window.__infoZh=JSON.parse(full.info_zh)}catch(e){}}
      const card=JSON.parse(full.card_json);
      renderCard(card);
      // show which sections already present
      const present=EXTRACT_SECTIONS.filter(s=>card[s]!=null);
      initExtractState(present);
      present.forEach(s=>{__extractState[s]='done'});
      renderExtractProgress();
      if($('paper-status'))$('paper-status').textContent=present.length
        ?`已有调研卡 · ${present.length} 个板块（点「重新解析」可按模块重抽）`
        :'';
      return;
    }
  }
  const secs=selectedExtractSections();
  initExtractState(secs);
  renderExtractProgress();
  if($('paper-status'))$('paper-status').textContent=extractProgressSummary(secs);
  const result=await extractSectionsSequential(p.id,secs,{
    onStart:()=>{if($('paper-status'))$('paper-status').textContent=extractProgressSummary(secs)},
    onDone:(sec,card,exj)=>{
      window.__card=card;
      if(exj.ccf)window.__paperFull=Object.assign(window.__paperFull||{},{ccf:exj.ccf});
      const t=(card['文献信息']||{})['标题'];
      if(t&&t!==MISS){
        window.__ctx.paper.title=String(t).slice(0,80);saveCtx();
        if($('paper-title'))$('paper-title').textContent=window.__ctx.paper.title;
      }
      renderPaperMeta();
      renderCard(card);
      if($('paper-status'))$('paper-status').textContent=extractProgressSummary(secs);
    },
    onErr:()=>{if($('paper-status'))$('paper-status').textContent=extractProgressSummary(secs)}
  });
  if($('paper-status'))$('paper-status').textContent=extractProgressSummary(secs)
    +(result.ok?` · ${new Date().toLocaleTimeString()}`:'');
  if(result.ok&&result.fail)toast(`部分完成：成功 ${result.ok}，失败 ${result.fail}`);
  else if(!result.ok)toast('抽取失败：全部模块未成功','err');
  else toast('抽取完成','ok');
}
const MISS='未在论文中找到';
const vOr=x=>(x==null||x===''||x===MISS)?`<span class="missing">${MISS}</span>`:esc(x);
function renderPaper(){
  const p=window.__ctx.paper;
  if(!p){$('paper-empty').style.display='block';$('paper-wrap').style.display='none';return}
  $('paper-empty').style.display='none';$('paper-wrap').style.display='block';
  $('paper-title').textContent=p.title||'论文';
  renderPaperMeta();
  extractPaper();
}
window.__ccfMap={};
function ccfBadge(ccf,key){
  if(!ccf||!ccf.r)return '';
  const k=key||('c'+Date.now()+Math.random());
  window.__ccfMap[k]=ccf;
  const cls=ccf.r==='A'?'blue':(ccf.r==='B'?'ok':'gray');
  return ` <span class="tag ${cls}" style="cursor:pointer" title="点击查看 CCF 目录详情" onclick="showCcf('${k}',event)">CCF-${esc(ccf.r)}</span>`;
}
function arxivBadge(venue){
  const v=String(venue||'').trim();
  if(!v)return '';
  if(/^(arxiv|biorxiv|medrxiv|preprint)/i.test(v) || /arxiv\.org/i.test(v)){
    return ` <span class="tag gray" title="预印本：尚未（或不算）正式期刊/会议发表">arXiv 预印本</span>`;
  }
  return '';
}
/** CCF badge if matched; otherwise arXiv preprint badge when venue is arXiv. */
function paperBadge(ccf,venue,key){
  return ccfBadge(ccf,key)||arxivBadge(venue);
}
function showCcf(k,ev){
  if(ev)ev.stopPropagation();
  const c=window.__ccfMap[k];if(!c)return;
  const row=(l,v)=>`<div style="display:flex;gap:12px;padding:7px 0;border-bottom:1px solid var(--soft-br)">
    <span style="width:64px;flex-shrink:0;color:var(--dim);font-size:12px">${l}</span>
    <span style="font-size:13px;min-width:0;word-break:break-word">${v}</span></div>`;
  const cls=c.r==='A'?'blue':(c.r==='B'?'ok':'gray');
  openModalText('CCF 推荐目录详情','中国计算机学会 (CCF) 2026 年第七版',null);
  $('modal-log').innerHTML=
    `<div style="padding:4px 2px">
      ${row('等级',`<span class="tag ${cls}" style="font-size:14px;padding:4px 14px">CCF-${esc(c.r)} 类</span>`)}
      ${row('类型',esc(c.t||'—'))}
      ${row('简称',`<b>${esc(c.s||'—')}</b>`)}
      ${row('全称',esc(c.f||'—'))}
      ${row('领域',esc(c.a||'—'))}
      ${row('出版社',esc(c.p||'—'))}
      ${row('网址',c.u?`<a href="${esc(c.u)}" target="_blank" style="word-break:break-all">${esc(c.u)}</a>`:'—')}
      <div style="color:var(--faint);font-size:11px;margin-top:10px">数据来源：CCF 推荐国际学术会议和期刊目录（2026 年 · 第七版）</div>
    </div>`;
}
/* ================= CCF 目录浏览器 ================= */
let __ccfAll=null,__ccfShown=120;
async function openCcfBrowser(){
  if(!__ccfAll){
    try{__ccfAll=await (await fetch('/static/ccf.json')).json()}
    catch(e){toast('目录加载失败','err');return}
  }
  __ccfShown=120;
  openModalText('CCF 推荐目录 · 2026 第七版',`${__ccfAll.length} 条 · 检索/筛选/点击看详情`,null);
  $('modal-log').innerHTML=
    `<div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
        <input id="ccf-q" placeholder="搜索简称/全称/领域…" style="flex:1;min-width:180px"
          oninput="__ccfShown=120;renderCcfList()">
        <span id="ccf-rank-chips" class="roles" style="margin:0"></span>
        <span id="ccf-type-chips" class="roles" style="margin:0"></span>
      </div>
      <div id="ccf-list" style="max-height:52vh;overflow:auto"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px">
        <a href="/static/ccf-catalog-2026.pdf" target="_blank" style="font-size:12px">下载原始 PDF（第七版 2026）</a>
        <span id="ccf-count" class="sub"></span>
      </div>
    </div>`;
  renderCcfChips();renderCcfList();
  $('ccf-q').focus();
}
let __ccfF={r:'',t:''};
function renderCcfChips(){
  $('ccf-rank-chips').innerHTML=['','A','B','C'].map(r=>
    `<span class="chip ${__ccfF.r===r?'on':''}" onclick="__ccfF.r='${r}';renderCcfChips();renderCcfList()">${r||'全部'}</span>`).join('');
  $('ccf-type-chips').innerHTML=['','会议','期刊'].map(t=>
    `<span class="chip ${__ccfF.t===t?'on':''}" onclick="__ccfF.t='${t}';renderCcfChips();renderCcfList()">${t||'会议+期刊'}</span>`).join('');
}
function renderCcfList(){
  const q=($('ccf-q')&&$('ccf-q').value.trim().toLowerCase())||'';
  let es=__ccfAll;
  if(q)es=es.filter(e=>(e.s+' '+e.f+' '+e.a).toLowerCase().includes(q));
  if(__ccfF.r)es=es.filter(e=>e.r===__ccfF.r);
  if(__ccfF.t)es=es.filter(e=>e.t===__ccfF.t);
  $('ccf-count').textContent=`${es.length} 条`;
  const slice=es.slice(0,__ccfShown);
  $('ccf-list').innerHTML=slice.map(e=>{
    const k='b'+e.u;window.__ccfMap[k]=e;
    const cls=e.r==='A'?'blue':(e.r==='B'?'ok':'gray');
    return `<div style="display:flex;align-items:center;gap:10px;padding:7px 4px;border-bottom:1px solid var(--soft-br);cursor:pointer" onclick="showCcf('${k}')">
      <span class="tag ${cls}" style="flex-shrink:0">CCF-${esc(e.r)}</span>
      <span style="flex-shrink:0;font:600 12px var(--mono);min-width:70px">${esc((e.s||'').slice(0,14))}</span>
      <span style="flex:1;font-size:12px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(e.f)}">${esc(e.f)}</span>
      <span style="flex-shrink:0;font-size:11px;color:var(--faint)">${esc(e.t)} · ${esc((e.a||'').slice(0,12))}</span>
    </div>`}).join('')
    +(es.length>__ccfShown?`<div style="text-align:center;padding:10px"><button class="sm" onclick="__ccfShown+=120;renderCcfList()">加载更多（剩 ${es.length-__ccfShown} 条）</button></div>`:'')
    +(es.length?'':'<div class="empty">无匹配条目</div>');
}
function renderPaperMeta(){
  const p=window.__ctx.paper,f=window.__paperFull||{};
  $('paper-src').textContent=`来源 ${p.source_type||''} ${p.source_ref||''}`;
  $('paper-ccf').innerHTML=paperBadge(f.ccf,f.venue,'cur');
  const btn=$('paper-save');
  if(!btn)return;
  if(f.saved){
    btn.style.display='';btn.disabled=true;
    btn.textContent='✓ 已在调研库';
    btn.style.background='var(--green-bg)';btn.style.color='#1E9E5A';
    btn.style.border='1px solid #C9EDD8';
  }else{
    btn.style.display='';btn.disabled=false;
    btn.textContent='💾 保存到调研库';
    btn.style.background='';btn.style.color='';btn.style.border='';
  }
}
async function savePaper(){
  const p=window.__ctx.paper;if(!p)return;
  const r=await fetch(`/api/papers/${p.id}/save`,{method:'POST'});
  if(!r.ok){toast('保存失败: '+await errText(r));return}
  window.__paperFull=Object.assign({},window.__paperFull,{saved:1});
  renderPaperMeta();
  renderLibrary();
}
async function delLibPaper(id,ev){
  if(ev)ev.stopPropagation();
  const btn=ev?ev.target.closest('button'):null;
  if(btn&&!btn.dataset.armed){  // 第一次点击: 就地变成确认按钮, 4 秒后还原
    btn.dataset.armed='1';btn.textContent='确认删除';btn.classList.add('danger');
    setTimeout(()=>{if(btn.isConnected){delete btn.dataset.armed;
      btn.textContent='删除';btn.classList.remove('danger')}},4000);
    return;
  }
  const r=await fetch(`/api/papers/${id}`,{method:'DELETE'});
  if(!r.ok){toast('删除失败: '+await errText(r),'err');return}
  toast('已从调研库删除','ok');
  if(window.__ctx.paper&&window.__ctx.paper.id===id){
    window.__ctx.paper=null;window.__ctx.run=null;saveCtx();
    renderPaper();
  }
  renderLibrary();
}
async function toggleLibrary(){
  const box=$('paper-library');
  if(box.style.display==='none'){box.style.display='block';await renderLibrary()}
  else box.style.display='none';
}
async function renderLibrary(){
  try{
    const r=await fetch('/api/papers?saved=1');
    const ps=await r.json();
    window.__libCount=ps.length;
    $('library-sub').textContent=`已保存 ${ps.length} 篇 · 点击打开调研`;
    $('library-list').innerHTML=ps.length?ps.map(p=>
      `<div class="act-item" style="cursor:pointer;display:flex;align-items:center;gap:8px" onclick="choosePaper(${p.id},'${esc(p.title).replace(/'/g,'')}')">
        <span style="flex:1;min-width:0">
          <span class="nm">${esc(p.title)}${paperBadge(p.ccf,p.venue,'p'+p.id)}</span>
          <span class="rng">${p.source_type} ${p.source_ref||''} · ${new Date((p.created_at||0)*1000).toLocaleDateString()}${(window.__user&&window.__user.role==='advisor'&&p.owner)?' · '+esc(p.owner):''}</span></span>
        <button class="sm ghost" style="flex-shrink:0" title="从调研库删除" onclick="delLibPaper(${p.id},event)">删除</button></div>`).join('')
      :'<div class="empty">调研库为空 — 完成调研后点「💾 保存到调研库」</div>';
  }catch(e){$('library-list').innerHTML='<div class="empty">加载失败</div>'}
}
function cardSection(title,inner){return `<div class="card"><div class="card-h"><div class="ic">§</div><b>${title}</b></div>${inner}</div>`}
function renderCard(card){
  window.__card=card;
  if(window.__ctx.paper)window.__focusCardId=window.__ctx.paper.id;
  if(typeof curPage!=='undefined'&&curPage==='欢迎')renderFocus();
  if(typeof curPage!=='undefined'&&curPage==='日志')syncCurveFromFocus();
  const kv=o=>'<div class="kv">'+Object.entries(o||{}).map(([k,v])=>
    `<span class="k">${esc(k)}</span><span class="v">${vOr(Array.isArray(v)?v.join('；'):v)}</span>`).join('')+'</div>';
  const tbl=(rows,cols)=>rows&&rows.length?'<div style="overflow-x:auto"><table><thead><tr>'+
    cols.map(c=>`<th>${c[0]}</th>`).join('')+'</tr></thead><tbody>'+
    rows.map(r=>'<tr>'+cols.map(c=>`<td class="num" style="font-size:12px">${vOr(r[c[1]])}</td>`).join('')+'</tr>').join('')+
    '</tbody></table></div>':`<div class="empty">${MISS}</div>`;
  let html='';
  if(card['文献信息']){
    html+=`<div class="card"><div class="card-h"><div class="ic">§</div><b>① 文献信息</b>
      <div class="right"><span class="chip on" id="tab-orig" onclick="showInfoLang('orig')">原文</span>
      <span class="chip" id="tab-zh" onclick="showInfoLang('zh')">翻译</span></div></div>
      <div id="info-kv">${kv(card['文献信息'])}</div></div>`;
  }
  if(card['代码与资源'])html+=cardSection('② 代码与资源',kv(card['代码与资源']));
  if(card['实验架构']){
    const arch=card['实验架构']||{};
    const nodes=(arch['节点']||[]).filter(n=>n&&n!==MISS);
    html+=cardSection('③ 实验架构',
      `<p style="font-size:13px;margin-bottom:10px">${vOr(arch['描述'])}</p>`+
      (nodes.length?'<div class="roles">'+nodes.map(n=>
        `<span class="chip" title="${esc(n['连接']||'')}">${esc(n['名称']||'?')}<span style="color:var(--faint);font-size:10px"> · ${esc(n['类型']||'')}</span></span>`).join('')+'</div>':`<div class="empty">${MISS}</div>`));
  }
  if(card['参数设计'])html+=cardSection('④ 参数设计',tbl(card['参数设计'],[['参数','参数'],['值','值']]));
  if(card['数据集'])html+=cardSection('⑤ 数据集',tbl(card['数据集'],[['名称','名称'],['划分','划分'],['官方下载','官方下载'],['许可','许可']]));
  if(card['结论与局限']){
    const cl=card['结论与局限']||{};
    const lst=a=>((a||[]).filter(x=>x&&x!==MISS)).map(x=>`<li style="margin:4px 0">${esc(x)}</li>`).join('')||`<li class="missing">${MISS}</li>`;
    html+=cardSection('⑥ 结论与局限',
      `<b style="font-size:13px">结论</b><ul style="margin:6px 0 12px 20px">${lst(cl['结论'])}</ul><b style="font-size:13px">局限</b><ul style="margin:6px 0 0 20px">${lst(cl['局限'])}</ul>`);
  }
  if(card['复现要点']){
    const rp=card['复现要点']||{};
    const tables=rp['主要结果表'];
    const tableHtml=Array.isArray(tables)&&tables.length
      ?`<div style="margin:8px 0 12px"><div class="sub" style="margin-bottom:6px">主要结果表</div>`+
        `<div class="roles">${tables.map(t=>
          `<span class="chip" title="${esc(t['用途']||'')}">${esc(t['表号']||'?')}${t['表标题']?` · ${esc(String(t['表标题']).slice(0,28))}`:''}</span>`
        ).join('')}</div></div>`:'';
    const note=rp['对照说明']&&rp['对照说明']!==MISS
      ?`<p style="font-size:13px;margin:0 0 10px;color:var(--dim)">${esc(rp['对照说明'])}</p>`:'';
    html+=cardSection('⑦ 复现要点',
      note+
      `<div class="kv"><span class="k">最小可跑通命令</span><span class="v mono" style="font-size:12px">${vOr(rp['最小可跑通命令'])}</span>
       <span class="k">必填目录</span><span class="v">${vOr(rp['必填目录'])}</span></div>`+
      tableHtml+
      `<div class="sub" style="margin:8px 0 6px">对照指标（用于日志曲线 / 对照页）</div>`+
      tbl(rp['对照指标'],[
        ['指标','指标名'],['表号','论文表号'],['数值','数值'],['越高越好','越高越好'],
        ['说明','说明'],['设定','设定'],['数据集','数据集'],['方法行','方法行']
      ]));
  }
  if(!html)html=`<div class="card"><div class="empty">本次未抽取任何板块</div></div>`;
  $('paper-cards').innerHTML=html;
}
const kvInfoHtml=o=>'<div class="kv">'+Object.entries(o||{}).map(([k,v])=>
  `<span class="k">${esc(k)}</span><span class="v" style="font-family:var(--sans);font-size:13px">${vOr(Array.isArray(v)?v.join('；'):v)}</span>`).join('')+'</div>';
async function showInfoLang(lang){
  const origTab=$('tab-orig'),zhTab=$('tab-zh');
  if(lang==='orig'){
    origTab.classList.add('on');zhTab.classList.remove('on');
    $('info-kv').innerHTML=kvInfoHtml((window.__card||{})['文献信息']);return;
  }
  zhTab.classList.add('on');origTab.classList.remove('on');
  if(!window.__infoZh){
    $('info-kv').innerHTML='<div class="empty">GLM 翻译中（约 5-20 秒，服务器缓存后永久生效）…</div>';
    const r=await fetch(`/api/papers/${window.__ctx.paper.id}/translate`,{method:'POST'});
    if(!r.ok){$('info-kv').innerHTML='<div class="empty">翻译失败: '+esc(await errText(r))+'</div>';return}
    window.__infoZh=await r.json();
  }
  $('info-kv').innerHTML=kvInfoHtml(window.__infoZh);
}
function cardToMd(p,c){
  const MISS='未在论文中找到';
  const V=x=>(x==null||x===''||x===MISS)?MISS:(Array.isArray(x)?x.join('；'):String(x));
  const escMd=s=>String(s).replace(/\|/g,'\\|').replace(/\n/g,' ');
  const tbl=(rows,cols)=>!rows||!rows.length?''
    :'| '+cols.join(' | ')+' |\n|'+cols.map(()=>'---').join('|')+'|\n'
    +rows.map(r=>'| '+cols.map(cn=>escMd(V(r[cn]))).join(' | ')+' |').join('\n');
  const info=c['文献信息']||{};
  const L=[];
  L.push(`# 调研卡 · ${escMd(p.title||info['标题']||'未命名论文')}`,'',
    `> 来源：${p.source_type||''} ${p.source_ref||''} ｜ 导出时间：${new Date().toLocaleString('zh-CN')}`,'',
    '## 一、文献信息','',
    '| 字段 | 内容 |','|---|---|',
    ...Object.entries(info).map(([k,v])=>`| ${escMd(k)} | ${escMd(V(v))} |`),'',
    '## 二、代码与资源','',
    ...Object.entries(c['代码与资源']||{}).map(([k,v])=>`- **${escMd(k)}**：${escMd(V(v))}`),'',
    '## 三、实验架构','',
    V((c['实验架构']||{})['描述']),'',
    tbl((c['实验架构']||{})['节点'],['名称','类型','连接']),'',
    '## 四、参数设计','',
    tbl(c['参数设计'],['参数','值']),'',
    '## 五、数据集','',
    tbl(c['数据集'],['名称','划分','预处理','官方下载','许可','本机是否已有']),'',
    '## 六、结论与局限','',
    '**结论**',...((c['结论与局限']||{})['结论']||[]).map(x=>`- ${escMd(V(x))}`),'',
    '**局限**',...((c['结论与局限']||{})['局限']||[]).map(x=>`- ${escMd(V(x))}`),'',
    '## 七、复现要点','',
    '```bash',V((c['复现要点']||{})['最小可跑通命令']),'```','',
    `- **必填目录**：${escMd(V((c['复现要点']||{})['必填目录']))}`,'',
    '### 对照说明','',
    V((c['复现要点']||{})['对照说明']),'',
    '### 主要结果表','',
    tbl((c['复现要点']||{})['主要结果表'],['表号','表标题','用途']),'',
    '### 对照指标','',
    tbl((c['复现要点']||{})['对照指标'],['指标名','论文表号','数值','越高越好','说明','设定','数据集','方法行']),'');
  return L.join('\n');
}
function downloadMd(name,text){
  const blob=new Blob([text],{type:'text/markdown;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=name.replace(/[\\/:*?"<>|]/g,'');
  a.click();URL.revokeObjectURL(a.href);
}
function exportCard(){
  const p=window.__ctx.paper;if(!p)return;
  downloadMd(`调研卡-${(p.title||'论文').slice(0,40)}.md`,cardToMd(p,window.__card||{}));
}
async function exportAllCards(){
  await loadPapers();
  const saved=papers.filter(p=>p.saved);
  if(!saved.length){toast('调研库为空');return}
  const parts=[];
  for(const p of saved){
    try{
      const full=await (await fetch(`/api/papers/${p.id}`)).json();
      if(full.card_json)parts.push(cardToMd(p,JSON.parse(full.card_json)));
    }catch(e){}
  }
  if(!parts.length){toast('没有已抽取的调研卡');return}
  downloadMd(`调研卡合集-${new Date().toISOString().slice(0,10)}.md`,parts.join('\n\n---\n\n'));
}
function pickPaper(){
  loadPapers().then(()=>{
    // 焦点只能从调研库已保存、且已抽取调研卡的论文中选
    const lib=papers.filter(p=>p.saved&&p.extracted);
    if(!lib.length){
      const savedOnly=papers.filter(p=>p.saved&&!p.extracted).length;
      if(savedOnly){
        toast('调研库中的论文尚未抽取调研卡，请先去「调研」完成抽取并保存');
      }else{
        toast('调研库还是空的：先上传/调研论文，保存到调研库后再设为焦点');
      }
      return;
    }
    const row=p=>`<div class="act-item" style="cursor:pointer" onclick="choosePaper(${p.id})">
      <span class="nm">${esc(p.title||('论文 #'+p.id))}${paperBadge(p.ccf,p.venue,'pick'+p.id)}</span>
      <span class="rng">${esc(p.source_type||'')} ${esc(p.source_ref||'')}${(window.__user&&window.__user.role==='advisor'&&p.owner)?' · '+esc(p.owner):''}</span></div>`;
    openModalText(`从调研库选择焦点论文 · ${lib.length} 篇`,
      '仅显示已保存且已抽取的论文',
      `<div class="sub" style="margin-bottom:10px">流程：上传并调研 → 保存到调研库 → 在此设为当前焦点</div>`
      +lib.map(row).join(''), true);
  });
}

/* ================= 执行 ================= */
const CONDA_SH='/home/niexin/miniconda3/etc/profile.d/conda.sh';
function buildLaunchCommand(){
  let cmd=($('x-cmd').value||'').trim();
  const extra=($('x-extra').value||'').trim();
  const env=($('x-env').value||'').trim();
  if(extra)cmd=(cmd?(cmd+' '+extra):extra).trim();
  if(env){
    // shell=True on server; activate conda then run user command
    cmd=`source ${CONDA_SH} && conda activate ${env} && ${cmd}`;
  }
  return cmd;
}
function updateLaunchPreview(){
  const dir=($('x-dir').value||'').trim();
  const raw=($('x-cmd').value||'').trim();
  const finalCmd=buildLaunchCommand();
  const extra=($('x-extra').value||'').trim();
  const env=($('x-env').value||'').trim();
  if($('lp-dir'))$('lp-dir').textContent=dir||'（未填写）';
  // show user-facing command (with extra) before conda wrap for clarity
  let showCmd=raw||'（未填写）';
  if(raw&&extra)showCmd=raw+' '+extra;
  if($('lp-cmd'))$('lp-cmd').textContent=showCmd+(env?`  ·  conda: ${env}`:'');
  if($('lp-eq')){
    if(!dir||!raw)$('lp-eq').textContent='—';
    else $('lp-eq').textContent=`cd ${dir} && ${finalCmd}`;
  }
  const ctx=$('launch-ctx');
  if(ctx){
    const paper=window.__ctx&&window.__ctx.paper;
    ctx.textContent=paper
      ?(`当前论文：${paper.title||('#'+paper.id)} · 命令在工作目录下执行`)
      :'选择目录与命令 · 一眼看清在哪跑';
  }
  renderPreflight();
}
function renderExecPrefill(){
  const c=window.__card;
  const remembered=localStorage.getItem('wb_workdir')||'';
  if(remembered&&$('x-dir')&&!$('x-dir').value)$('x-dir').value=remembered;
  if(c&&c['复现要点']){
    const rp=c['复现要点'];
    if(!$('x-cmd').value)$('x-cmd').value=String(rp['最小可跑通命令']||'').slice(0,200)||'';
    if(!$('x-dir').value)$('x-dir').value=String(rp['必填目录']||'').slice(0,120)||'';
  }
  updateLaunchPreview();
}
const TREE_JUNK=new Set(['__pycache__','.git','.claude','.idea','node_modules']);
function treeHtml(d,depth){
  const pad=depth*18;
  return (d.items||d.entries||[]).map(it=>{
    const isDir=it.dir!==undefined?it.dir:false;
    const junk=TREE_JUNK.has(it.name);
    if(isDir)
      return `<div class="tree-node" style="padding-left:${pad}px;${junk?'color:var(--faint)':''}"
        data-path="${esc(d.path+'/'+it.name)}" onclick="toggleTreeNode(this)"><span class="tree-arrow" style="display:inline-block;width:14px">▸</span>📁 ${esc(it.name)}</div>
        <div class="tree-kids" style="display:none"></div>`;
    return `<div style="padding-left:${pad+20}px;font:11px var(--mono);color:${junk?'var(--faint)':'var(--dim)'}">· ${esc(it.name)}</div>`;
  }).join('');
}
async function toggleTreeNode(el){
  const kids=el.nextElementSibling;
  if(!kids)return;
  const arrow=el.querySelector('.tree-arrow');
  if(kids.style.display!=='none'){kids.style.display='none';if(arrow)arrow.textContent='▸';return}
  if(!kids.innerHTML){
    kids.innerHTML='<div class="empty" style="padding:6px">加载中…</div>';
    try{
      const r=await fetch('/api/dircheck',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:el.dataset.path})});
      const d=await r.json();
      kids.innerHTML=d.exists?treeHtml(d,1):'<div class="empty">无法读取</div>';
    }catch(e){kids.innerHTML='<div class="empty">读取失败</div>'}
  }
  kids.style.display='block';if(arrow)arrow.textContent='▾';
}
async function dirCheck(){
  const p=$('x-dir').value.trim();
  if(!p){toast('填写服务器目录');return}
  const r=await fetch('/api/dircheck',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})});
  const d=await r.json();
  const box=$('dircheck-result');
  if(!d.exists){box.innerHTML=`<span class="tag err">目录不存在</span> <span class="num" style="font-size:11px">${esc(d.path)}</span>`;updateLaunchPreview();return}
  localStorage.setItem('wb_workdir',d.path);
  box.innerHTML=`<span class="tag ${d.writable?'ok':'err'}">${d.writable?'目录可写 ✓':'目录不可写'}</span>
    <span class="tag ${d.has_code?'blue':'warn'}">${d.has_code?'识别到代码':'未见代码/配置'}</span>
    <div style="margin-top:8px;max-height:220px;overflow:auto;border:1px solid var(--soft-br);border-radius:var(--r-sm);padding:8px 10px;background:#FAFBFC">${treeHtml(d,0)}</div>`;
  window.__dirOk=!!(d.exists&&d.writable);
  updateLaunchPreview();
  renderPreflight(d);
}
async function renderPreflight(dirInfo){
  const snap=window.__gpuSnap||{};
  const gpus=(snap.gpus||[]).filter(g=>!g.error&&g.util<10&&g.mem_used<2000).map(g=>g.index);
  const pin=$('x-gpus')?$('x-gpus').value.trim():'';
  const items=[];
  if(dirInfo){
    items.push(dirInfo.exists?(dirInfo.writable?['ok','工作目录可写']:['err','目录不可写']):['err','目录不存在']);
  }else if(window.__dirOk){
    items.push(['ok','工作目录已预检']);
  }else{
    items.push(['warn','目录未预检（建议先点预检）']);
  }
  items.push(['ok',`当前空闲 GPU：${gpus.length?gpus.join(', '):'无'}`]);
  if(pin){
    const want=pin.split(',').map(s=>+s.trim()).filter(n=>!isNaN(n));
    const freeSet=new Set(gpus);
    const allFree=want.every(i=>freeSet.has(i));
    items.push(allFree?['ok',`将使用指定卡 ${pin}`]:['warn',`指定卡 ${pin} 暂不空闲 · 将排队等待`]);
  }else{
    items.push(['ok','GPU 自动分配']);
  }
  if(!window.__ctx.paper)items.push(['warn','未选论文（对照页将不可用）']);
  if($('preflight'))$('preflight').innerHTML=items.map(i=>`<span class="tag ${i[0]}">${i[1]}</span>`).join('');
}
async function launchRun(){
  const dir=$('x-dir').value.trim(), raw=$('x-cmd').value.trim(), name=$('x-name').value.trim();
  const pin=$('x-gpus').value.trim()||null;
  if(!dir||!raw){toast('工作目录和启动命令必填');return}
  const command=buildLaunchCommand();
  if(!command){toast('命令为空');return}
  const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name||('run-'+Date.now().toString(36)),command,gpus_needed:pin?pin.split(',').length:1,workdir:dir,gpus_pinned:pin})});
  if(!r.ok){toast(await errText(r));return}
  const t=await r.json();
  window.__ctx.run={type:'task',key:'t'+t.id,id:t.id,name:t.name,log_file:null,status:'pending'};
  saveCtx();pushRecent('run',t.name);
  localStorage.setItem('wb_workdir',dir);
  loadTasks();
  showPage('日志');
  startRunLog();
}

/* ================= 对照 ================= */
function renderComparePage(){
  const p=window.__ctx.paper,run=window.__ctx.run;
  const sel=$('cmp-run');
  const opts=[...liveExps.map(l=>({v:'l'+l.pid,label:l.name,log:l.log,jsonl:l.jsonl})),
    ...(window.__tasks||[]).filter(t=>t.status==='done').map(t=>({v:'t'+t.id,label:t.name+' (任务)',log:null}))];
  if(!p||!opts.length){$('cmp-empty').style.display='block';$('cmp-wrap').style.display='none';return}
  $('cmp-empty').style.display='none';$('cmp-wrap').style.display='block';
  sel.innerHTML=opts.map(o=>`<option value="${o.v}">${esc(o.label.slice(0,30))}</option>`).join('');
  if(run&&run.key)sel.value=run.key;
  renderCompare();
}
async function renderCompare(){
  const p=window.__ctx.paper;if(!p)return;
  const v=$('cmp-run').value;
  let log='',jsonl='';
  const lv=liveExps.find(l=>'l'+l.pid===v);
  if(lv){log=lv.log||'';jsonl=lv.jsonl||''}
  $('cmp-sub').textContent=`${p.title.slice(0,24)} × ${($('cmp-run').selectedOptions[0]||{label:''}).label.slice(0,24)}`;
  const r=await fetch('/api/compare',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({paper_id:p.id,jsonl,log})});
  const body=$('cmp-body');
  if(!r.ok){body.innerHTML=`<tr><td colspan="6" class="empty">${await errText(r)}</td></tr>`;$('cmp-conclusion').innerHTML='';return}
  const d=await r.json();
  body.innerHTML=d.rows.map(row=>`<tr>
    <td style="font-size:12px">${esc(row['指标'])}</td><td class="num">${esc(row['表号']||'')}</td>
    <td class="num">${row['论文值']}</td><td class="num">${row['本次最优']==null?'—':row['本次最优']}</td>
    <td class="num">${row['相对误差%']!=null?row['相对误差%']+'%':'—'}</td>
    <td>${row['判定']==='达到'?'<span class="tag ok">达到</span>':row['判定']==='接近'?'<span class="tag warn">接近</span>':row['判定']==='无本次数据'?'<span class="tag gray">无数据</span>':'<span class="tag err">未达到</span>'}</td></tr>`).join('');
  const cls=d.conclusion==='复现成功'?'ok':d.conclusion==='部分复现'?'warn':'err';
  $('cmp-conclusion').innerHTML=`<span class="tag ${cls}" style="font-size:14px;padding:8px 22px">${d.conclusion}</span>
    <span style="color:var(--dim);font-size:12px;margin-left:12px">判定阈值: ≤${d.thresholds.reached_pct}% 达到 · ≤${d.thresholds.near_pct}% 接近</span>`;
  window.__cmpLast={paper_id:p.id,jsonl,log};
}
function exportReport(){
  const c=window.__cmpLast;if(!c){toast('先完成一次对照');return}
  const q=`jsonl=${encodeURIComponent(c.jsonl||'')}&log=${encodeURIComponent(c.log||'')}`;
  window.open(`/api/papers/${c.paper_id}/report?${q}`,'_blank');
}

/* ================= 每周动态(沿用) ================= */
async function loadActivity(){
  try{actItems=(await (await fetch('/api/activity?days=200')).json()).items}catch(e){return}
  renderCal();
}
function calMove(n){calCur=new Date(calCur.getFullYear(),calCur.getMonth()+n,1);renderCal()}
function calToday(){calCur=new Date();calSelDay=new Date();renderCal()}
function renderCal(){
  const y=calCur.getFullYear(),m=calCur.getMonth();
  $('cal-title').textContent=`${y}年${m+1}月`;
  const days=new Date(y,m+1,0).getDate();
  const lead=(new Date(y,m,1).getDay()+6)%7,today=new Date();
  const maxH=Math.max(1,...Array.from({length:days},(_,k)=>{
    const d0=new Date(y,m,k+1).getTime()/1000;let h=0;
    actItems.forEach(i=>h+=overlapSec(i.start,i.end,d0,d0+86400));return h/3600;}));
  let html=['一','二','三','四','五','六','日'].map(w=>`<div class="cw">${w}</div>`).join('');
  for(let i=0;i<lead;i++)html+='<div class="cal-cell dim"></div>';
  for(let d=1;d<=days;d++){
    const ds=new Date(y,m,d),de=new Date(y,m,d+1);
    const acts=actItems.filter(i=>i.start<de.getTime()/1000&&i.end>=ds.getTime()/1000);
    let h=0;acts.forEach(i=>h+=overlapSec(i.start,i.end,ds.getTime()/1000,de.getTime()/1000));
    const bg=acts.length?`style="background:rgba(47,111,237,${(0.06+0.22*(h/3600)/maxH).toFixed(2)})"`:'';
    const isT=today.toDateString()===ds.toDateString(),isS=calSelDay&&calSelDay.toDateString()===ds.toDateString();
    html+=`<div class="cal-cell${isT?' today':''}${isS?' sel':''}" ${bg} onclick="pickDay('${y}-${m+1}-${d}')">
      <span class="dn" style="font-size:10px">${d}</span>${acts.length?`<span class="n">${acts.length}</span>`:''}</div>`;
  }
  $('cal-grid').innerHTML=html;
  renderDay();
  const now=new Date();const wd=(now.getDay()+6)%7;
  const ws=new Date(now.getFullYear(),now.getMonth(),now.getDate()-wd);
  const w0=ws.getTime()/1000,w1=w0+7*86400;
  const inW=actItems.filter(i=>i.start<w1&&i.end>=w0);
  let gpuH=0;inW.forEach(i=>gpuH+=overlapSec(i.start,i.end,w0,w1));
  $('act-wk').textContent=`${fmtD(w0)}~${fmtD(w1-D8)} · ${inW.length} 实验 · ${(gpuH/3600).toFixed(0)} 卡时`;
}
function pickDay(s){const[a,b,c]=s.split('-').map(Number);calSelDay=new Date(a,b-1,c);renderCal()}
function renderDay(){
  if(!calSelDay){$('cal-day').innerHTML='';return}
  const ds=calSelDay,de=new Date(ds.getTime()+D8);
  const acts=actItems.filter(i=>i.start<de.getTime()/1000&&i.end>=ds.getTime()/1000);
  const fmtH=t=>new Date(t*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  let html=`<div style="font:600 11px var(--sans);color:var(--dim);margin-bottom:6px">${ds.toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'short'})}</div>`;
  if(!acts.length){$('cal-day').innerHTML=html+'<div class="empty" style="padding:10px">当天无实验</div>';return}
  const groups={};acts.forEach(i=>{(groups[i.group]=groups[i.group]||[]).push(i)});
  for(const[g,list]of Object.entries(groups)){
    html+=`<div style="font:600 10px var(--sans);color:var(--dim);margin:6px 0 4px">${esc(g)} · ${list.length} 个</div>`;
    html+=list.map(i=>{
      const st=Math.max(i.start,ds.getTime()/1000),en=Math.min(i.end,de.getTime()/1000);
      return `<div class="act-item"><span class="nm">${isRun(i)?'<span class="dot run" style="display:inline-block;margin-right:6px"></span>':''}${esc(i.short||i.name)}
        ${i.gpu?`<span class="tag blue" style="margin-left:6px">GPU${i.gpu}</span>`:''}${i.best!=null?`<span class="tag ok" style="margin-left:4px">最佳 ${i.best.toFixed(1)}</span>`:''}</span>
        <span class="rng">${i.start<ds.getTime()/1000?'← 前已开始':fmtH(i.start)} → ${i.end>=de.getTime()/1000?'持续中 →':fmtH(i.end)}</span></div>`}).join('');
  }
  $('cal-day').innerHTML=html;
}

/* ================= AI 悬浮球 ================= */
function toggleAI(){$('ai-panel').classList.toggle('on')}
let chatHist=[];
try{chatHist=JSON.parse(localStorage.getItem('wb3_chat')||'[]')}catch(e){}
function renderChat(){
  const box=$('ai-msgs');
  if(!chatHist.length){box.innerHTML='<div class="empty">对话历史存于浏览器本地</div>';return}
  box.innerHTML=chatHist.map(m=>`<div class="msg ${m.role}">${esc(m.content)}</div>`).join('');
  box.scrollTop=box.scrollHeight;
}
function clearChat(){chatHist=[];localStorage.removeItem('wb3_chat');renderChat()}
function buildChatExps(){
  const sel=$('c-exp');
  window.__chatExps=[...liveExps.map(l=>({type:'live',jsonl:l.jsonl,log:l.log,name:l.name}))];
  sel.innerHTML='<option value="">—</option>'+window.__chatExps.map((e,i)=>
    `<option value="${i}">▶ ${esc(e.name.slice(0,30))}</option>`).join('');
}
async function sendChat(){
  const inp=$('chat-in');const text=inp.value.trim();if(!text)return;
  inp.value='';
  chatHist.push({role:'user',content:text});renderChat();
  const cs=$('c-exp');let expBody={};
  if(cs.value){const e=(window.__chatExps||[])[+cs.value];
    if(e)expBody={jsonl:e.jsonl,log:e.log,exp_name:e.name};}
  try{
    if(isBrowserAi()){
      const prepR=await fetch('/api/chat/prepare',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({messages:chatHist,include_gpu:$('c-gpu').checked,...expBody})});
      if(!prepR.ok)throw new Error(await errText(prepR));
      const prep=await prepR.json();
      const msgs=[{role:'system',content:prep.system||''},...chatHist];
      const j=await browserChatCompletions(msgs);
      const reply=(((j.choices||[])[0]||{}).message||{}).content||'(空回复)';
      chatHist.push({role:'assistant',content:reply});
    }else{
      const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({messages:chatHist,include_gpu:$('c-gpu').checked,...expBody})});
      const j=await r.json();
      if(!r.ok)throw new Error(j.detail||r.statusText);
      chatHist.push({role:'assistant',content:j.reply});
    }
  }catch(e){chatHist.push({role:'assistant',content:'错误: '+e.message})}
  localStorage.setItem('wb3_chat',JSON.stringify(chatHist));
  renderChat();
}

/* ================= 设置 ================= */
const SET_FIELDS=[['s-gpus','default_gpus'],['s-visible','visible_gpus'],['s-coderoot','code_root'],
  ['s-dataroot','data_root'],['s-outroot','out_root'],['s-reached','reached_pct'],['s-near','near_pct'],
  ['s-env','default_env'],['s-envvars','env_vars'],['s-perm-view','perm_view'],['s-perm-run','perm_run']];

const AI_CLIENT_KEY='wb3_ai_client';
const AI_PROFILES_KEY='wb3_ai_profiles';

function loadAiClientCfg(){
  try{return JSON.parse(localStorage.getItem(AI_CLIENT_KEY)||'{}')||{}}catch(e){return {}}
}
function saveAiClientCfg(cfg){
  localStorage.setItem(AI_CLIENT_KEY, JSON.stringify(cfg||{}));
}
function loadAiProfilesStore(){
  try{
    const s=JSON.parse(localStorage.getItem(AI_PROFILES_KEY)||'{}')||{};
    if(!Array.isArray(s.profiles)) s.profiles=[];
    return s;
  }catch(e){return {profiles:[], activeId:''}}
}
function saveAiProfilesStore(store){
  localStorage.setItem(AI_PROFILES_KEY, JSON.stringify(store||{profiles:[],activeId:''}));
}
function _aiNewId(){
  return 'p_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,7);
}
function ensureAiProfilesMigrated(){
  const store=loadAiProfilesStore();
  if(store.profiles.length)return store;
  const cur=loadAiClientCfg();
  // seed defaults
  const seeds=[];
  if(cur.base_url||cur.api_key){
    seeds.push({
      id:_aiNewId(),
      name:cur.browser?'浏览器直连（当前）':'当前配置',
      browser:!!cur.browser,
      base_url:cur.base_url||'',
      model:cur.model||'',
      api_key:cur.api_key||'',
      updated_at:Date.now(),
    });
  }
  seeds.push({
    id:_aiNewId(), name:'实验室服务器 · 智谱', browser:false,
    base_url:'', model:'glm-4.7', api_key:'', updated_at:Date.now(),
  });
  seeds.push({
    id:_aiNewId(), name:'AgentRouter（浏览器）', browser:true,
    base_url:'https://agentrouter.org/v1', model:'claude-opus-4-6',
    api_key:cur.browser? (cur.api_key||'') : '', updated_at:Date.now(),
  });
  store.profiles=seeds;
  store.activeId=seeds[0].id;
  saveAiProfilesStore(store);
  return store;
}
function getAiProfile(id){
  const store=ensureAiProfilesMigrated();
  return (store.profiles||[]).find(p=>p.id===id)||null;
}
function renderAiProfileSelect(selectId){
  const store=ensureAiProfilesMigrated();
  const sel=$('c-profile'); if(!sel)return;
  const aid=selectId||store.activeId||'';
  sel.innerHTML=(store.profiles||[]).map(p=>{
    const route=p.browser?'浏览器':'服务器';
    const mdl=(p.model||'').slice(0,22);
    const label=`${p.name} · ${route}${mdl?(' · '+mdl):''}`;
    return `<option value="${String(p.id).replace(/"/g,'')}">${esc(label)}</option>`;
  }).join('')||'<option value="">（暂无配置）</option>';
  if(aid && [...sel.options].some(o=>o.value===aid)) sel.value=aid;
  const p=getAiProfile(sel.value);
  if($('c-profile-name'))$('c-profile-name').value=p? (p.name||'') : '';
}
function applyAiProfileToActive(p){
  if(!p)return;
  if(p.browser){
    saveAiClientCfg({
      browser:true,
      base_url:(p.base_url||'').replace(/\/$/,''),
      model:p.model||'',
      api_key:p.api_key||'',
      profile_id:p.id,
    });
  }else{
    const cur=loadAiClientCfg();
    saveAiClientCfg({
      ...cur,
      browser:false,
      profile_id:p.id,
      // keep last browser key in profiles only; clear active browser fields
    });
  }
  const store=loadAiProfilesStore();
  store.activeId=p.id;
  saveAiProfilesStore(store);
}
function collectAiProfileFromForm(existing){
  const f=formAiFields();
  const browser=!!$('ai-route-browser')?.checked;
  const name=($('c-profile-name').value||'').trim() || (existing&&existing.name) || '未命名配置';
  let key=(f.api_key||'').trim();
  if(!key && existing) key=existing.api_key||'';
  if(browser){
    const cur=loadAiClientCfg();
    if(!key) key=cur.api_key||'';
  }
  return {
    id:(existing&&existing.id)||_aiNewId(),
    name,
    browser,
    base_url: browser ? (f.base_url||'').replace(/\/$/,'') : (f.base_url||existing?.base_url||''),
    model:f.model||'',
    api_key: browser ? key : (existing?.api_key||''), // server key stays on server
    updated_at:Date.now(),
  };
}
function upsertAiProfile(prof){
  const store=loadAiProfilesStore();
  const i=(store.profiles||[]).findIndex(p=>p.id===prof.id);
  if(i>=0) store.profiles[i]=prof;
  else store.profiles.push(prof);
  store.activeId=prof.id;
  saveAiProfilesStore(store);
  return prof;
}
function onAiProfileSelect(){
  const id=$('c-profile')?.value;
  const p=getAiProfile(id);
  if(!p)return;
  if($('c-profile-name'))$('c-profile-name').value=p.name||'';
  applyAiProfileToActive(p);
  loadChatConfig();
  toast(`已切换：${p.name}`,'ok');
}
function saveAiProfileAsNew(){
  const name=($('c-profile-name').value||'').trim();
  if(!name){toast('请先填写配置名称');return}
  const prof=collectAiProfileFromForm(null);
  prof.name=name;
  if(prof.browser && !prof.api_key){toast('浏览器直连请填写 Token');return}
  if(prof.browser && !prof.base_url){toast('请填写 API Base URL');return}
  upsertAiProfile(prof);
  applyAiProfileToActive(prof);
  renderAiProfileSelect(prof.id);
  toast(`已另存为「${name}」`,'ok');
  loadChatConfig();
}
function deleteAiProfile(){
  const id=$('c-profile')?.value;
  const store=loadAiProfilesStore();
  if(!id||!(store.profiles||[]).length){toast('没有可删除的配置');return}
  const p=getAiProfile(id);
  if(!confirm(`删除配置「${p?p.name:id}」？`))return;
  store.profiles=store.profiles.filter(x=>x.id!==id);
  if(store.activeId===id) store.activeId=(store.profiles[0]&&store.profiles[0].id)||'';
  saveAiProfilesStore(store);
  if(store.activeId){
    applyAiProfileToActive(getAiProfile(store.activeId));
  }
  renderAiProfileSelect(store.activeId);
  loadChatConfig();
  toast('已删除','ok');
}
function isBrowserAi(){
  return !!(loadAiClientCfg().browser);
}
function maskKey(k){
  k=String(k||'');
  if(k.length<=8)return k?'****':'';
  return k.slice(0,4)+'…'+k.slice(-4);
}
function formAiFields(){
  const custom=($('c-model-custom').value||'').trim();
  return {
    base_url:($('c-base').value||'').trim(),
    model:custom||$('c-model').value,
    api_key:($('c-key').value||'').trim(),
  };
}
function fillAiForm(base_url, model, hasKey, masked){
  $('c-base').value=base_url||'';
  const sel=$('c-model');
  const known=[...sel.options].some(o=>o.value===model);
  if(known){sel.value=model;$('c-model-custom').value=''}
  else{sel.value='glm-4.7';$('c-model-custom').value=model||''}
  $('c-key').value='';
  $('c-key').placeholder=hasKey
    ?(`已配置 ${masked} · 留空保持不变，填新值则覆盖`)
    :'尚未配置 · 粘贴 API Key';
}
function onAiRouteChange(){
  const browser=$('ai-route-browser')?.checked;
  const cur=loadAiClientCfg();
  cur.browser=!!browser;
  saveAiClientCfg(cur);
  loadChatConfig();
}
async function browserChatCompletions(messages, opts={}){
  const cfg=loadAiClientCfg();
  const base=(cfg.base_url||'').replace(/\/$/,'');
  const key=cfg.api_key||'';
  const model=cfg.model||'claude-opus-4-6';
  if(!base||!key)throw new Error('请先在设置中开启「浏览器直连」并保存 Base URL / Token');
  const endpoint=base+'/chat/completions';
  let r;
  try{
    r=await fetch(endpoint,{
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
      body:JSON.stringify({
        model,
        messages,
        temperature:opts.temperature??0.2,
        max_tokens:opts.max_tokens,
        stream:false,
      }),
    });
  }catch(e){
    const m=String(e.message||e);
    if(/Failed to fetch|NetworkError|CORS/i.test(m))
      throw new Error('浏览器直连失败（多为跨域 CORS 或本机网络）：'+m);
    throw e;
  }
  if(!r.ok){
    const t=await r.text().catch(()=>'');
    throw new Error(`模型接口 ${r.status}：${t.slice(0,240)}`);
  }
  return await r.json();
}
function parseExtractJson(content, sections){
  let s=String(content||'').trim()
    .replace(/^```json\s*/i,'').replace(/^```\s*/,'').replace(/\s*```$/,'').trim();
  const card=JSON.parse(s);
  if(!card||typeof card!=='object'||Array.isArray(card))
    throw new Error('抽取结果不是 JSON 对象');
  const want=new Set(sections&&sections.length?sections:Object.keys(card));
  const out={};
  for(const k of want){if(k in card)out[k]=card[k]}
  if(!Object.keys(out).length)throw new Error('JSON 中缺少目标板块');
  return out;
}

async function loadChatConfig(){
  const st=$('chat-cfg-status'), hint=$('chat-cfg-hint');
  ensureAiProfilesMigrated();
  renderAiProfileSelect();
  const local=loadAiClientCfg();
  const browser=!!local.browser;
  if($('ai-route-browser'))$('ai-route-browser').checked=browser;
  if($('ai-route-server'))$('ai-route-server').checked=!browser;
  const active=getAiProfile(local.profile_id||$('c-profile')?.value);
  if(active && $('c-profile-name'))$('c-profile-name').value=active.name||'';
  try{
    if(browser){
      fillAiForm(
        local.base_url||active?.base_url||'https://agentrouter.org/v1',
        local.model||active?.model||'claude-opus-4-6',
        !!(local.api_key||active?.api_key),
        maskKey(local.api_key||active?.api_key||''));
      const ok=!!(local.base_url&&local.api_key);
      if(st){st.textContent=ok?'浏览器直连':'未配置';st.style.color=ok?'var(--green)':'var(--amber)'}
      if(hint)hint.textContent=ok
        ?`档案「${active?.name||'当前'}」· ${local.model} · Token ${maskKey(local.api_key)} · 本机保存，下次可下拉切换`
        :'填写后点「保存并启用」；可用「另存为」保留多套配置';
      return;
    }
    const c=await (await fetch('/api/chat-config')).json();
    fillAiForm(c.base_url||'', c.model||'glm-4.7', c.has_api_key, c.api_key_masked);
    if(st)st.textContent=c.configured?'服务器调用':'未配置';
    if(st)st.style.color=c.configured?'var(--green)':'var(--amber)';
    if(hint)hint.textContent=c.configured
      ?`服务器调用 ${c.model} · Token ${c.api_key_masked} · 可另存档案方便切换回浏览器配置`
      :'保存后由机房服务器出网调用（实验室共用）';
  }catch(e){
    if(hint)hint.textContent='加载失败: '+e;
  }
}
async function saveChatConfig(){
  const f=formAiFields();
  const browser=$('ai-route-browser')?.checked;
  const curId=$('c-profile')?.value;
  const existing=getAiProfile(curId);
  const name=($('c-profile-name').value||'').trim() || existing?.name || (browser?'浏览器直连':'服务器配置');
  if($('c-profile-name'))$('c-profile-name').value=name;

  if(browser){
    const cur=loadAiClientCfg();
    const key=(f.api_key||'').trim() || cur.api_key || existing?.api_key || '';
    if(!f.base_url){toast('请填写 API Base URL');return}
    if(!key){toast('请填写 API Token');return}
    const prof=collectAiProfileFromForm(existing||null);
    prof.name=name;
    prof.browser=true;
    prof.base_url=f.base_url.replace(/\/$/,'');
    prof.model=f.model;
    prof.api_key=key;
    upsertAiProfile(prof);
    applyAiProfileToActive(prof);
    renderAiProfileSelect(prof.id);
    toast(`已保存并启用「${name}」`,'ok');
    $('c-key').value='';
    await loadChatConfig();
    return;
  }
  const body={base_url:f.base_url, model:f.model, api_key:f.api_key};
  const r=await fetch('/api/chat-config',{method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){toast('保存失败: '+await errText(r));return}
  const prof=collectAiProfileFromForm(existing||null);
  prof.name=name;
  prof.browser=false;
  prof.model=f.model;
  prof.base_url=f.base_url;
  upsertAiProfile(prof);
  applyAiProfileToActive(prof);
  renderAiProfileSelect(prof.id);
  toast(`服务器配置已保存 · 档案「${name}」`,'ok');
  $('c-key').value='';
  await loadChatConfig();
}
async function testChatConfig(){
  const btn=$('btn-chat-test'), hint=$('chat-cfg-hint'), st=$('chat-cfg-status');
  const f=formAiFields();
  const browser=$('ai-route-browser')?.checked;
  const old=btn?btn.textContent:'';
  if(btn){btn.disabled=true;btn.textContent='测试中…'}
  if(hint)hint.textContent=browser?'正在从浏览器 ping 模型接口…':'正在由服务器 ping 模型接口…';
  try{
    if(browser){
      const cur=loadAiClientCfg();
      const key=(f.api_key||'').trim() || cur.api_key || '';
      const base=(f.base_url||cur.base_url||'').replace(/\/$/,'');
      const model=f.model||cur.model||'claude-opus-4-6';
      if(!base||!key)throw new Error('请先填写并保存 Base URL / Token');
      const prev=loadAiClientCfg();
      saveAiClientCfg({...prev, browser:true, base_url:base, model, api_key:key});
      try{
        const j=await browserChatCompletions(
          [{role:'user',content:'ping'}],{temperature:0,max_tokens:8});
        const reply=((((j.choices||[])[0]||{}).message||{}).content||'').trim().slice(0,40);
        if(st){st.textContent='连通 OK';st.style.color='var(--green)'}
        if(hint)hint.textContent=`浏览器直连成功 · ${model} · 回复: ${reply||'(空)'}`;
        toast('浏览器直连成功','ok');
      }finally{
        const keep=loadAiClientCfg();
        if(!(f.api_key||'').trim()) saveAiClientCfg({...keep, api_key:prev.api_key||keep.api_key});
      }
      return;
    }
    const r=await fetch('/api/chat-config/test',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});
    if(!r.ok){
      const msg=await errText(r);
      if(st){st.textContent='不通';st.style.color='var(--red)'}
      if(hint)hint.textContent='连通失败：'+msg;
      toast('连通失败','err');
      return;
    }
    const j=await r.json();
    if(st){st.textContent='连通 OK';st.style.color='var(--green)'}
    if(hint)hint.textContent=`${j.message||'连通成功'} · 回复: ${(j.reply||'').slice(0,40)}`;
    toast(j.message||'连通成功','ok');
  }catch(e){
    if(st){st.textContent='不通';st.style.color='var(--red)'}
    if(hint)hint.textContent='连通失败：'+(e.message||e);
    toast('连通失败: '+(e.message||e),'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent=old||'测试连通'}
  }
}

async function loadUsers(){
  const body=$('users-body');
  if(!body||!window.__user||window.__user.role!=='advisor')return;
  try{
    const us=await (await fetch('/api/users')).json();
    body.innerHTML=us.map(u=>`<tr>
      <td style="font-weight:600">${esc(u.username)}</td>
      <td>${u.role==='advisor'?'<span class="tag blue">导师</span>':'<span class="tag gray">学生</span>'}</td>
      <td><input class="mono" style="padding:4px 8px" id="pw-${esc(u.username)}" placeholder="新密码(≥6位)"></td>
      <td><button class="sm" onclick="resetPw('${esc(u.username)}')">重置</button></td></tr>`).join('');
  }catch(e){body.innerHTML='<tr><td colspan="4" class="empty">加载失败</td></tr>'}
}
async function resetPw(username){
  const pw=$('pw-'+username).value.trim();
  if(pw.length<6){toast('密码至少 6 位');return}
  const r=await fetch(`/api/users/${encodeURIComponent(username)}/password`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  toast(r.ok?`已重置 ${username} 的密码(其所有会话已失效)`:'重置失败: '+await errText(r));
}
async function loadSettings(){
  try{
    const s=await (await fetch('/api/settings')).json();
    SET_FIELDS.forEach(([id,k])=>{if(s[k]!=null)$(id).value=s[k]});
    $('s-welcome-refresh').checked=s.welcome_refresh!=='0';
    $('s-notify-run').checked=s.notify_run!=='0';
    $('s-notify-node').checked=s.notify_node!=='0';
  }catch(e){}
}
async function saveSettings(){
  const body={};
  SET_FIELDS.forEach(([id,k])=>body[k]=$(id).value.trim());
  body.welcome_refresh=$('s-welcome-refresh').checked?'1':'0';
  body.notify_run=$('s-notify-run').checked?'1':'0';
  body.notify_node=$('s-notify-node').checked?'1':'0';
  const r=await fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){toast('保存失败: '+await errText(r),'err');return}
  toast('已保存','ok');
  loadSettings();
}
async function loadFeishu(){
  try{
    const s=await (await fetch('/api/feishu')).json();
    if($('s-feishu-webhook'))$('s-feishu-webhook').value=s.feishu_webhook||'';
    if($('s-feishu-secret'))$('s-feishu-secret').value=s.feishu_secret||'';
    if($('s-notify-run'))$('s-notify-run').checked=s.notify_run!=='0';
    if($('s-notify-round'))$('s-notify-round').checked=s.notify_round!=='0';
    if($('s-notify-node'))$('s-notify-node').checked=s.notify_node!=='0';
  }catch(e){}
}
async function saveFeishu(){
  const body={
    feishu_webhook:($('s-feishu-webhook').value||'').trim(),
    feishu_secret:($('s-feishu-secret').value||'').trim(),
    notify_run:$('s-notify-run').checked?'1':'0',
    notify_round:($('s-notify-round')&&$('s-notify-round').checked)?'1':'0',
    notify_node:$('s-notify-node').checked?'1':'0',
  };
  const r=await fetch('/api/feishu',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){toast('保存失败: '+await errText(r),'err');return}
  toast('飞书通知已保存','ok');
  loadFeishu();
}
async function testFeishu(){
  const btn=$('btn-feishu-test'), hint=$('feishu-hint');
  if(btn){btn.disabled=true;btn.textContent='发送中…'}
  try{
    const r=await fetch('/api/feishu/test',{method:'POST'});
    const msg=r.ok?(await r.json()).message:await errText(r);
    if(hint)hint.textContent=msg;
    toast(msg, r.ok?'ok':'err');
  }catch(e){
    if(hint)hint.textContent=e.message||String(e);
    toast('发送失败','err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='发送测试'}
  }
}

/* ================= 焦点 / 最近打开 ================= */
function renderFocus(){
  const c=window.__ctx;
  const body=$('focus-body');
  if(!c.paper){body.innerHTML='<div class="empty">还没有焦点论文 — 先在下方上传并调研，保存到「调研库」后点「重选」选取</div>';return}
  const runName=c.run?c.run.name:null;
  const gpu=liveExps.find(l=>c.run&&c.run.type==='live'&&l.jsonl===c.run.jsonl);
  const metrics=focusPaperMetrics();
  const metricHtml=metrics.length
    ?`<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">${metrics.slice(0,10).map(m=>
      `<span class="chip" title="${esc([m['说明'],m['设定'],m['数据集']].filter(Boolean).join(' · ')||'日志曲线将按此对照')}">${esc(m['指标名']||'')} · ${esc(m['论文表号']||'')} · ${m['数值']??'—'}</span>`).join('')}
      ${metrics.length>10?`<span class="sub">+${metrics.length-10}</span>`:''}</div>
      <div class="sub" style="margin-top:6px">日志页曲线 / 对照页均按以上论文表格指标；换论文只能在本焦点「重选」。</div>`
    :`<div class="sub" style="margin-top:8px">尚无对照指标 — 去「调研」抽取「复现要点」后，日志曲线可选论文表中的得分 / 准确率 / 损失。</div>`;
  body.innerHTML=`<div class="kv">
    <span class="k">论文</span><span class="v" style="font-family:var(--sans);font-size:13px">${esc(c.paper.title)}</span>
    <span class="k">关联 run</span><span class="v" style="font-family:var(--sans)">${runName?esc(runName.slice(0,40)):'<span class="missing">未启动</span>'}</span>
    <span class="k">占用节点</span><span class="v" style="font-family:var(--sans)">${gpu?'GPU'+gpu.gpu:'<span class="missing">—</span>'}${gpu&&gpu.eta?` · ⏳${fmtDur(gpu.eta.eta_sec)}后完成`:''}</span></div>
    ${metricHtml}
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="sm" onclick="showPage('调研')">继续调研</button>
      <button class="sm" onclick="showPage(c.run?'日志':'执行')">${c.run?'看日志':'去执行'}</button>
      <button class="sm" onclick="showPage('对照')">指标对照</button></div>`;
  // refresh metrics if card not loaded yet
  if(!metrics.length)ensureFocusCard().then(()=>{if(focusPaperMetrics().length)renderFocus()});
}
function choosePaper(id,title){
  const p=(papers||[]).find(x=>x.id===id);
  const t=title||(p&&p.title)||('论文 #'+id);
  window.__ctx.paper={id,title:t};window.__ctx.run=null;saveCtx();closeLog();
  window.__card=null;window.__focusCardId=null;window.__paperMetric=null;
  pushRecent('paper',t);
  showPage('欢迎');
  ensureFocusCard().then(()=>{renderFocus();syncCurveFromFocus()});
  toast('已设为当前焦点：'+t.slice(0,40),'ok');
}
function pushRecent(){}

/* ================= hero 一区 3D 交互 ================= */
function initHeroArt(){
  const art=$('hero-art'), scene=$('q1-scene');
  if(!art||!scene||art.dataset.ready)return;
  art.dataset.ready='1';
  const reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const baseX=12, baseY=-18;
  let rx=baseX, ry=baseY, tx=baseX, ty=baseY, raf=0;
  const orbs=[...art.querySelectorAll('.q1-orb')];
  const apply=()=>{
    scene.style.transform=`rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg)`;
    orbs.forEach(o=>{
      const d=+o.dataset.depth||20;
      const ox=((ry-baseY)/18)*d, oy=((baseX-rx)/14)*d;
      o.style.setProperty('--ox',ox.toFixed(1)+'px');
      o.style.setProperty('--oy',oy.toFixed(1)+'px');
    });
  };
  const tick=()=>{
    rx+=(tx-rx)*.12; ry+=(ty-ry)*.12;
    apply();
    if(Math.abs(tx-rx)>0.05||Math.abs(ty-ry)>0.05)raf=requestAnimationFrame(tick);
    else raf=0;
  };
  const aim=(nx,ny)=>{
    if(reduce)return;
    tx=baseX+(-ny*10); ty=baseY+(nx*16);
    art.classList.add('is-tilting');
    if(!raf)raf=requestAnimationFrame(tick);
  };
  const reset=()=>{
    tx=baseX; ty=baseY; art.classList.remove('is-tilting','is-press');
    if(!raf)raf=requestAnimationFrame(tick);
  };
  art.addEventListener('pointermove',e=>{
    const r=art.getBoundingClientRect();
    const nx=((e.clientX-r.left)/r.width-.5)*2;
    const ny=((e.clientY-r.top)/r.height-.5)*2;
    aim(Math.max(-1,Math.min(1,nx)), Math.max(-1,Math.min(1,ny)));
  });
  art.addEventListener('pointerleave',reset);
  art.addEventListener('pointerdown',()=>art.classList.add('is-press'));
  art.addEventListener('pointerup',()=>art.classList.remove('is-press'));
  apply();
}

/* ================= 助手（服务端会话 + 只读工具） ================= */
const AGENT_PROMPTS=['哪张 GPU 空着？','现在有哪些任务在跑？','最近的训练日志有没有异常？','库里有哪些论文？'];
let agentSessions=[], agentSid=null, agentMsgs=[], agentDraft=null, agentBusy=false, agentWs=null;

function agentInputKey(e){
  if(e.key==='Enter' && !e.shiftKey){e.preventDefault();agentSend()}
}
function agentAsk(text){
  const inp=$('agent-in'); if(!inp)return;
  inp.value=text; agentSend();
}
function renderAgentSessions(){
  const box=$('agent-sessions'); if(!box)return;
  if(!agentSessions.length){box.innerHTML='<div class="empty">还没有对话</div>';return}
  const adv=window.__user&&window.__user.role==='advisor';
  box.innerHTML=agentSessions.map(s=>`<div class="agent-sess ${s.id===agentSid?'on':''}" onclick="agentSelect(${s.id})">
    <b>${esc(s.title||'新对话')}</b>
    <span>${fmtT(s.updated_at)}${adv?(' · '+esc(s.owner||'')):''}</span>
    <button type="button" class="sm ghost" onclick="agentDelete(${s.id}, event)">删除</button>
  </div>`).join('');
}
function renderAgentMsg(m){
  if(m.role==='tool'){
    const preview=(m.content||'').slice(0,800);
    return `<details class="agent-tool"><summary>${esc(m.tool_name||'tool')} · 结果</summary><pre>${esc(preview)}</pre></details>`;
  }
  if(m.role==='user') return `<div class="msg user">${esc(m.content||'')}</div>`;
  if(m.role==='assistant' && !(m.content||'').trim()) return '';
  return `<div class="msg assistant">${esc(m.content||'')}</div>`;
}
function renderDraft(d){
  const html=(d.parts||[]).map(p=>{
    if(p.type==='confirm'){
      const done=p.status!=='pending';
      const label=p.status==='approved'?'已确认':(p.status==='rejected'?'已取消':'等待确认');
      const buttons=done?'':`<div class="row">
        <button type="button" class="pri sm" data-confirm="${esc(p.id)}" data-approved="1">确认执行</button>
        <button type="button" class="sm" data-confirm="${esc(p.id)}" data-approved="0">取消</button></div>`;
      return `<div class="agent-confirm"><b>${esc(p.name||'操作')} · ${label}</b><pre>${esc(p.summary||'')}</pre>${buttons}</div>`;
    }
    if(p.type==='tool'){
      const st=p.status==='running'?'查询中…':(p.ok?'完成':'失败');
      const body=p.preview?`<pre>${esc(p.preview)}</pre>`:'';
      return `<details class="agent-tool" ${p.status==='running'?'open':''}><summary>${esc(p.name||'tool')} · ${st}</summary>${body}</details>`;
    }
    return `<div class="msg assistant">${esc(p.text||'')}${agentBusy?'<span class="agent-caret"></span>':''}</div>`;
  }).join('');
  return html || '<div class="msg assistant">思考中…<span class="agent-caret"></span></div>';
}
function renderAgent(){
  const box=$('agent-msgs'); if(!box)return;
  const sess=agentSessions.find(s=>s.id===agentSid);
  const title=$('agent-title');
  if(title) title.textContent=sess?(sess.title||'助手'):'助手';
  if(!agentMsgs.length && !agentDraft){
    box.innerHTML=`<div class="empty">问 GPU、任务、训练日志或论文。对话保存在服务器上，刷新不会丢。
      <div class="agent-prompts">${AGENT_PROMPTS.map(p=>`<button type="button" class="chip" data-q="${esc(p)}">${esc(p)}</button>`).join('')}</div></div>`;
    return;
  }
  box.innerHTML=agentMsgs.map(renderAgentMsg).join('')+(agentDraft?renderDraft(agentDraft):'');
  box.scrollTop=box.scrollHeight;
}
document.addEventListener('click',e=>{
  const b=e.target.closest && e.target.closest('#agent-msgs [data-q]');
  if(b) agentAsk(b.getAttribute('data-q'));
  const c=e.target.closest && e.target.closest('[data-confirm]');
  if(c) agentDecide(c.getAttribute('data-confirm'), c.getAttribute('data-approved')==='1');
  const f=e.target.closest && e.target.closest('#agent-files [data-file]');
  if(f) agentPreviewFile(f.getAttribute('data-file'));
  const d=e.target.closest && e.target.closest('#agent-files [data-del]');
  if(d) agentDeleteFile(d.getAttribute('data-del'));
});
async function agentLoadSessions(){
  const r=await fetch('/api/agent/sessions');
  if(!r.ok) return;
  agentSessions=await r.json();
  if(agentSid && !agentSessions.some(s=>s.id===agentSid)) agentSid=null;
  if(!agentSid && agentSessions.length) agentSid=agentSessions[0].id;
  renderAgentSessions();
}
async function agentLoadMessages(){
  if(!agentSid){agentMsgs=[];renderAgent();return}
  const r=await fetch('/api/agent/sessions/'+agentSid+'/messages');
  if(!r.ok){toast(await errText(r),'err');return}
  agentMsgs=await r.json();
  renderAgent();
}
async function agentLoadSkills(){
  const el=$('agent-skills'); if(!el)return;
  try{
    const rows=await (await fetch('/api/agent/skills')).json();
    el.textContent='技能：'+(rows.map(s=>s.name).join('、')||'无');
  }catch(e){el.textContent=''}
}
async function agentOpen(){
  if(agentBusy) return;
  try{
    const cfg=await (await fetch('/api/config')).json();
    const hint=$('agent-hint');
    if(hint && cfg && cfg.chat_configured===false)
      hint.textContent='还没配置模型 Token，请先到设置里保存。';
  }catch(e){}
  await agentLoadSkills();
  await agentLoadSessions();
  if(agentSid) await agentLoadMessages();
  else renderAgent();
  await agentLoadFiles();
}
async function agentNewSession(){
  if(agentBusy) return;
  const r=await fetch('/api/agent/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!r.ok){toast(await errText(r),'err');return}
  const s=await r.json();
  agentSid=s.id; agentMsgs=[]; agentDraft=null;
  await agentLoadSessions();
  renderAgent();
  agentClosePreview();
  await agentLoadFiles();
}
async function agentSelect(id){
  if(agentBusy){toast('请等当前回复结束');return}
  agentSid=id; agentDraft=null;
  renderAgentSessions();
  await agentLoadMessages();
  await agentLoadFiles();
  agentClosePreview();
}
async function agentDelete(id, ev){
  if(ev) ev.stopPropagation();
  if(agentBusy){toast('请等当前回复结束');return}
  const r=await fetch('/api/agent/sessions/'+id,{method:'DELETE'});
  if(!r.ok){toast(await errText(r),'err');return}
  if(agentSid===id){agentSid=null;agentMsgs=[];agentDraft=null}
  await agentLoadSessions();
  if(agentSid) await agentLoadMessages();
  else renderAgent();
  agentClosePreview();
  await agentLoadFiles();
  toast('已删除对话','ok');
}
function agentEnsureWs(){
  if(agentWs && agentWs.readyState===1 && agentWs.__ready) return Promise.resolve(agentWs);
  return new Promise((resolve,reject)=>{
    const proto=location.protocol==='https:'?'wss':'ws';
    const ws=new WebSocket(proto+'://'+location.host+'/api/agent/ws');
    const timer=setTimeout(()=>{try{ws.close()}catch(e){} reject(new Error('连接助手超时'))},8000);
    ws.onopen=()=>ws.send(JSON.stringify({auth:localStorage.wb_token||''}));
    ws.onerror=()=>{clearTimeout(timer);reject(new Error('连接助手失败'))};
    ws.onclose=()=>{
      if(agentWs===ws) agentWs=null;
      if(agentBusy && ws.__ready){
        agentBusy=false; const b=$('agent-send'); if(b) b.disabled=false;
        toast('连接中断','err');
      }
    };
    ws.onmessage=ev=>{
      let msg; try{msg=JSON.parse(ev.data)}catch(e){return}
      if(!ws.__ready){
        if(msg.event==='ready'){
          clearTimeout(timer); ws.__ready=true; agentWs=ws;
          ws.onmessage=ev2=>{let m; try{m=JSON.parse(ev2.data)}catch(err){return} agentOnEvent(m)};
          resolve(ws);
        }else if(msg.event==='error'){
          clearTimeout(timer); reject(new Error(msg.message||'未登录'));
        }
        return;
      }
      agentOnEvent(msg);
    };
  });
}
function agentAppendToken(text){
  if(!agentDraft) agentDraft={parts:[]};
  const parts=agentDraft.parts;
  const last=parts[parts.length-1];
  if(!last || last.type!=='text') parts.push({type:'text', text:''});
  parts[parts.length-1].text+=text;
  renderAgent();
}
function agentUpsertTool(ev, status){
  if(!agentDraft) agentDraft={parts:[]};
  let t=agentDraft.parts.find(p=>p.type==='tool' && p.call_id===ev.call_id);
  if(!t){
    t={type:'tool', name:ev.name, call_id:ev.call_id, status:status, preview:'', ok:true};
    agentDraft.parts.push(t);
  }
  t.status=status;
  if(ev.name) t.name=ev.name;
  if(ev.preview!=null) t.preview=ev.preview;
  if(ev.ok!=null) t.ok=ev.ok;
  renderAgent();
}
function agentFinishStream(){
  agentBusy=false; agentDraft=null;
  const b=$('agent-send'); if(b) b.disabled=false;
}
async function agentOnEvent(ev){
  if(ev.event==='token'){agentAppendToken(ev.text||'');return}
  if(ev.event==='tool_start'){agentUpsertTool(ev,'running');return}
  if(ev.event==='tool_end'){agentUpsertTool(ev,'done');return}
  if(ev.event==='confirm'){
    if(!agentDraft) agentDraft={parts:[]};
    agentDraft.parts.push({type:'confirm', id:ev.confirm_id, name:ev.name||'',
      summary:ev.summary||'', status:'pending'});
    renderAgent();
    return;
  }
  if(ev.event==='done' || ev.event==='error'){
    if(ev.event==='error' && ev.message) toast(ev.message,'err');
    agentFinishStream();
    await agentLoadSessions();
    await agentLoadMessages();
    await agentLoadFiles();
  }
}
function agentDecide(id, approved){
  const parts=(agentDraft&&agentDraft.parts)||[];
  const p=parts.find(x=>x.type==='confirm' && x.id===id && x.status==='pending');
  if(!p) return;
  p.status=approved?'approved':'rejected';
  renderAgent();
  if(agentWs && agentWs.readyState===1)
    agentWs.send(JSON.stringify({confirm_id:id, approved:!!approved}));
}
async function agentLoadFiles(){
  const box=$('agent-files'); if(!box) return;
  if(!agentSid){box.innerHTML='<div class="empty">暂无产物</div>';return}
  const r=await fetch('/api/agent/sessions/'+agentSid+'/artifacts');
  if(!r.ok){box.innerHTML='';return}
  const rows=await r.json();
  if(!rows.length){box.innerHTML='<div class="empty">这个对话还没有报告</div>';return}
  box.innerHTML=rows.map(f=>`<div class="agent-file">
    <button type="button" class="ghost" data-file="${esc(f.name)}">${esc(f.name)}</button>
    <button type="button" class="sm ghost" data-del="${esc(f.name)}">删除</button></div>`).join('');
}
async function agentPreviewFile(name){
  if(!agentSid||!name) return;
  const r=await fetch('/api/agent/sessions/'+agentSid+'/artifacts/'+encodeURIComponent(name));
  if(!r.ok){toast(await errText(r),'err');return}
  const j=await r.json();
  const box=$('agent-preview'); if(!box) return;
  box.hidden=false;
  box.innerHTML=`<div class="agent-preview-h"><b>${esc(j.name||name)}</b>
    <a href="/api/agent/sessions/${agentSid}/artifacts/${encodeURIComponent(j.name||name)}?download=1">下载</a>
    <button type="button" class="sm ghost" onclick="agentClosePreview()">关闭</button></div>
    <pre>${esc(j.content||'')}</pre>`;
}
function agentClosePreview(){
  const box=$('agent-preview'); if(!box) return;
  box.hidden=true; box.innerHTML='';
}
async function agentDeleteFile(name){
  if(!agentSid||!name) return;
  const r=await fetch('/api/agent/sessions/'+agentSid+'/artifacts/'+encodeURIComponent(name),{method:'DELETE'});
  if(!r.ok){toast(await errText(r),'err');return}
  agentClosePreview();
  await agentLoadFiles();
  toast('已删除报告','ok');
}
async function agentSend(){
  if(agentBusy) return;
  const inp=$('agent-in'); if(!inp) return;
  const text=(inp.value||'').trim(); if(!text) return;
  inp.value='';
  if(!agentSid){
    const r=await fetch('/api/agent/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(!r.ok){toast(await errText(r),'err');return}
    const s=await r.json();
    agentSid=s.id;
    await agentLoadSessions();
  }
  agentMsgs.push({role:'user', content:text});
  agentDraft={parts:[]};
  agentBusy=true;
  $('agent-send').disabled=true;
  renderAgent();
  try{
    const ws=await agentEnsureWs();
    ws.send(JSON.stringify({session_id:agentSid, content:text}));
  }catch(e){
    agentFinishStream();
    agentDraft=null;
    toast(e.message||'发送失败','err');
    renderAgent();
  }
}

let agentJobId=null, agentCaseId=null, agentCases=[];
function agentTab(name){
  const review=name==='review';
  const chat=$('agent-chat-wrap'), pane=$('agent-review');
  if(chat) chat.classList.toggle('off', review);
  if(pane) pane.classList.toggle('on', review);
  const a=$('agent-tab-chat'), b=$('agent-tab-review');
  if(a) a.classList.toggle('on', !review);
  if(b) b.classList.toggle('on', review);
  if(review) agentLoadReview();
}
async function agentLoadReview(){
  const jr=await fetch('/api/agent/jobs');
  if(jr.ok){
    const jobs=await jr.json();
    const job=jobs[0];
    if(job){
      agentJobId=job.id;
      $('job-enabled').checked=!!job.enabled;
      $('job-cron').value=job.cron||'0 22 * * *';
      $('job-tz').value=job.timezone||'Asia/Shanghai';
      $('job-last').textContent=job.last_message?((job.last_run_at?fmtT(job.last_run_at)+' · ':'')+job.last_message):'还没跑过';
    }
  }
  const pr=await fetch('/api/agent/profile');
  const profile=pr.ok?await pr.json():{prompt:'', skills:null};
  $('profile-prompt').value=profile.prompt||'';
  let skills=[];
  try{skills=await (await fetch('/api/agent/skills')).json()}catch(e){skills=[]}
  const allow=profile.skills;
  $('profile-skills').innerHTML=(skills||[]).map(s=>{
    const on=allow==null || allow.includes(s.name);
    return `<label class="check"><input type="checkbox" data-skill="${esc(s.name)}" ${on?'checked':''}>${esc(s.name)}</label>`;
  }).join('');
  await agentLoadCases();
}
async function agentSaveJob(){
  if(!agentJobId){toast('还没有复盘计划','err');return}
  const r=await fetch('/api/agent/jobs/'+agentJobId,{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled:$('job-enabled').checked, cron:$('job-cron').value.trim(), timezone:$('job-tz').value.trim()})});
  if(!r.ok){toast(await errText(r),'err');return}
  toast('复盘计划已保存','ok');
  await agentLoadReview();
}
async function agentRunJob(){
  if(!agentJobId){toast('还没有复盘计划','err');return}
  const btn=$('job-run'); if(btn) btn.disabled=true;
  $('job-last').textContent='复盘中…';
  try{
    const r=await fetch('/api/agent/jobs/'+agentJobId+'/run',{method:'POST'});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){toast(j.detail||'复盘失败','err');return}
    toast(j.message||'复盘完成', j.ok?'ok':'err');
    if(j.session_id){agentSid=j.session_id; await agentLoadSessions(); await agentLoadMessages(); await agentLoadFiles()}
  }finally{
    if(btn) btn.disabled=false;
    await agentLoadReview();
  }
}
function agentProfileSkills(){
  return [...document.querySelectorAll('#profile-skills [data-skill]:checked')].map(el=>el.getAttribute('data-skill'));
}
async function agentSaveProfile(){
  const r=await fetch('/api/agent/profile',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prompt:$('profile-prompt').value, skills:agentProfileSkills()})});
  if(!r.ok){toast(await errText(r),'err');return}
  toast('个人说明已保存','ok');
}
async function agentLoadCases(){
  const r=await fetch('/api/agent/cases');
  agentCases=r.ok?await r.json():[];
  const box=$('case-list'); if(!box) return;
  if(!agentCases.length){box.innerHTML='<div class="empty">还没有案例</div>';return}
  box.innerHTML=agentCases.map(c=>`<div class="case-row">
    <input type="checkbox" data-case="${c.id}" style="width:auto">
    <b>${esc(c.title)}</b>
    <span class="tag ${c.status==='resolved'?'ok':'warn'}">${c.status==='resolved'?'已解决':'处理中'}</span>
    <button type="button" class="sm ghost" onclick="agentEditCase(${c.id})">编辑</button>
    <button type="button" class="sm ghost" onclick="agentSplitCase(${c.id})">拆分</button>
    <button type="button" class="sm ghost" onclick="agentDeleteCase(${c.id})">删除</button>
  </div>`).join('');
}
function agentFillCase(c){
  agentCaseId=c?c.id:null;
  $('case-title').value=c?(c.title||''):'';
  $('case-exp').value=c?(c.experiment||''):'';
  $('case-sym').value=c?(c.symptom||''):'';
  $('case-cause').value=c?(c.cause||''):'';
  $('case-act').value=c?(c.action||''):'';
  $('case-status').value=c&&c.status==='resolved'?'resolved':'open';
}
function agentNewCase(){agentFillCase(null)}
function agentEditCase(id){agentFillCase(agentCases.find(c=>c.id===id)||null)}
function agentCaseBody(){
  return {title:$('case-title').value, experiment:$('case-exp').value, symptom:$('case-sym').value,
    cause:$('case-cause').value, action:$('case-act').value, status:$('case-status').value, source_file:''};
}
async function agentSaveCase(){
  const url=agentCaseId?('/api/agent/cases/'+agentCaseId):'/api/agent/cases';
  const r=await fetch(url,{method:agentCaseId?'PUT':'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(agentCaseBody())});
  if(!r.ok){toast(await errText(r),'err');return}
  toast('案例已保存','ok');
  agentCaseId=(await r.json()).id;
  await agentLoadCases();
}
async function agentDeleteCase(id){
  const r=await fetch('/api/agent/cases/'+id,{method:'DELETE'});
  if(!r.ok){toast(await errText(r),'err');return}
  if(agentCaseId===id) agentNewCase();
  await agentLoadCases();
}
async function agentSplitCase(id){
  const r=await fetch('/api/agent/cases/'+id+'/split',{method:'POST'});
  if(!r.ok){toast(await errText(r),'err');return}
  toast('已拆成两条','ok');
  await agentLoadCases();
}
async function agentMergeCases(){
  const ids=[...document.querySelectorAll('#case-list [data-case]:checked')].map(el=>+el.getAttribute('data-case'));
  if(ids.length!==2){toast('请勾选两条案例再合并');return}
  const r=await fetch('/api/agent/cases/merge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ids})});
  if(!r.ok){toast(await errText(r),'err');return}
  toast('已合并','ok');
  agentNewCase();
  await agentLoadCases();
}

/* ================= boot ================= */
let __booted=false;
function bootStart(){
  if(__booted)return;__booted=true;
  renderExtractSettings();
  startGpuStream();
  renderFocus();renderChat();
  startMonTimer();
  initHeroArt();
  loadActivity();setInterval(loadActivity,300000);
  loadPapers().then(()=>{
    if(window.__ctx.paper){
      renderPaper();
      ensureFocusCard().then(()=>renderFocus());
    }
  });
}
(async()=>{
  try{
    const r=await fetch('/api/me');
    if(r.status===401){showLogin();return}
    window.__user=await r.json();
    $('login-page').classList.remove('on');
    applyUser();
    bootStart();
  }catch(e){showLogin()}
})();
