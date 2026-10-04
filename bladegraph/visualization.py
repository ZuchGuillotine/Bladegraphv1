"""Offline researcher demo. No remote scripts, telemetry, or raw monitor evidence."""

import json
from pathlib import Path

PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<title>BladeGraph · Containment & recovery</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1220;color:#e5edf8}
body{margin:0;padding:36px;max-width:1400px;margin:auto}h1{font-size:36px;margin:4px 0 10px;letter-spacing:-1px}
.eyebrow{color:#7dd3fc;letter-spacing:3px;font-size:12px}p{color:#a7b8d0;max-width:920px;line-height:1.6}
.badge{display:inline-block;border:1px solid #475569;border-radius:20px;padding:6px 12px;color:#fbbf24;font-size:12px}
.controls{display:flex;gap:18px;align-items:center;margin:24px 0}button{background:#38bdf8;border:0;padding:10px 22px;border-radius:6px;color:#071321;font-weight:700;cursor:pointer}input{flex:1}
.panel{background:#111d30;border:1px solid #273850;border-radius:12px;padding:20px;margin:16px 0}.stats{display:flex;gap:40px;flex-wrap:wrap}.stat b{display:block;font-size:28px}.stat span{font-size:12px;color:#9eb2ce}
.graph-wrap{overflow-x:auto}svg{width:100%;min-width:1050px;height:410px}text{fill:#b9cbe2;font-size:11px}#detail{min-height:24px}.legend{display:flex;gap:24px;flex-wrap:wrap;font-size:13px}.legend span:before{content:'●';color:var(--c);padding-right:7px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:9px;border-bottom:1px solid #26384d}th{color:#94a3b8}#events{max-height:250px;overflow:auto}
</style>
<div class="eyebrow">HAIDAA RESEARCH / GROVE × VILLAGEAI</div><h1>BladeGraph</h1>
<p>Contain the contaminated branch. Preserve verified work. Reconnect a fresh generation to the knowledge graph.</p>
<div class="badge" id="mode"></div>
<div class="panel stats"><div class="stat"><b>13</b><span>INITIAL WORKERS</span></div><div class="stat"><b id="culls">0</b><span>CULLED GENERATIONS</span></div><div class="stat"><b id="spawns">0</b><span>REPLACEMENTS</span></div><div class="stat"><b id="verified">0</b><span>VERIFIED CLAIMS</span></div><div class="stat"><b id="blocked">0</b><span>BLOCKED ARTIFACTS</span></div></div>
<div class="controls"><button id="play">Play</button><input aria-label="Event timeline" id="slider" type="range" min="0" value="0"><span id="position"></span></div>
<div class="legend"><span style="--c:#34d399">Verified</span><span style="--c:#94a3b8">Unverified</span><span style="--c:#fbbf24">Quarantined</span><span style="--c:#fb7185">Culled</span><span style="--c:#38bdf8">Respawned</span></div>
<div class="panel"><div class="graph-wrap"><svg id="graph" viewBox="0 0 1280 470" role="img" aria-label="Agent generations, artifacts and material dependencies"></svg></div><div id="detail"></div></div>
<div class="panel"><b>Intervention timeline</b><div id="events"></div></div>
<p id="caveat"></p>
<script>
const DATA=__DATA__;
const $=id=>document.getElementById(id), slider=$('slider');slider.max=DATA.events.length;
$('mode').textContent=DATA.metrics.mode+' · Arm '+DATA.metrics.arm+' · Seed '+DATA.metrics.seed;
$('caveat').textContent=DATA.metrics.limitations;
let timer=null;const ns='http://www.w3.org/2000/svg';
function el(tag,attrs,text){const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e}
function draw(){const n=Number(slider.value), agents={}, arts={}, interventions=[];let culls=0,spawns=0;
for(const e of DATA.events.slice(0,n)){const p=e.p;
 if(e.type==='agent.spawned'){agents[e.agent]={g:e.g,state:e.g?'RESPAWNED':'NORMAL'};if(e.g)spawns++}
 if(e.type==='agent.state'){agents[e.agent]={g:e.g,state:p.state};if(p.state==='CULLED')culls++;interventions.push(e)}
 if(e.type==='artifact.created')arts[p.id]={...p,blocked:false};
 if(e.type==='artifact.quarantined'){for(const id of p.ids)if(arts[id])arts[id].blocked=true;interventions.push(e)}
 if(e.type==='artifact.validated'&&arts[p.id])arts[p.id].verified=p.passed;
 if(e.type==='agent.bootstrapped')interventions.push(e);
}
const svg=$('graph');svg.replaceChildren();const xy={};
for(let i=0;i<13;i++){const key='worker-'+String(i).padStart(2,'0'), a=agents[key];if(!a)continue;const x=52+i*98,y=58;xy[key]=[x,y];const color=a.state==='CULLED'?'#fb7185':a.g?'#38bdf8':'#94a3b8';svg.append(el('circle',{cx:x,cy:y,r:20,fill:color,opacity:.9}),el('text',{x,y:94,'text-anchor':'middle'},String(i+1)+' · g'+a.g));}
const list=Object.values(arts);list.forEach((a,i)=>xy[a.id]=[45+(i%20)*63,162+Math.floor(i/20)*83]);
for(const a of list){const [x,y]=xy[a.id];for(const dep of a.deps){if(xy[dep])svg.append(el('line',{x1:x,y1:y,x2:xy[dep][0],y2:xy[dep][1],stroke:a.blocked?'#b7792e':'#355779','stroke-width':1.5,opacity:.8}))}const actor=a.author.split(':')[0];if(xy[actor])svg.append(el('line',{x1:xy[actor][0],y1:82,x2:x,y2:y,stroke:'#355779',opacity:.18}));}
for(const a of list){const [x,y]=xy[a.id],color=a.blocked?'#fbbf24':a.verified?'#34d399':'#94a3b8';const dot=el('circle',{cx:x,cy:y,r:a.kind==='measurements'?9:12,fill:color});dot.append(el('title',{},a.id+' · '+a.kind));dot.addEventListener('click',()=>{$('detail').textContent=a.id+' · '+a.kind+' · '+(a.blocked?'unavailable':a.verified?'verified':'unverified')});svg.append(dot,el('text',{x,y:y+25,'text-anchor':'middle'},a.id.replace('source-task-','s')));}
$('position').textContent=n+' / '+DATA.events.length;$('culls').textContent=culls;$('spawns').textContent=spawns;
$('verified').textContent=list.filter(a=>a.verified&&a.kind==='claim').length;$('blocked').textContent=list.filter(a=>a.blocked).length;
const table=document.createElement('table');for(const e of interventions.slice(-12).reverse()){const tr=document.createElement('tr');for(const value of [e.id,e.agent+' g'+e.g,e.type,e.p.state||e.p.id||'clean graph IDs']){const td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)}$('events').replaceChildren(table);
}
slider.addEventListener('input',draw);$('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='Play';return}if(+slider.value>=DATA.events.length)slider.value=0;$('play').textContent='Pause';timer=setInterval(()=>{slider.value=Math.min(+slider.value+4,DATA.events.length);draw();if(+slider.value===DATA.events.length){clearInterval(timer);timer=null;$('play').textContent='Replay'}},80)};draw();
</script></html>"""


def render(run: Path, output: Path):
    events = []
    for line in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        p, kind = e["payload"], e["event_type"]
        if kind == "artifact.created":
            public = {
                "id": p["artifact_id"],
                "author": p["author"],
                "kind": p["content"]["kind"],
                "deps": p["derived_from"],
                "verified": p["verification_state"] == "VERIFIED",
            }
        elif kind == "artifact.quarantined":
            public = {"id": p["root"], "ids": p["artifact_ids"]}
        elif kind == "artifact.validated":
            public = {"id": p["artifact_id"], "passed": p["passed"]}
        elif kind == "agent.state":
            public = {"state": p["state"]}
        elif kind in {"agent.spawned", "agent.bootstrapped"}:
            public = {}
        else:
            continue
        events.append(
            {"id": e["event_id"], "type": kind, "agent": e["agent_id"], "g": e["agent_generation"], "p": public}
        )
    data = {"events": events, "metrics": json.loads((run / "metrics.json").read_text())}
    output.write_text(PAGE.replace("__DATA__", json.dumps(data).replace("<", "\\u003c")))
