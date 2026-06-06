"""
daemon_page.py - Self-contained Daemons + Waste page served at /daemons.

Kept separate from the 1300-line vendor dashboard so the custom logic stays legible
and the vendor token charts remain untouched. Talks to /api/daemons (report) and
/api/prompt (server-side repair-prompt generation reusing promptgen.py).
"""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daemon Health + Waste</title>
<style>
  :root {
    --bg:#0f1115; --card:#171a21; --border:#262b36; --text:#e6e9ef;
    --muted:#8b93a7; --accent:#5b9bd5; --waste:#f0a020; --rogue:#f87171;
    --unknown:#a78bfa; --healthy:#34d399;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         background:var(--bg); color:var(--text); padding-bottom:90px; }
  header { display:flex; align-items:center; gap:16px; padding:16px 24px;
           border-bottom:1px solid var(--border); }
  header h1 { font-size:18px; margin:0; }
  nav a { color:var(--muted); text-decoration:none; margin-right:14px; }
  nav a.active, nav a:hover { color:var(--text); }
  .container { padding:20px 24px; }
  .counts { display:flex; gap:12px; margin-bottom:18px; flex-wrap:wrap; }
  .count-card { background:var(--card); border:1px solid var(--border); border-radius:8px;
                padding:10px 16px; min-width:96px; }
  .count-card .n { font-size:22px; font-weight:600; }
  .count-card .l { color:var(--muted); font-size:12px; text-transform:uppercase; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
  .btn { background:var(--card); color:var(--text); border:1px solid var(--border);
         border-radius:6px; padding:6px 12px; cursor:pointer; font-size:13px; }
  .btn:hover { border-color:var(--accent); }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:#06121f; font-weight:600; }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border);
          font-size:13px; vertical-align:top; }
  th { color:var(--muted); font-weight:600; cursor:pointer; user-select:none; white-space:nowrap; }
  tr:hover td { background:#1c212b; }
  .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
  .b-WASTE { background:rgba(240,160,32,.18); color:var(--waste); }
  .b-ROGUE { background:rgba(248,113,113,.18); color:var(--rogue); }
  .b-UNKNOWN { background:rgba(167,139,250,.18); color:var(--unknown); }
  .b-HEALTHY { background:rgba(52,211,153,.16); color:var(--healthy); }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  .reasons { color:var(--muted); font-size:12px; }
  .cmd { color:var(--waste); }
  .fixbtn { margin-top:6px; padding:3px 9px; font-size:12px; }
  .fixbtn:hover { border-color:var(--healthy); color:var(--healthy); }
  .cart { position:fixed; bottom:0; left:0; right:0; background:#10141c;
          border-top:1px solid var(--border); padding:12px 24px; display:flex;
          align-items:center; gap:14px; }
  .cart .count { font-weight:600; }
  .seg { display:inline-flex; border:1px solid var(--border); border-radius:6px; overflow:hidden; }
  .seg button { background:var(--card); border:none; color:var(--muted); padding:6px 12px;
                cursor:pointer; font-size:13px; }
  .seg button.active { background:var(--accent); color:#06121f; font-weight:600; }
  dialog { background:var(--card); color:var(--text); border:1px solid var(--border);
           border-radius:10px; width:min(820px,92vw); max-height:80vh; }
  dialog textarea { width:100%; height:46vh; background:var(--bg); color:var(--text);
                    border:1px solid var(--border); border-radius:6px; font-family:ui-monospace,monospace;
                    font-size:12px; padding:10px; }
  .hint { color:var(--muted); font-size:12px; margin:6px 0 14px; }
</style>
</head>
<body>
<header>
  <h1>Daemon Health + Waste</h1>
  <nav>
    <a href="/">Usage</a>
    <a href="/daemons" class="active">Daemons</a>
  </nav>
  <span class="hint" id="reg-path"></span>
</header>

<div class="container">
  <div class="counts" id="counts"></div>

  <div class="seg" id="view-seg">
    <button data-view="all" class="active" onclick="setView('all')">All daemons</button>
    <button data-view="waste" onclick="setView('waste')">Waste &amp; Rogue</button>
  </div>

  <div class="toolbar">
    <button class="btn" onclick="selectAll()">Select all</button>
    <button class="btn" onclick="deselectAll()">Deselect all</button>
    <button class="btn" onclick="invertSel()">Invert</button>
    <span class="hint" id="row-hint"></span>
  </div>

  <table id="tbl">
    <thead><tr>
      <th></th>
      <th onclick="sortBy('label')">Label</th>
      <th onclick="sortBy('bucket')">Bucket</th>
      <th onclick="sortBy('loaded')">State</th>
      <th>Schedule</th>
      <th onclick="sortBy('last_exit')">Exit</th>
      <th onclick="sortBy('cost_30d')">30d $</th>
      <th>Why / Remediation</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>

<div class="cart">
  <span class="count"><span id="cart-n">0</span> selected</span>
  <button class="btn primary" onclick="copyPrompt()">Copy repair prompt</button>
  <button class="btn" onclick="exportPrompt()">Export .md</button>
  <span class="hint">Report-only - prompt lists commands, runs nothing.</span>
</div>

<dialog id="dlg">
  <div style="padding:16px">
    <h3 style="margin-top:0">Repair prompt</h3>
    <textarea id="dlg-text" readonly></textarea>
    <div style="margin-top:10px;display:flex;gap:8px">
      <button class="btn primary" onclick="copyFromDialog()">Copy to clipboard</button>
      <button class="btn" onclick="document.getElementById('dlg').close()">Close</button>
    </div>
  </div>
</dialog>

<script>
let report = null;
let view = 'all';
let sortCol = 'bucket', sortDir = 'asc';
const selected = new Set();   // keys: "d:<label>" or "r:<pid>"

function esc(s){const d=document.createElement('div');d.textContent=String(s==null?'':s);return d.innerHTML;}
function keyOf(item){ return item.pid!=null && item.label==null ? 'r:'+item.pid : 'd:'+item.label; }

async function load(){
  const r = await fetch('/api/daemons'); report = await r.json();
  document.getElementById('reg-path').textContent = 'registry: ' + (report.registry_path||'');
  renderCounts(); render();
}

function renderCounts(){
  const c = report.counts||{};
  const order = [['HEALTHY','healthy'],['WASTE','waste'],['ROGUE','rogue'],['UNKNOWN','unknown']];
  document.getElementById('counts').innerHTML = order.map(([k,cls])=>
    `<div class="count-card"><div class="n b-${k}" style="background:none;padding:0">${c[k]||0}</div><div class="l">${k}</div></div>`
  ).join('');
}

function items(){
  const ds = (report.daemons||[]).map(d=>({...d, _key:'d:'+d.label}));
  const rs = (report.rogues||[]).map(r=>({...r, _key:'r:'+r.pid, label:null}));
  let all = ds.concat(rs);
  if(view==='waste') all = all.filter(x=>['WASTE','ROGUE','UNKNOWN'].includes(x.bucket));
  all.sort((a,b)=>{
    let av=a[sortCol], bv=b[sortCol];
    if(sortCol==='bucket'){ const ord={ROGUE:0,WASTE:1,UNKNOWN:2,HEALTHY:3}; av=ord[a.bucket]; bv=ord[b.bucket]; }
    av=av==null?'':av; bv=bv==null?'':bv;
    return (av<bv?-1:av>bv?1:0)*(sortDir==='asc'?1:-1);
  });
  return all;
}

function render(){
  const list = items();
  document.getElementById('rows').innerHTML = list.map(x=>{
    const k = x._key, checked = selected.has(k)?'checked':'';
    const label = x.label!=null ? esc(x.label) : 'process pid '+x.pid+' <span class="mono">'+esc((x.command||'').slice(0,48))+'</span>';
    const state = x.label!=null ? (x.loaded?'loaded':'off') : (x.cpu!=null?x.cpu.toFixed(0)+'% cpu':'');
    const cost = x.cost_30d!=null ? ('$'+x.cost_30d.toFixed(2)+(x.cost_mixed?'*':'')) : '';
    const why = (x.reasons||[]).join('; ');
    const cmd = x.remediation ? `<div class="cmd mono">${esc(x.remediation)}</div>` : '';
    const fix = (x.label!=null && x.bucket!=='HEALTHY')
      ? `<button class="btn fixbtn" data-label="${esc(x.label)}" onclick="copyFix(this)">Fix &rarr;</button>` : '';
    return `<tr>
      <td><input type="checkbox" ${checked} onchange="toggle('${k}',this.checked)"></td>
      <td>${label}</td>
      <td><span class="badge b-${x.bucket}">${x.bucket}</span></td>
      <td>${esc(state)}</td>
      <td>${esc(x.schedule||'')}</td>
      <td>${x.last_exit==null?'':esc(x.last_exit)}</td>
      <td>${esc(cost)}</td>
      <td><div class="reasons">${esc(why)}</div>${cmd}${fix}</td>
    </tr>`;
  }).join('');
  document.getElementById('row-hint').textContent =
    list.length + ' rows' + (view==='all' ? ' (* = shared dir, cost not attributable)' : '');
  updateCart();
}

function copyFix(btn){
  const label = btn.getAttribute('data-label');
  const cmd = '/fix-daemon ' + label;
  const done = ()=>{ const o=btn.innerHTML; btn.innerHTML='Copied &check;'; setTimeout(()=>btn.innerHTML=o,1300); };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(cmd).then(done).catch(()=>prompt('Copy this command into Claude:', cmd));
  } else { prompt('Copy this command into Claude:', cmd); }
}
function toggle(k,on){ on?selected.add(k):selected.delete(k); updateCart(); }
function selectAll(){ items().forEach(x=>selected.add(x._key)); render(); }
function deselectAll(){ selected.clear(); render(); }
function invertSel(){ const cur=new Set(selected); selected.clear();
  items().forEach(x=>{ if(!cur.has(x._key)) selected.add(x._key); }); render(); }
function setView(v){ view=v; document.querySelectorAll('#view-seg button').forEach(b=>
  b.classList.toggle('active', b.dataset.view===v)); render(); }
function sortBy(c){ if(sortCol===c) sortDir=sortDir==='asc'?'desc':'asc'; else {sortCol=c; sortDir='asc';} render(); }
function updateCart(){ document.getElementById('cart-n').textContent = selected.size; }

async function genPrompt(){
  const labels=[], pids=[];
  selected.forEach(k=>{ if(k.startsWith('d:')) labels.push(k.slice(2)); else pids.push(parseInt(k.slice(2))); });
  const r = await fetch('/api/prompt', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({labels, pids})});
  const d = await r.json(); return d.prompt||'';
}
async function copyPrompt(){
  if(!selected.size){ alert('Select at least one item.'); return; }
  const text = await genPrompt();
  const dlg=document.getElementById('dlg'); document.getElementById('dlg-text').value=text;
  try{ await navigator.clipboard.writeText(text); }catch(e){}
  dlg.showModal();
}
async function copyFromDialog(){ try{ await navigator.clipboard.writeText(document.getElementById('dlg-text').value); }catch(e){} }
async function exportPrompt(){
  if(!selected.size){ alert('Select at least one item.'); return; }
  const text = await genPrompt();
  const now=new Date(); const pad=n=>String(n).padStart(2,'0');
  const name=`daemon-repair-${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}.md`;
  const blob=new Blob([text],{type:'text/markdown'}); const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download=name; a.click(); URL.revokeObjectURL(url);
}
load();
</script>
</body>
</html>
"""
