/* 科研工作台 v3.0 — 论文复现流水线前端逻辑 */
'use strict';
const $=id=>document.getElementById(id);
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
function applyUser(){
  const u=window.__user;
  if(!u)return;
  document.body.classList.toggle('is-advisor',u.role==='advisor');
  $('avatar').textContent=(u.username[0]||'?').toUpperCase();
  $('user-menu-name').textContent=u.username;
  $('user-menu-role').textContent=u.role==='advisor'?'导师 · 管理员':'学生';
  document.querySelector('.nav-item[data-page="设置"]').style.display=
    u.role==='advisor'?'':'none';
  if(u.role==='advisor'){
    $('owner-filter').innerHTML=['','zhouyan','lihan','jiyang'].map(o=>
      `<span class="chip ${(__ownerFilter===o)?'on':''}" onclick="setOwnerFilter('${o}')">`
      +(o||'全部')+'</span>').join('');
  }
}
let __ownerFilter='';
function setOwnerFilter(o){__ownerFilter=o;applyUser();loadTasks()}

/* ================= 路由与上下文 ================= */
const PAGES=['欢迎','调研','执行','日志','对照','设置'];
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
  if(name==='设置'){loadSettings();loadUsers()}
  if(name==='欢迎')renderFocus();
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
function renderGpu(snap){
  window.__gpuSnap=snap;
  const byPid=window.__liveByPid||{};
  const tb=$('gpu-mini');
  if(snap.error){tb.innerHTML=`<tr><td colspan="6" class="empty">${esc(snap.error)}</td></tr>`;return}
  let idleN=0,sharedN=0;
  tb.innerHTML=snap.gpus.map(g=>{
    const idle=!g.error&&g.util<10&&g.mem_used<2000;
    const shared=!idle&&!g.error&&g.util<10&&(g.mem_total-g.mem_used)>=(snap.idle_rule.shared_spare_mb||12000);
    if(idle)idleN++;else if(shared)sharedN++;
    const mine=g.procs.find(p=>p.user===snap.me);
    const st=g.error?'<span class="tag err">异常</span>':(mine?'<span class="tag blue">训练中</span>':(idle?'<span class="tag ok">空闲</span>':(shared?'<span class="tag warn">共用</span>':'<span class="tag gray">占用</span>')));
    const exp=mine?(byPid[mine.pid]?esc(shortCmd((byPid[mine.pid].name||'').split(' / ')[0])):esc(mine.user)):(g.procs[0]?esc(g.procs[0].user):'—');
    const eta=mine&&byPid[mine.pid]&&byPid[mine.pid].eta;
    return `<tr>
      <td><b>GPU ${g.index}</b></td><td class="num">${GPU_NAME}</td>
      <td class="num">${g.error?'—':(g.mem_used/1024).toFixed(1)+' / '+(g.mem_total/1024).toFixed(0)+' G'}</td>
      <td class="num">${g.error?'—':g.util+'%'}</td><td>${st}</td>
      <td style="font-size:12px;color:var(--dim)">${exp}${eta?`<br><span style="color:var(--blue)">⏳ r${eta.round+1}/${eta.total} · ${fmtDur(eta.eta_sec)}后</span>`:''}</td></tr>`;
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
function renderRunList(){
  const box=$('run-list');
  const items=[
    ...liveExps.map(l=>({key:'l'+l.pid,type:'live',name:l.name,gpu:l.gpu,live:l,
      status:'running',run:l.run||0,owner:l.owner})),
    ...(window.__tasks||[]).filter(t=>['running','pending','done','failed'].includes(t.status))
      .map(t=>({key:'t'+t.id,type:'task',name:t.name,gpu:t.gpu_assigned,t,owner:t.owner}))
  ];
  if(!items.length){box.innerHTML='<div class="empty">暂无实验运行</div>';return}
  const stTag=s=>s==='running'?'<span class="dot run"></span>':s==='pending'?'<span class="dot"></span>':s==='done'?'<span class="dot ok"></span>':'<span class="dot err"></span>';
  box.innerHTML=items.slice(0,30).map(r=>{
    const sel=window.__ctx.run&&window.__ctx.run.key===r.key;
    const st=r.status||(r.t?r.t.status:'running');
    const eta=r.live&&r.live.eta;
    const ownerTag=(window.__user&&window.__user.role==='advisor'&&r.owner)
      ?` · ${esc(r.owner)}`:'';
    return `<div class="run-item ${sel?'sel':''}" onclick="selectRun('${r.type}','${r.key}')">
      <div class="t">${stTag(st)}${esc((r.name||'').slice(0,22))}</div>
      <div class="s">GPU${r.gpu||'—'}${eta?` · r${eta.round+1}/${eta.total} · ${fmtDur(eta.eta_sec)}`:''}${r.type==='task'?' · 工作台':''}${r.status==='pending'?' · 排队中':''}${ownerTag}</div>
    </div>`}).join('');
}
function selectRun(type,key){
  if(type==='live'){
    const l=liveExps.find(x=>'l'+x.pid===key);if(!l)return;
    window.__ctx.run={type:'live',key,jsonl:l.jsonl,log:l.log,name:l.name,pid:l.pid};
  }else{
    const t=(window.__tasks||[]).find(x=>'t'+x.id===key);if(!t)return;
    window.__ctx.run={type:'task',key,id:t.id,name:t.name,log_file:t.log_file,status:t.status};
  }
  saveCtx();renderRunList();renderMyExps();
  showPage('日志');
  $('arch-card').style.display='block';
  selFields=new Set(['task']);cmpMetric='task';$('chart-mode').value='vl';
  refreshChart();
  startRunLog();
  pushRecent('run',window.__ctx.run.name);
}
function renderMyExps(){
  const tb=$('myexps');
  const rows=[
    ...liveExps.map(l=>({name:l.name,gpu:l.gpu,eta:l.eta,type:'live',key:'l'+l.pid,pid:l.pid})),
    ...(window.__tasks||[]).filter(t=>['running','pending'].includes(t.status))
      .map(t=>({name:t.name,gpu:t.gpu_assigned,type:'task',key:'t'+t.id,id:t.id,status:t.status}))
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
  try{await Promise.all([loadTasks(),loadLiveExps()])}catch(e){}
  ticking=false;
}

/* ================= tasks ================= */
async function loadTasks(){
  try{
    const q=__ownerFilter?`?owner=${encodeURIComponent(__ownerFilter)}`:'';
    const ts=await (await fetch('/api/tasks'+q)).json();
    window.__tasks=ts;
    renderRunList();renderMyExps();
    const body=$('task-body');
    if(!ts.length){body.innerHTML='<tr><td colspan="8" class="empty">无任务</td></tr>';}
    else body.innerHTML=ts.map(t=>{
      const act=t.status==='running'
        ?`<button class="sm" onclick="openLog(${t.id})">日志</button> <button class="sm danger" onclick="stopTask(${t.id})">停止</button>`
        :t.status==='pending'?`<button class="sm danger" onclick="cancelTask(${t.id})">取消</button>`
        :(t.ai_summary?`<button class="sm" onclick="showAI(${t.id})">复盘</button> `:'')+(t.log_file?`<button class="sm" onclick="openLog(${t.id})">日志</button>`:'');
      const tag=t.status==='done'?'ok':(t.status==='running'?'blue':(t.status==='pending'?'warn':(t.status==='failed'||t.status==='killed'?'err':'gray')));
      return `<tr><td class="num">${t.id}</td><td><span class="tag ${tag}">${t.status}</span></td>
        <td class="num col-owner" style="font-size:11px">${esc(t.owner||'')}</td>
        <td style="font-size:12px">${esc(t.name)}</td>
        <td class="num" style="font-size:11px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t.command)}">${esc(t.command)}</td>
        <td class="num">${t.gpu_assigned||(t.gpus_pinned?'锁'+t.gpus_pinned:'需'+t.gpus_needed)}</td>
        <td class="num">${t.exit_code==null?'':t.exit_code}</td><td>${act}</td></tr>`}).join('');
    $('task-summary').textContent=`运行 ${ts.filter(t=>t.status==='running').length} · 排队 ${ts.filter(t=>t.status==='pending').length}`;
  }catch(e){}
}
async function submitTask(){
  const name=$('f-name').value.trim(),command=$('f-cmd').value.trim();
  const pin=$('f-pin').value.trim()||null,dir=$('f-dir').value.trim()||null;
  const chain=$('f-chain').value.trim();
  if(!name){alert('任务名必填');return}
  let r;
  if(chain){
    const cmds=chain.split('\n').map(s=>s.trim()).filter(Boolean);
    if(command)cmds.unshift(command);
    r=await fetch('/api/tasks/chain',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name_prefix:name,commands:cmds,gpus_needed:+$('f-gpus').value,workdir:dir,gpus_pinned:pin})});
  }else{
    if(!command){alert('命令必填(或填写任务链)');return}
    r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name,command,gpus_needed:+$('f-gpus').value,workdir:dir,gpus_pinned:pin})});
  }
  if(!r.ok){alert(await errText(r));return}
  $('f-name').value='';$('f-cmd').value='';$('f-chain').value='';loadTasks();
}
async function errText(r){try{const j=await r.json();return j.detail||JSON.stringify(j)}catch(e){return r.statusText}}
async function stopTask(id){if(confirm('停止任务 #'+id+'?')){await fetch(`/api/tasks/${id}/stop`,{method:'POST'});setTimeout(loadTasks,500)}}
async function cancelTask(id){await fetch(`/api/tasks/${id}/cancel`,{method:'POST'});loadTasks()}
async function stopSelRun(){
  const r=window.__ctx.run;if(!r||r.type!=='task')return;
  await fetch(`/api/tasks/${r.id}/stop`,{method:'POST'});loadTasks();
}
function showAI(id){
  const t=(window.__tasks||[]).find(x=>x.id===id);
  if(!t||!t.ai_summary){alert('复盘尚未生成');return}
  openModalText(`AI 复盘 · #${t.id} ${t.name}`,`exit=${t.exit_code}`,t.ai_summary);
}

