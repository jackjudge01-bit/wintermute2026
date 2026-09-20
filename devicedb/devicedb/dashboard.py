#!/usr/bin/env python3
"""who's near me — a live device dashboard for the local agent.

Serves a single-page dashboard that:
  - runs a live sweep (Marauder ESP32 + BLEA) on demand
  - shows devices currently in range as cards with RSSI bars
  - shows vendor / kind breakdowns
  - lets you search the full device corpus

Run:  cd ~/devicedb && python3 dashboard.py   (port 8095)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devicedb as d  # reuse the agent's own functions

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="who's near me", version="0.1.0")

PORT = int(os.environ.get("DASH_PORT", "8095"))


def _live_sweep():
    """Run a quick sweep and return the sweep result + devices seen recently."""
    conn = d.get_db()
    res = {}
    # Marauder (ESP32) — WiFi APs + stations + BLE
    try:
        res["marauder"] = d.collect_marauder(conn, seconds=8, ble_seconds=6)
    except Exception as e:
        res["marauder"] = {"error": str(e)}
    # BLEA (host Bluetooth) — BLE
    try:
        rc, so, se = d.run_cmd(["blea", "scan", "--timeout", "6", "--json"], timeout=40)
        if rc == 0 and so.strip():
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                f.write(so)
                tmp = f.name
            d.ingest_blea(conn, tmp, None)
            os.unlink(tmp)
            res["blea"] = {"ok": True}
        else:
            res["blea"] = {"error": se or "no output"}
    except Exception as e:
        res["blea"] = {"error": str(e)}
    conn.commit()

    rows = conn.execute(
        """
        SELECT d.device_id, d.hostname, d.vendor, d.kinds, d.first_seen, d.last_seen,
               (SELECT o.rssi FROM observations o WHERE o.device_id=d.device_id
                 ORDER BY o.ts DESC LIMIT 1) AS rssi,
               (SELECT o.kind FROM observations o WHERE o.device_id=d.device_id
                 ORDER BY o.ts DESC LIMIT 1) AS kind
          FROM devices d
         WHERE d.last_seen > datetime('now','-5 minutes')
         ORDER BY d.last_seen DESC
        """
    ).fetchall()
    conn.close()
    return {"sweep": res, "count": len(rows), "devices": [dict(r) for r in rows]}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


@app.get("/api/live")
def api_live():
    return _live_sweep()


@app.get("/api/devices")
def api_devices(
    search: str | None = None,
    kind: str | None = None,
    limit: int = Query(300, le=1000),
):
    conn = d.get_db()
    q = ("SELECT device_id, mac, hostname, vendor, kinds, first_seen, last_seen "
         "FROM devices WHERE 1=1")
    p = []
    if search:
        q += " AND (hostname LIKE ? OR vendor LIKE ? OR mac LIKE ?)"
        p += [f"%{search}%"] * 3
    if kind:
        q += " AND kinds LIKE ?"
        p.append(f"%{kind}%")
    q += " ORDER BY last_seen DESC LIMIT ?"
    p.append(limit)
    rows = conn.execute(q, p).fetchall()
    conn.close()
    return {"count": len(rows), "devices": [dict(r) for r in rows]}


@app.get("/api/stats")
def api_stats():
    conn = d.get_db()
    vendors = conn.execute(
        "SELECT COALESCE(vendor,'unknown') AS v, COUNT(*) AS n FROM devices "
        "GROUP BY v ORDER BY n DESC LIMIT 12"
    ).fetchall()
    kinds = conn.execute(
        "SELECT kinds, COUNT(*) AS n FROM devices GROUP BY kinds ORDER BY n DESC LIMIT 12"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()[0]
    conn.close()
    return {"total": total, "vendors": [dict(r) for r in vendors],
            "kinds": [dict(r) for r in kinds]}


@app.get("/api/device/{device_id}")
def api_device(device_id: int):
    """Full detail for one device: identity, all sightings, aliases, history."""
    conn = d.get_db()
    dev = conn.execute(
        "SELECT * FROM devices WHERE device_id=?", (device_id,)
    ).fetchone()
    if not dev:
        conn.close()
        return {"error": "not found"}
    aliases = conn.execute(
        "SELECT mac, name, mfr_data, first_seen, last_seen, source "
        "FROM identities WHERE device_id=? ORDER BY last_seen DESC", (device_id,)
    ).fetchall()
    sightings = conn.execute(
        "SELECT ts, source, kind, name, rssi, channel, place, lat, lon, extra "
        "FROM observations WHERE device_id=? ORDER BY ts DESC LIMIT 50", (device_id,)
    ).fetchall()
    conn.close()
    return {
        "device": dict(dev),
        "aliases": [dict(a) for a in aliases],
        "sightings": [dict(s) for s in sightings],
    }


# ---- per-kind probes ------------------------------------------------------
def _probe_ble(mac):
    """BLE probes: name, services (GATT), RSSI. Uses hcitool/gatttool."""
    out = {"ok": True, "results": []}
    # RSSI / name via hcitool
    try:
        rc, so, se = d.run_cmd(["hcitool", "rssi", mac], timeout=10)
        out["results"].append({"probe": "rssi", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "rssi", "error": str(e)})
    try:
        rc, so, se = d.run_cmd(["hcitool", "name", mac], timeout=10)
        out["results"].append({"probe": "name", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "name", "error": str(e)})
    # GATT services via gatttool (may need the device to be connectable)
    try:
        rc, so, se = d.run_cmd(
            ["gatttool", "-b", mac, "--primary"], timeout=8)
        out["results"].append({"probe": "gatt_services", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "gatt_services", "error": str(e)})
    return out


def _probe_wifi(mac):
    """WiFi probes: ping, ARP, port scan (nmap)."""
    out = {"ok": True, "results": []}
    try:
        rc, so, se = d.run_cmd(["ping", "-c", "2", "-W", "2", mac], timeout=10)
        out["results"].append({"probe": "ping", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "ping", "error": str(e)})
    try:
        rc, so, se = d.run_cmd(["nmap", "-sP", "-T4", mac], timeout=30)
        out["results"].append({"probe": "nmap_ping", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "nmap_ping", "error": str(e)})
    return out


def _probe_lan(ip):
    """LAN probes: ping, port scan (nmap top ports)."""
    out = {"ok": True, "results": []}
    try:
        rc, so, se = d.run_cmd(["ping", "-c", "2", "-W", "2", ip], timeout=10)
        out["results"].append({"probe": "ping", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "ping", "error": str(e)})
    try:
        rc, so, se = d.run_cmd(["nmap", "-T4", "-p", "22,80,443,8080,3389,445,53,21,25", ip], timeout=60)
        out["results"].append({"probe": "nmap_ports", "output": (so or se).strip()})
    except Exception as e:
        out["results"].append({"probe": "nmap_ports", "error": str(e)})
    return out


@app.get("/api/probe/{device_id}")
def api_probe(device_id: int, probe: str = Query(...)):
    """Run a probe against a device. Only probes valid for its kind are offered."""
    conn = d.get_db()
    dev = conn.execute(
        "SELECT * FROM devices WHERE device_id=?", (device_id,)
    ).fetchone()
    if not dev:
        conn.close()
        return {"error": "not found"}
    kinds = (dev["kinds"] or "").split(",")
    mac = dev["mac"]
    ip = dev["ip"]
    conn.close()

    # pick the probe set by kind
    if "ble" in kinds:
        if probe not in ("rssi", "name", "gatt_services"):
            return {"error": f"probe '{probe}' not valid for BLE device"}
        if not mac:
            return {"error": "no MAC for this device"}
        return _probe_ble(mac)
    if "wifi" in kinds:
        if probe not in ("ping", "nmap_ping"):
            return {"error": f"probe '{probe}' not valid for WiFi device"}
        target = ip or mac
        if not target:
            return {"error": "no IP or MAC for this device"}
        return _probe_wifi(target)
    if "eth" in kinds or "lan" in kinds:
        if probe not in ("ping", "nmap_ports"):
            return {"error": f"probe '{probe}' not valid for LAN device"}
        if not ip:
            return {"error": "no IP for this device"}
        return _probe_lan(ip)
    return {"error": f"no probes defined for kind '{kinds}'"}


HTML_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>who's near me</title>
<style>
  :root{--bg:#0b0e14;--panel:#141a26;--panel2:#1b2333;--line:#26304a;--txt:#dbe4f5;
        --dim:#7c8aa8;--acc:#4f8cff;--ok:#3ddc84;--warn:#ffb454;--bad:#ff5c5c;}
  *{box-sizing:border-box}
  body{margin:0;font-family:ui-monospace,Menlo,Consolas,monospace;background:var(--bg);
       color:var(--txt);font-size:14px}
  header{display:flex;align-items:center;gap:16px;padding:14px 22px;border-bottom:1px solid var(--line);
         background:var(--panel);position:sticky;top:0;z-index:5}
  header h1{font-size:18px;margin:0;letter-spacing:.5px}
  header h1 span{color:var(--acc)}
  .btn{background:var(--acc);color:#04101f;border:0;border-radius:6px;padding:8px 18px;
       font-weight:700;cursor:pointer;font-family:inherit;font-size:13px}
  .btn:disabled{opacity:.5;cursor:wait}
  .pill{font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid var(--line);color:var(--dim)}
  .pill.on{color:var(--ok);border-color:var(--ok)}
  main{padding:20px 22px;display:grid;grid-template-columns:1fr 320px;gap:18px}
  @media(max-width:900px){main{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
  .panel h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--dim)}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
  .card{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
  .card .name{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .card .sub{color:var(--dim);font-size:11px;margin-top:2px}
  .badge{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;margin-top:6px;
         background:#223;color:var(--acc)}
  .badge.ble{background:#1d2b3a;color:#5cc8ff}.badge.wifi{background:#2a2418;color:#ffb454}
  .badge.lan{background:#1d2a1d;color:#3ddc84}
  .rssi{height:4px;background:#0a0e16;border-radius:2px;margin-top:8px;overflow:hidden}
  .rssi i{display:block;height:100%;background:linear-gradient(90deg,var(--bad),var(--warn),var(--ok))}
  .empty{color:var(--dim);padding:20px;text-align:center}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
  th{color:var(--dim);font-weight:600;position:sticky;top:0;background:var(--panel)}
  td.name{max-width:220px;overflow:hidden;text-overflow:ellipsis}
  input[type=search]{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--txt);
       border-radius:6px;padding:8px 10px;font-family:inherit;margin-bottom:10px}
  .bar{height:14px;background:var(--panel2);border-radius:3px;margin:3px 0;overflow:hidden}
  .bar i{display:block;height:100%;background:var(--acc)}
  .row{display:flex;justify-content:space-between;font-size:12px;color:var(--dim);margin-top:2px}
  .big{font-size:26px;font-weight:800;color:var(--txt)}
  .muted{color:var(--dim)}
  .err{color:var(--bad);font-size:12px;margin-top:8px}
  .card{cursor:pointer}
  .card:hover{border-color:var(--acc)}
  .modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:20;
         align-items:flex-start;justify-content:center;padding:30px 16px;overflow:auto}
  .modal.open{display:flex}
  .modalbox{background:var(--panel);border:1px solid var(--line);border-radius:12px;
            max-width:760px;width:100%;padding:20px}
  .modalbox h2{margin:0 0 4px;font-size:18px}
  .modalbox .close{float:right;cursor:pointer;color:var(--dim);font-size:20px;background:none;border:0}
  .kv{display:grid;grid-template-columns:130px 1fr;gap:4px 12px;font-size:12px;margin:12px 0}
  .kv b{color:var(--dim);font-weight:600}
  .probes{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
  .probe{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
         border-radius:6px;padding:6px 12px;cursor:pointer;font-family:inherit;font-size:12px}
  .probe:hover{border-color:var(--acc)}
  .probe:disabled{opacity:.5;cursor:wait}
  .probeout{background:#0a0e16;border:1px solid var(--line);border-radius:6px;padding:10px;
            font-size:11px;white-space:pre-wrap;max-height:220px;overflow:auto;margin-top:6px}
  .hist{font-size:11px;color:var(--dim);max-height:180px;overflow:auto}
  .hist div{padding:2px 0;border-bottom:1px solid var(--line)}
</style></head><body>
<header>
  <h1>who's <span>near me</span></h1>
  <button class="btn" id="scan">SCAN NOW</button>
  <span class="pill" id="autorefresh">auto-refresh off</span>
  <span class="pill" id="lastscan">never</span>
</header>
<main>
  <div>
    <div class="panel">
      <h2>devices in range <span class="muted" id="livecount"></span></h2>
      <div class="cards" id="cards"><div class="empty">hit SCAN NOW to see what's around you</div></div>
      <div class="err" id="liveerr"></div>
    </div>
    <div class="panel" style="margin-top:18px">
      <h2>device corpus <span class="muted" id="corpuscount"></span></h2>
      <input type="search" id="q" placeholder="search name / vendor / mac...">
      <div style="max-height:420px;overflow:auto">
        <table><thead><tr><th>name</th><th>vendor</th><th>kind</th><th>first seen</th><th>last seen</th></tr></thead>
        <tbody id="corpus"></tbody></table>
      </div>
    </div>
  </div>
  <div>
    <div class="panel">
      <h2>corpus</h2>
      <div class="big" id="total">–</div>
      <div class="row"><span>devices tracked</span></div>
    </div>
    <div class="panel" style="margin-top:18px">
      <h2>top vendors</h2>
      <div id="vendors"></div>
    </div>
    <div class="panel" style="margin-top:18px">
      <h2>by kind</h2>
      <div id="kinds"></div>
    </div>
  </div>
</main>
<div class="modal" id="modal"><div class="modalbox">
  <button class="close" onclick="closeDetail()">×</button>
  <h2 id="mname">–</h2>
  <div class="muted" id="msub">–</div>
  <div class="kv" id="mkv"></div>
  <h2 style="font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:1px">probes</h2>
  <div class="probes" id="mprobes"></div>
  <div id="mprobeout"></div>
  <h2 style="font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-top:14px">history</h2>
  <div class="hist" id="mhist"></div>
</div></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const kindBadge=k=>`<span class="badge ${(k||'').toLowerCase()}">${esc(k||'?')}</span>`;
const rssiBar=r=>{const w=Math.max(0,Math.min(100,(r+100)/60*100));return `<div class="rssi"><i style="width:${w}%"></i></div>`;};

async function live(){
  const b=$('#scan'); b.disabled=true; b.textContent='SCANNING…';
  try{
    const r=await fetch('/api/live').then(x=>x.json());
    $('#lastscan').textContent='last scan '+new Date().toLocaleTimeString();
    $('#livecount').textContent='('+r.count+')';
    const c=$('#cards'); c.innerHTML='';
    if(!r.devices.length){c.innerHTML='<div class="empty">nothing in range — try again</div>';}
    for(const dv of r.devices){
      const el=document.createElement('div'); el.className='card';
      el.innerHTML=`<div class="name">${esc(dv.hostname||dv.vendor||'unknown')}</div>
        <div class="sub">${esc(dv.vendor||'')} · ${esc(dv.kinds||'')}</div>
        ${kindBadge(dv.kind)} ${rssiBar(dv.rssi)}
                <div class="sub">rssi ${dv.rssi??'–'} · ${esc((dv.last_seen||'').slice(11,19))}</div>`;
              el.onclick=()=>openDetail(dv.device_id);
      c.appendChild(el);
    }
    $('#liveerr').textContent = r.sweep?.marauder?.error ? 'marauder: '+r.sweep.marauder.error : '';
  }catch(e){$('#liveerr').textContent='scan failed: '+e;}
  b.disabled=false; b.textContent='SCAN NOW';
}

async function stats(){
  const r=await fetch('/api/stats').then(x=>x.json());
  $('#total').textContent=r.total.toLocaleString();
  $('#vendors').innerHTML=r.vendors.map(v=>
    `<div class="row"><span>${esc(v.v)}</span><span>${v.n}</span></div>
     <div class="bar"><i style="width:${v.n/r.total*100}%"></i></div>`).join('');
  $('#kinds').innerHTML=r.kinds.map(k=>
    `<div class="row"><span>${esc(k.kinds)}</span><span>${k.n}</span></div>
     <div class="bar"><i style="width:${k.n/r.total*100}%"></i></div>`).join('');
}

let qTimer;
async function corpus(){
  const q=$('#q').value;
  const r=await fetch('/api/devices?search='+encodeURIComponent(q)+'&limit=300').then(x=>x.json());
  $('#corpuscount').textContent='('+r.count+')';
  $('#corpus').innerHTML=r.devices.map(dv=>
    `<tr><td class="name" title="${esc(dv.hostname||'')}">${esc(dv.hostname||'–')}</td>
     <td>${esc(dv.vendor||'')}</td><td>${esc(dv.kinds||'')}</td>
     <td>${esc((dv.first_seen||'').slice(0,16))}</td><td>${esc((dv.last_seen||'').slice(0,16))}</td></tr>`).join('')
    || '<tr><td colspan=5 class="empty">no matches</td></tr>';
}
$('#q').addEventListener('input',()=>{clearTimeout(qTimer);qTimer=setTimeout(corpus,250);});
$('#scan').addEventListener('click',live);
let auto=false;
setInterval(()=>{ if(auto) live(); },30000);
document.addEventListener('keydown',e=>{ if(e.key==='a'){auto=!auto;$('#autorefresh').textContent=auto?'auto-refresh on':'auto-refresh off';$('#autorefresh').classList.toggle('on',auto);} });
let curId=null;
async function openDetail(id){
  curId=id;
  const r=await fetch('/api/device/'+id).then(x=>x.json());
  if(r.error){return;}
  const dv=r.device;
  $('#mname').textContent=dv.hostname||dv.vendor||'device #'+id;
  $('#msub').textContent=(dv.vendor||'')+' · '+(dv.kinds||'');
  $('#mkv').innerHTML=
    `<b>device id</b><span>${dv.device_id}</span>
     <b>mac</b><span>${esc(dv.mac||'–')}</span>
     <b>ip</b><span>${esc(dv.ip||'–')}</span>
     <b>vendor</b><span>${esc(dv.vendor||'–')}</span>
     <b>kinds</b><span>${esc(dv.kinds||'–')}</span>
     <b>first seen</b><span>${esc(dv.first_seen||'–')}</span>
     <b>last seen</b><span>${esc(dv.last_seen||'–')}</span>
     <b>aliases</b><span>${r.aliases.length} MAC(s)</span>`;
  const kinds=(dv.kinds||'').split(',');
  const probes=probeSet(kinds);
  $('#mprobes').innerHTML=probes.map(p=>`<button class="probe" onclick="runProbe(${id},'${p.id}')">${p.label}</button>`).join('')||'<span class="muted">no probes for this kind</span>';
  $('#mprobeout').innerHTML='';
  $('#mhist').innerHTML=r.sightings.map(s=>
    `<div>${esc(s.ts||'')} · ${esc(s.source||'')} · ${esc(s.kind||'')} · rssi ${s.rssi??'–'} · ${esc(s.place||'')}</div>`).join('')||'<div>no history</div>';
  $('#modal').classList.add('open');
}
function closeDetail(){$('#modal').classList.remove('open');}
function probeSet(kinds){
  const s=[];
  if(kinds.includes('ble')) s.push({id:'rssi',label:'RSSI'},{id:'name',label:'Name'},{id:'gatt_services',label:'GATT services'});
  if(kinds.includes('wifi')) s.push({id:'ping',label:'Ping'},{id:'nmap_ping',label:'nmap ping'});
  if(kinds.includes('eth')||kinds.includes('lan')) s.push({id:'ping',label:'Ping'},{id:'nmap_ports',label:'Port scan'});
  return s;
}
async function runProbe(id,probe){
  const btns=[...document.querySelectorAll('.probe')];
  btns.forEach(b=>b.disabled=true);
  $('#mprobeout').innerHTML='<div class="probeout">running '+probe+'…</div>';
  try{
    const r=await fetch('/api/probe/'+id+'?probe='+probe).then(x=>x.json());
    if(r.error){$('#mprobeout').innerHTML='<div class="probeout">'+esc(r.error)+'</div>';}
    else{
      $('#mprobeout').innerHTML=r.results.map(x=>
        `<div class="probeout"><b>${esc(x.probe)}</b>\n${esc(x.output||x.error||'')}</div>`).join('');
    }
  }catch(e){$('#mprobeout').innerHTML='<div class="probeout">'+esc(e)+'</div>';}
  btns.forEach(b=>b.disabled=false);
}
stats(); corpus();
</script></body></html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