/* ================= 曲线 ================= */
let chart=null,selFields=new Set(['task']),cmpMetric='task',lastSeries=null;
const chartColors=['#2F6FED','#22C55E','#B45309','#DC2626','#7C3AED','#0D9488','#DB2777','#65A30D'];
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
  drawDatasets(ds);
}
async function refreshChart(){
  const run=window.__ctx.run;if(!run)return;
  const mode=$('chart-mode').value;
  const url=run.type==='live'
    ?`/api/series?jsonl=${encodeURIComponent(run.jsonl)}${mode?'&mode='+mode:''}`
    :null;
  if(!url){$('curve-sub').textContent='工作台任务曲线需 JSONL — 选择左侧自动发现的实验可看曲线';return}
  const r=await (await fetch(url)).json();
  lastSeries=r;drawChart(r);
  $('curve-sub').textContent=run.name.slice(0,40)+(run.ended?' (已结束)':'');
}
function archPick(field,mode){
  selFields=new Set([field]);cmpMetric=field;
  if(mode!==null&&mode!==undefined)$('chart-mode').value=mode;
  refreshChart();
}
async function compareAll(){
  const ds=[];
  if(cmpMetric.startsWith('score:')){
    const md=cmpMetric.slice(6);
    for(const e of liveExps){
      try{
        const m=await (await fetch(`/api/metrics?jsonl=${encodeURIComponent(e.jsonl)}${e.log?'&log='+encodeURIComponent(e.log):''}`)).json();
        const s=(m.scores||{})[md];
        if(s&&s.values.length)ds.push({label:`${e.name.slice(0,26)} · VQAScore`,data:s.values.map((v,i)=>({x:s.rounds[i],y:v})),
          borderColor:chartColors[ds.length%8],backgroundColor:chartColors[ds.length%8],pointRadius:3,borderWidth:2,tension:.25});
      }catch(err){}
    }
  }else{
    const field=cmpMetric||[...selFields][0]||'task';
    const mode=$('chart-mode').value||'vl';
    for(const e of liveExps){
      try{
        const r=await (await fetch(`/api/series?jsonl=${encodeURIComponent(e.jsonl)}&mode=${mode}`)).json();
        const vals=(r.data||{})[field]||[];
        if(vals.length)ds.push({label:`${e.name.slice(0,26)} · ${field}[${mode}]`,data:vals,
          borderColor:chartColors[ds.length%8],backgroundColor:chartColors[ds.length%8],pointRadius:0,borderWidth:1.6,tension:.2});
      }catch(err){}
    }
  }
  if(!ds.length){alert('没有可比数据');return}
  drawDatasets(ds);
}
async function pickScore(){
  cmpMetric='score:vl';compareAll();
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
  $('log-stop').style.display=run.type==='task'&&((window.__tasks||[]).find(t=>t.id===run.id)||{}).status==='running'?'':'none';
  if(run.type==='live'){
    if(!run.log){box.textContent='该实验未找到文本日志(可用监视行「问AI」分析)';return}
    logEs=new EventSource('/api/logs/attach?path='+encodeURIComponent(run.log));
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
function openModalText(title,meta,content){
  $('modal-title').textContent=title;$('modal-meta').textContent=meta||'';
  $('modal-log').textContent=content;
  $('modal').classList.add('on');
  stopLogPoll();if(logEs){logEs.close();logEs=null}
}

/* ================= 论文 ================= */
let papers=[];
function focusArxiv(){$('e-arxiv').focus();uzOn('uz-arxiv');clearPdf()}
function focusText(){$('e-text').focus();uzOn('uz-text');clearPdf()}
function uzOn(id){document.querySelectorAll('.uz').forEach(b=>b.classList.remove('on'));$(id).classList.add('on')}
function uploadPdf(inp){
  const f=inp.files&&inp.files[0];if(!f)return;
  uzOn('uz-pdf');
  $('uz-pdf').lastChild.textContent='已选 '+f.name.slice(0,26)+(f.name.length>26?'…':'');
}
function clearPdf(){$('pdf-file').value='';$('uz-pdf').lastChild.textContent='上传 PDF'}
async function loadPapers(){
  try{papers=await (await fetch('/api/papers')).json()}catch(e){papers=[]}
}
function inputMode(){
  if($('pdf-file').files.length)return 'pdf';
  if($('e-arxiv').value.trim())return 'arxiv';
  if($('e-text').value.trim().length>=50)return 'text';
  return null;
}
async function startResearch(){
  const mode=inputMode();
  if(!mode){alert('请上传 PDF、填写 arXiv 链接或粘贴至少 50 字文本');return}
  let r;
  if(mode==='pdf'){
    const fd=new FormData();fd.append('file',$('pdf-file').files[0]);
    if($('e-arxiv').value.trim())fd.append('title',$('e-arxiv').value.trim().slice(0,80));
    r=await fetch('/api/papers/pdf',{method:'POST',body:fd});
  }else{
    r=await fetch('/api/papers',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(mode==='arxiv'?{arxiv_url:$('e-arxiv').value.trim()}:{text:$('e-text').value.trim()})});
  }
  if(!r.ok){alert(await errText(r));return}
  const p=await r.json();
  window.__ctx.paper={id:p.id,title:p.title};
  window.__ctx.run=null;saveCtx();
  pushRecent('paper',p.title);
  showPage('调研');
  await extractPaper();
}
async function extractPaper(force){
  const p=window.__ctx.paper;if(!p)return;
  if(!force){
    const full=await (await fetch(`/api/papers/${p.id}`)).json();
    if(full.card_json){renderCard(JSON.parse(full.card_json));return}
  }
  $('paper-status').textContent='正在抽取（GLM 阅读 Token 中，约 10-60 秒）…';
  const r=await fetch(`/api/papers/${p.id}/extract`,{method:'POST'});
  if(!r.ok){$('paper-status').textContent='抽取失败: '+await errText(r);return}
  $('paper-status').textContent='抽取完成 · '+new Date().toLocaleTimeString();
  renderCard(await r.json());
}
const MISS='未在论文中找到';
const vOr=x=>(x==null||x===''||x===MISS)?`<span class="missing">${MISS}</span>`:esc(x);
function renderPaper(){
  const p=window.__ctx.paper;
  if(!p){$('paper-empty').style.display='block';$('paper-wrap').style.display='none';return}
  $('paper-empty').style.display='none';$('paper-wrap').style.display='block';
  $('paper-title').textContent=p.title||'论文';
  $('paper-src').textContent=`来源 ${p.source_type||''} ${p.source_ref||''}`;
  extractPaper();
}
function cardSection(title,inner){return `<div class="card"><div class="card-h"><div class="ic">§</div><b>${title}</b></div>${inner}</div>`}
function renderCard(card){
  window.__card=card;
  const kv=o=>'<div class="kv">'+Object.entries(o||{}).map(([k,v])=>
    `<span class="k">${esc(k)}</span><span class="v">${vOr(Array.isArray(v)?v.join('；'):v)}</span>`).join('')+'</div>';
  const tbl=(rows,cols)=>rows&&rows.length?'<div style="overflow-x:auto"><table><thead><tr>'+
    cols.map(c=>`<th>${c[0]}</th>`).join('')+'</tr></thead><tbody>'+
    rows.map(r=>'<tr>'+cols.map(c=>`<td class="num" style="font-size:12px">${vOr(r[c[1]])}</td>`).join('')+'</tr>').join('')+
    '</tbody></table></div>':`<div class="empty">${MISS}</div>`;
  let html='';
  html+=cardSection('① 文献信息',kv(card['文献信息']));
  html+=cardSection('② 代码与资源',kv(card['代码与资源']));
  const arch=card['实验架构']||{};
  const nodes=(arch['节点']||[]).filter(n=>n&&n!==MISS);
  html+=cardSection('③ 实验架构',
    `<p style="font-size:13px;margin-bottom:10px">${vOr(arch['描述'])}</p>`+
    (nodes.length?'<div class="roles">'+nodes.map(n=>
      `<span class="chip" title="${esc(n['连接']||'')}">${esc(n['名称']||'?')}<span style="color:var(--faint);font-size:10px"> · ${esc(n['类型']||'')}</span></span>`).join('')+'</div>':`<div class="empty">${MISS}</div>`));
  html+=cardSection('④ 参数设计',tbl(card['参数设计'],[['参数','参数'],['值','值']]));
  html+=cardSection('⑤ 数据集',tbl(card['数据集'],[['名称','名称'],['划分','划分'],['官方下载','官方下载'],['许可','许可']]));
  const cl=card['结论与局限']||{};
  const lst=a=>((a||[]).filter(x=>x&&x!==MISS)).map(x=>`<li style="margin:4px 0">${esc(x)}</li>`).join('')||`<li class="missing">${MISS}</li>`;
  html+=cardSection('⑥ 结论与局限',
    `<b style="font-size:13px">结论</b><ul style="margin:6px 0 12px 20px">${lst(cl['结论'])}</ul><b style="font-size:13px">局限</b><ul style="margin:6px 0 0 20px">${lst(cl['局限'])}</ul>`);
  const rp=card['复现要点']||{};
  html+=cardSection('⑦ 复现要点（供执行页使用）',
    kv({'最小可跑通命令':rp['最小可跑通命令'],'必填目录':rp['必填目录']})+
    (rp['对照指标']&&rp['对照指标'].length?tbl(rp['对照指标'],[['指标名','指标名'],['论文表号','论文表号'],['数值','数值'],['方向','越高越好']]):''));
  $('paper-cards').innerHTML=html;
}
function exportCard(){
  const p=window.__ctx.paper;if(!p)return;
  window.open(`/api/papers/${p.id}`,'_blank');
}
function pickPaper(){
  loadPapers().then(()=>{
    if(!papers.length){alert('还没有任何论文');return}
    openModalText('选择论文','',papers.map(p=>
      `<div class="act-item" style="cursor:pointer" onclick="choosePaper(${p.id},'${esc(p.title).replace(/'/g,'')}')">
        <span class="nm">${esc(p.title)}</span><span class="rng">${p.source_type} ${p.source_ref||''}${(window.__user&&window.__user.role==='advisor'&&p.owner)?' · '+esc(p.owner):''}</span></div>`).join(''));
  });
}
function choosePaper(id,title){
  window.__ctx.paper={id,title};window.__ctx.run=null;saveCtx();closeLog();
  pushRecent('paper',title);
  showPage('调研');
}

/* ================= 执行 ================= */
function renderExecPrefill(){
  const c=window.__card;
  if(c&&c['复现要点']){
    const rp=c['复现要点'];
    if(!$('x-cmd').value)$('x-cmd').value=String(rp['最小可跑通命令']||'').slice(0,200)||'';
    if(!$('x-dir').value)$('x-dir').value=String(rp['必填目录']||'').slice(0,120)||'';
  }
  renderPreflight();
}
async function dirCheck(){
  const p=$('x-dir').value.trim();
  if(!p){alert('填写服务器目录');return}
  const r=await fetch('/api/dircheck',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})});
  const d=await r.json();
  const box=$('dircheck-result');
  if(!d.exists){box.innerHTML=`<span class="tag err">目录不存在</span> <span class="num" style="font-size:11px">${esc(d.path)}</span>`;renderPreflight();return}
  box.innerHTML=`<span class="tag ${d.writable?'ok':'err'}">${d.writable?'可写 ✓':'不可写'}</span>
    <span class="tag ${d.has_code?'blue':'warn'}">${d.has_code?'识别到代码':'未见代码/配置'}</span>
    <div style="margin-top:8px;font:11px var(--mono);color:var(--dim);word-break:break-all">${(d.entries||[]).map(esc).join(' · ')}</div>`;
  renderPreflight(d);
}
async function renderPreflight(dirInfo){
  const snap=window.__gpuSnap||{};
  const gpus=(snap.gpus||[]).filter(g=>!g.error&&g.util<10&&g.mem_used<2000).map(g=>g.index);
  const pin=$('x-gpus').value.trim();
  const items=[];
  items.push(dirInfo?(dirInfo.exists?(dirInfo.writable?['ok','服务器目录 可写']:['err','目录不可写']):['err','目录不存在']):['warn','目录未预检']);
  items.push(['ok',`空闲卡 ${gpus.length?gpus.join(','):0} 张`]);
  if(pin)items.push(gpus.every(i=>pin.split(',').map(Number).includes(i))?['ok',`指定卡 ${pin} 当前空闲`]:['warn',`指定卡 ${pin} 当前不空闲(将排队等待)`]);
  if(!window.__ctx.paper)items.push(['warn','未选论文(对照页将不可用)']);
  $('preflight').innerHTML=items.map(i=>`<span class="tag ${i[0]}">${i[1]}</span>`).join('');
}
async function launchRun(){
  const dir=$('x-dir').value.trim(),cmd=$('x-cmd').value.trim(),name=$('x-name').value.trim();
  const pin=$('x-gpus').value.trim()||null;
  if(!dir||!cmd){alert('目录和命令必填');return}
  const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name||('run-'+Date.now().toString(36)),command:cmd,gpus_needed:pin?pin.split(',').length:1,workdir:dir,gpus_pinned:pin})});
  if(!r.ok){alert(await errText(r));return}
  const t=await r.json();
  window.__ctx.run={type:'task',key:'t'+t.id,id:t.id,name:t.name,log_file:null,status:'pending'};
  saveCtx();pushRecent('run',t.name);
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
  const c=window.__cmpLast;if(!c){alert('先完成一次对照');return}
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
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({messages:chatHist,include_gpu:$('c-gpu').checked,...expBody})});
    const j=await r.json();
    if(!r.ok)throw new Error(j.detail||r.statusText);
    chatHist.push({role:'assistant',content:j.reply});
  }catch(e){chatHist.push({role:'assistant',content:'错误: '+e.message})}
  localStorage.setItem('wb3_chat',JSON.stringify(chatHist));
  renderChat();
}

/* ================= 设置 ================= */
const SET_FIELDS=[['s-gpus','default_gpus'],['s-visible','visible_gpus'],['s-coderoot','code_root'],
  ['s-dataroot','data_root'],['s-outroot','out_root'],['s-reached','reached_pct'],['s-near','near_pct'],
  ['s-env','default_env'],['s-envvars','env_vars'],['s-perm-view','perm_view'],['s-perm-run','perm_run']];
async function loadUsers(){
  const body=$('users-body');
  if(!body||!window.__user||window.__user.role!=='advisor')return;
  try{
    const us=await (await fetch('/api/users')).json();
    body.innerHTML=us.map(u=>`<tr>
      <td style="font-weight:600">${esc(u.username)}</td>
      <td>${u.role==='advisor'?'<span class="tag blue">导师</span>':'<span class="tag gray">学生</span>'}</td>
      <td><input class="mono" style="padding:4px 8px" id="pw-${esc(u.username)}" placeholder="新密码(≥4位)"></td>
      <td><button class="sm" onclick="resetPw('${esc(u.username)}')">重置</button></td></tr>`).join('');
  }catch(e){body.innerHTML='<tr><td colspan="4" class="empty">加载失败</td></tr>'}
}
async function resetPw(username){
  const pw=$('pw-'+username).value.trim();
  if(pw.length<4){alert('密码至少 4 位');return}
  const r=await fetch(`/api/users/${encodeURIComponent(username)}/password`,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  alert(r.ok?`已重置 ${username} 的密码(其所有会话已失效)`:'重置失败: '+await errText(r));
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
  alert(r.ok?'已保存':'保存失败');
}

/* ================= 焦点 / 最近打开 ================= */
function renderFocus(){
  const c=window.__ctx;
  const body=$('focus-body');
  if(!c.paper){body.innerHTML='<div class="empty">还没有选择论文 — 从下方入口开始</div>';return}
  const runName=c.run?c.run.name:null;
  const gpu=liveExps.find(l=>c.run&&c.run.type==='live'&&l.jsonl===c.run.jsonl);
  body.innerHTML=`<div class="kv">
    <span class="k">论文</span><span class="v" style="font-family:var(--sans);font-size:13px">${esc(c.paper.title)}</span>
    <span class="k">关联 run</span><span class="v" style="font-family:var(--sans)">${runName?esc(runName.slice(0,40)):'<span class="missing">未启动</span>'}</span>
    <span class="k">占用节点</span><span class="v" style="font-family:var(--sans)">${gpu?'GPU'+gpu.gpu:'<span class="missing">—</span>'}${gpu&&gpu.eta?` · ⏳${fmtDur(gpu.eta.eta_sec)}后完成`:''}</span></div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button class="sm" onclick="showPage('调研')">继续调研</button>
      <button class="sm" onclick="showPage(c.run?'日志':'执行')">${c.run?'看日志':'去执行'}</button></div>`;
}
function pushRecent(type,name){
  try{
    const rec=JSON.parse(localStorage.getItem('wb3_recent')||'[]');
    rec.unshift({type,name,time:Date.now()});
    localStorage.setItem('wb3_recent',JSON.stringify(rec.slice(0,8)));
  }catch(e){}
  renderRecent();
}
function renderRecent(){
  try{
    const rec=JSON.parse(localStorage.getItem('wb3_recent')||'[]');
    if(!rec.length)return;
    $('recent-list').innerHTML=rec.map(r=>`<div class="act-item">
      <span class="nm">${r.type==='paper'?'📄':r.type==='run'?'▶':'📁'} ${esc(r.name.slice(0,24))}</span>
      <span class="rng">${new Date(r.time).toLocaleDateString()}</span></div>`).join('');
  }catch(e){}
}

/* ================= boot ================= */
let __booted=false;
function bootStart(){
  if(__booted)return;__booted=true;
  startGpuStream();
  renderFocus();renderRecent();renderChat();
  startMonTimer();
  loadActivity();setInterval(loadActivity,300000);
  loadPapers().then(()=>{if(window.__ctx.paper)renderPaper()});
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
