/* ============================================================
   Cogen — Venture Twin frontend
   Hash-routed SPA over the FastAPI surface. No build step.

   Routes
     #/                ventures index
     #/new             progressive intake
     #/v/{id}/{tab}    workspace: position|model|evidence|sandbox|roadmap|forks|activity
   ============================================================ */
"use strict";

/* ---------- tiny helpers ---------- */
const $  = (s, r = document) => r.querySelector(s);
const el = (t, a = {}, kids = []) => {
  const n = document.createElement(t);
  for (const k in a) {
    if (k === "class") n.className = a[k];
    else if (k === "html") n.innerHTML = a[k];
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), a[k]);
    else if (a[k] !== null && a[k] !== undefined && a[k] !== false) n.setAttribute(k, a[k]);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => c && n.append(c.nodeType ? c : String(c)));
  return n;
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

// Deliberately minimal — the model's own prose only ever uses headers, bold/italic emphasis, and
// bullet/numbered lists, never links or code. Escapes first, then builds HTML from a fixed set of
// known-safe tags, so nothing from model output ever lands in innerHTML unescaped.
function mdLite(text){
  const lines = String(text ?? "").split("\n");
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((#[^\s\)\"' ]+|https?:\/\/[^\s\)\"' ]+)\)/g, (m, txt, url) => {
      const isHash = url.startsWith("#");
      return `<a href="${url}"${isHash ? "" : ' target="_blank" rel="noopener"'}>${txt}</a>`;
    });
  let html = "", listTag = null, inTable = false;
  const closeList = () => { if (listTag){ html += `</${listTag}>`; listTag = null; } };
  const closeTable = () => { if (inTable){ html += `</tbody></table>`; inTable = false; } };
  for (const raw of lines){
    const line = raw.trim();
    if (!line){ closeList(); closeTable(); continue; }
    // GFM tables: a `| a | b |` header, a `| --- | --- |` separator, then data rows -- rendered as a
    // readable artifact so the model can present numbers as a grid instead of a wall of prose.
    if (inTable || /^\|.*\|$/.test(line)){
      if (/^\|[\s:|-]+\|$/.test(line)){  // header/separator row
        if (!inTable) inTable = true;
        continue;
      }
      const cells = line.replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim()));
      if (!inTable){ html += `<table><thead><tr>${cells.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>`; inTable = true; }
      else html += `<tr>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`;
      continue;
    }
    closeList(); closeTable();
    // Horizontal rule `---` / `***` / `___` — rendered as a soft divider, not literal text.
    if (/^(\s*[-*_]){3,}\s*$/.test(line)){ html += `<hr>`; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h){ html += `<h4>${inline(h[2])}</h4>`; continue; }
    const ul = line.match(/^[*-]\s+(.*)$/);
    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (ul || ol){
      const tag = ul ? "ul" : "ol";
      if (listTag !== tag){ closeList(); html += `<${tag}>`; listTag = tag; }
      html += `<li>${inline((ul || ol)[1])}</li>`;
      continue;
    }
    html += `<p>${inline(line)}</p>`;
  }
  closeList(); closeTable();
  return html;
}

let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let d = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      d = typeof j.detail === "string" ? j.detail
        : Array.isArray(j.detail) ? j.detail.map(x => `${(x.loc || []).slice(-1)}: ${x.msg}`).join("; ")
        : d;
    } catch (_) { /* keep status text */ }
    throw new Error(d);
  }
  return res.status === 204 ? null : res.json();
}

/* ---------- domain constants (mirror app/simulation.py) ---------- */
const UNCERTAINTY = { unknown:.65, low:.45, medium:.25, high:.12, verified:.05 };
const CONF_RANK   = { unknown:1, low:2, medium:3, high:4, verified:5 };
const REQUIRED = ["setup_costs","monthly_rent","monthly_payroll","monthly_utilities",
                  "gross_margin_pct","average_basket","transactions_per_day","days_open_month","shrinkage_pct"];
const RAMP = Array.from({ length:12 }, (_, i) => Math.min(1, .55 + i * .09));
const BOUNDS = {
  gross_margin_pct:{ floor:.01, cap:.95 }, days_open_month:{ floor:1, cap:31 },
  shrinkage_pct:{ floor:0, cap:.5 }, average_basket:{ floor:1 },
};
const DEC_LABEL = { reject:"Reject", conditional:"Conditional", approve:"Approve", needs_data:"Needs data" };

/* ---------- formatting ---------- */
const cur = v => (v.intake.currency || "").trim() || "";
const nfmt = n => Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
function money(n, c) {
  if (n === null || n === undefined) return "—";
  const s = n < 0 ? "−" : "";
  return `${s}${c ? c + " " : ""}${nfmt(Math.abs(n))}`;
}
function assumptionValue(a) {
  if (a.value === null || a.value === undefined) return "—";
  if (a.unit === "ratio") return (a.value * 100).toFixed(1) + "%";
  if (Math.abs(a.value) < 100 && !Number.isInteger(a.value)) return a.value.toFixed(2);
  return nfmt(a.value);
}
const pct = n => (n === null || n === undefined) ? "—" : (n * 100).toFixed(1) + "%";
const ago = iso => {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d/60)}m ago`;
  if (d < 86400) return `${Math.floor(d/3600)}h ago`;
  return `${Math.floor(d/86400)}d ago`;
};

/* ---------- compact "essentials" from a long recommendation ----------
   The weekly recommendation is a full researched account — great to read in full, but it should
   not force the founder to scroll past a wall of it to reach their venture list. We pull the
   headline direction, the first-found reason, and the most concrete figure so the collapsed
   window captures "what to build, and why it fits" at a glance. */
function essenceOf(text){
  const clean = s => s.replace(/\*\*(.+?)\*\*/g,"$1").replace(/\*([^*]+)\*/g,"$1").trim();
  const lines = String(text ?? "").split("\n").map(s=>s.trim()).filter(Boolean);
  const result = { title:null, bullets:[], promise:null };
  // Title = a "## Recommended direction:" heading, or the first heading.
  const maybeTitle = lines.find(l => /^#{1,3}\s/i.test(l)) || lines.find(l => /recommended|direction|build/i.test(l));
  if (maybeTitle) result.title = clean(maybeTitle.replace(/^#{1,3}\s+/i,""));
  // Backfill a sensible title from prose if nothing headed surfaced.
  if (!result.title){
    const firstSentence = lines.find(l => /\.$/.test(l) && l.length > 20);
    if (firstSentence) result.title = clean(firstSentence.replace(/\.$/,""));
  }
  // Bullets: the most informative list items (capital / margin / survival / location / revenue).
  const interesting = lines.filter(l => /^(?:[-*]|\d+[.)])\s/.test(l))
    .map(l => clean(l.replace(/^[-*\d.]\s+/,"")))
    .filter(l => /capital|margin|survival|locat|revenue|price|basket|month|setup|\$|€|£|KES|USD|profit/i.test(l));
  result.bullets = interesting.slice(0,3);
  // Promise: the single most decision-relevant sentence. Skip the generic opener
  // ("Here is your customized weekly...") — prefer a genuine conclusion.
  const prose = lines
    .map(clean)
    .filter(l => !/^([#>*|-]|\d+[.)])/.test(l) && l.length > 30)
    .find(l => /this fits|worth|because|strongest|would|should|ideal|recommend|direction/i.test(l) &&
               !/^Here is your customized/i.test(l));
  if (prose) result.promise = prose.replace(/\.$/,"");
  return result;
}
function factlines(ess){
  const kids = [];
  if (ess.title) kids.push(el("div",{class:"rec-ess"},[ el("span",{class:"rec-ess-k"},"This week:"), el("span",{class:"rec-ess-v"}, ess.title) ]));
  if (ess.promise) kids.push(el("div",{class:"rec-ess"},[ el("span",{class:"rec-ess-k"},"Why:"), el("span",{class:"rec-ess-v"}, ess.promise) ]));
  (ess.bullets||[]).slice(0,2).forEach(b => kids.push(el("div",{class:"rec-ess"},[ el("span",{class:"rec-ess-k"},"📊"), el("span",{class:"rec-ess-v"}, b) ])));
  return kids.length ? kids : [ el("div",{class:"rec-ess"},"Read more below for the full researched recommendation.") ];
}

/* ---------- local projection of the server model ----------
   Used ONLY for the trajectory/sensitivity visuals and what-if
   previews. The authoritative verdict always comes from the
   server's `venture.underwriting`. -------------------------- */
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
function triangular(r, lo, hi, mo){ if (hi <= lo) return lo; const u = r(), f = (mo-lo)/(hi-lo);
  return u < f ? lo + Math.sqrt(u*(hi-lo)*(mo-lo)) : hi - Math.sqrt((1-u)*(hi-lo)*(hi-mo)); }
function interval(base, conf, key){
  const s = UNCERTAINTY[conf] ?? .65, b = BOUNDS[key] || {};
  let lo = base*(1-s), hi = base*(1+s);
  if (b.floor !== undefined) lo = Math.max(b.floor, lo);
  if (b.cap   !== undefined) hi = Math.min(b.cap,   hi);
  return [lo, hi];
}
function sampleOf(r, base, conf, key){ const [lo,hi] = interval(base, conf, key);
  return triangular(r, lo, hi, Math.min(Math.max(base, lo), hi)); }

function inputsOf(venture){
  const m = {}; venture.assumptions.forEach(a => m[a.key] = a);
  for (const k of REQUIRED) if (!m[k] || m[k].value === null || m[k].value === undefined) return null;
  return m;
}
function usableCapital(f){ return f.available_capital + (f.debt_available || 0) - (f.protected_reserve || 0); }

function project(venture, overrides = {}, runs = 900){
  const m = inputsOf(venture);
  if (!m) return null;
  const val = k => (k in overrides ? overrides[k] : m[k].value);
  const f = { ...venture.intake.founder, ...overrides };
  const u = usableCapital(f);
  const r = mulberry32(0x5EED);
  const paths = Array.from({ length:13 }, () => []);
  let ok = 0;

  for (let i = 0; i < runs; i++){
    const s = {};
    REQUIRED.forEach(k => s[k] = sampleOf(r, val(k), m[k].confidence, k));
    let cash = u - s.setup_costs, survived = cash >= 0, last = -Infinity;
    paths[0].push(cash);
    for (let mo = 0; mo < 12; mo++){
      const rev = s.average_basket * s.transactions_per_day * s.days_open_month * RAMP[mo];
      last = rev * s.gross_margin_pct - rev * s.shrinkage_pct
           - s.monthly_rent - s.monthly_payroll - s.monthly_utilities;
      cash += last;
      paths[mo+1].push(cash);
      if (cash < 0) survived = false;
    }
    if (survived && last >= (f.target_monthly_owner_income || 0)) ok++;
  }
  const q = (arr, p) => { const a = [...arr].sort((x,y)=>x-y); return a[Math.min(a.length-1, Math.floor(p*a.length))]; };
  return { prob: ok/runs, band: paths.map(c => ({ p10:q(c,.10), p50:q(c,.50), p90:q(c,.90) })) };
}

/* Mirrors app/engine.py VentureEngine._decision — used only for an instant local
   preview of a candidate shock. The authoritative decision always comes from the
   server's real /sandbox call, never from this. */
function decideLocal(probability, unknownsCount, remaining){
  if (remaining < 0 || probability < .25) return "reject";
  if (unknownsCount > 0) return "conditional";
  if (probability >= .7) return "approve";
  if (probability >= .4) return "conditional";
  return "reject";
}
const prettyKey = k => k.replace(/_/g," ").replace(/\b\w/g, c => c.toUpperCase());
// A venture's own assumption.label is the real, founder-facing name for a key — prefer it
// everywhere a shock/citation/contradiction only carries the raw key; prettyKey is only the
// fallback for a key that (rarely) doesn't map to a current assumption.
const labelForKey = (v, key) => (v.assumptions.find(a => a.key === key) || {}).label || prettyKey(key);

/* Generate candidate rescue shocks from the venture's own sensitivity, not a fixed
   script — so this works for any venture, not just a hand-picked demo case. */
function rescueCandidates(v, det, uw){
  const m = inputsOf(v), c = cur(v);
  if (!m || !det) return [];
  const out = [];

  if (det.remaining < 0){
    const shortfall = -det.remaining;
    out.push({
      name:"Reduce entry cost",
      desc:`Hold demand and margin fixed and lower the cost of opening the door by ${money(shortfall,c)} — a smaller footprint, a turnkey space, or a staged buildout.`,
      shocks:{ setup_costs: Math.max(0, m.setup_costs.value - shortfall) },
    });
    out.push({
      name:"Raise entry capital",
      desc:`Keep the same buildout and close the gap on the funding side instead — additional capital or debt, with the protected reserve untouched.`,
      shocks:{ available_capital: v.intake.founder.available_capital + shortfall },
    });
  }

  // Independent of the capital gate: if monthly operating profit is itself negative, no amount
  // of entry capital fixes that — the venture burns cash every month it's open. Always test the
  // single largest lever on that too, not only when capital already clears, or a venture that
  // fails both tests only ever sees capital-fix candidates that (correctly) show no improvement,
  // with nothing explaining why. Verified live: a venture with det.remaining < 0 AND det.profit < 0
  // showed "REJECT → REJECT" on both capital candidates with no indication the real problem was
  // the monthly economics, not the entry cost.
  if (det.profit < 0 || (uw.break_even_probability_12m ?? 0) < .7){
    const base = det.yearOne;
    let worst = null;
    REQUIRED.filter(k => k !== "setup_costs").forEach(k => {
      const [lo,hi] = interval(m[k].value, m[k].confidence, k);
      const atLo = deterministic(v,{[k]:lo}).yearOne, atHi = deterministic(v,{[k]:hi}).yearOne;
      const gain = Math.max(atLo,atHi) - base;
      if (gain > 0 && (!worst || gain > worst.gain)) worst = { k, value: atLo > atHi ? lo : hi, gain };
    });
    if (worst){
      out.push({
        name:`Improve ${prettyKey(worst.k)}`,
        desc: det.profit < 0
          ? `The monthly operating economics are themselves negative (${money(det.profit,c)}/month) — no amount of entry capital fixes that on its own. This is the single largest lever on it among assumptions still uncertain enough to move.`
          : `This is the single largest lever on year-one cash among the assumptions still uncertain enough to move. Testing it toward the favourable edge of its own confidence interval, not an invented best case.`,
        shocks:{ [worst.k]: worst.value },
      });
    }
  }
  return out;
}

function deterministic(venture, overrides = {}){
  const m = inputsOf(venture);
  if (!m) return null;
  const val = k => (k in overrides ? overrides[k] : m[k].value);
  const f = { ...venture.intake.founder, ...overrides };
  const revenue = val("average_basket") * val("transactions_per_day") * val("days_open_month");
  const profit = revenue * val("gross_margin_pct") - revenue * val("shrinkage_pct")
               - val("monthly_rent") - val("monthly_payroll") - val("monthly_utilities");
  const rampSum = RAMP.reduce((a,b)=>a+b,0);
  const u = usableCapital(f);
  return {
    revenue, profit,
    remaining: u - val("setup_costs"),
    usable: u,
    yearOne: u - val("setup_costs")
           + revenue * rampSum * (val("gross_margin_pct") - val("shrinkage_pct"))
           - 12 * (val("monthly_rent") + val("monthly_payroll") + val("monthly_utilities")),
  };
}

/* ============================================================
   CHARTS
   ============================================================ */
const NS = "http://www.w3.org/2000/svg";
const sv = (n, at) => { const e = document.createElementNS(NS, n); for (const k in at) e.setAttribute(k, at[k]); return e; };
const kfmt = v => (Math.abs(v) >= 1000 ? (v<0?"−":"") + Math.round(Math.abs(v)/1000) + "k" : String(Math.round(v)));

function drawFan(svg, band){
  svg.textContent = "";
  const W=620,H=280,L=54,R=14,T=14,B=32, iw=W-L-R, ih=H-T-B;
  let lo = Math.min(0, ...band.map(b=>b.p10)), hi = Math.max(0, ...band.map(b=>b.p90));
  const pad = (hi-lo)*.08 || 1000; lo -= pad; hi += pad;
  const x = i => L + (i/12)*iw, y = v => T + ih - ((v-lo)/(hi-lo))*ih;

  for (let i=0;i<=4;i++){
    const v = lo + (hi-lo)*i/4, yy = y(v);
    svg.appendChild(sv("line",{x1:L,x2:W-R,y1:yy,y2:yy,class:"gridline"}));
    const t = sv("text",{x:L-8,y:yy+3,class:"axis-txt","text-anchor":"end"}); t.textContent = kfmt(v);
    svg.appendChild(t);
  }
  let d = "M" + band.map((b,i)=>`${x(i)},${y(b.p90)}`).join("L");
  d += "L" + band.map((_,i)=>`${x(12-i)},${y(band[12-i].p10)}`).join("L") + "Z";
  svg.appendChild(sv("path",{ d, fill:"var(--band)", stroke:"var(--band-edge)", "stroke-width":1 }));
  svg.appendChild(sv("line",{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:"var(--series-b)","stroke-width":1.5,"stroke-dasharray":"5 3"}));
  const sl = sv("text",{x:W-R,y:y(0)-6,class:"axis-txt","text-anchor":"end",fill:"var(--series-b)"});
  sl.textContent = "SOLVENCY"; svg.appendChild(sl);
  svg.appendChild(sv("path",{ d:"M"+band.map((b,i)=>`${x(i)},${y(b.p50)}`).join("L"),
    fill:"none", stroke:"var(--ink)", "stroke-width":2, "stroke-linejoin":"round" }));
  svg.appendChild(sv("circle",{cx:x(12),cy:y(band[12].p50),r:4,fill:"var(--ink)",stroke:"var(--surface)","stroke-width":2}));
  [0,3,6,9,12].forEach(mo => {
    const t = sv("text",{x:x(mo),y:H-12,class:"axis-txt","text-anchor":"middle"}); t.textContent = "M"+mo;
    svg.appendChild(t);
  });
}

function drawTornado(svg, venture){
  svg.textContent = "";
  const m = inputsOf(venture);
  const base = deterministic(venture);
  if (!m || !base) return;
  const rows = REQUIRED.map(k => {
    const [lo,hi] = interval(m[k].value, m[k].confidence, k);
    const a = deterministic(venture, { [k]:lo }).yearOne - base.yearOne;
    const b = deterministic(venture, { [k]:hi }).yearOne - base.yearOne;
    return { k, lo:Math.min(a,b), hi:Math.max(a,b), mag:Math.max(Math.abs(a),Math.abs(b)) };
  }).filter(r => r.mag > 1).sort((a,b)=>b.mag-a.mag).slice(0,7);
  if (!rows.length) return;

  const W=400,H=280,L=132,R=14,T=8,B=30, iw=W-L-R, rh=(H-T-B)/rows.length;
  const mx = Math.max(...rows.map(r=>Math.max(Math.abs(r.lo),Math.abs(r.hi)))) * 1.08 || 1;
  const x = v => L + iw/2 + (v/mx)*(iw/2);
  svg.appendChild(sv("line",{x1:x(0),x2:x(0),y1:T,y2:H-B,stroke:"var(--ink-3)","stroke-width":1}));
  rows.forEach((r,i) => {
    const cy = T + i*rh + rh/2, bh = Math.min(14, rh-8);
    if (r.lo < 0) svg.appendChild(sv("rect",{ x:x(r.lo), y:cy-bh/2,
      width:Math.max(1, x(Math.min(0,r.hi))-x(r.lo)-2), height:bh, rx:2, fill:"var(--series-b)" }));
    if (r.hi > 0) svg.appendChild(sv("rect",{ x:x(Math.max(0,r.lo))+2, y:cy-bh/2,
      width:Math.max(1, x(r.hi)-x(Math.max(0,r.lo))-2), height:bh, rx:2, fill:"var(--series-a)" }));
    const t = sv("text",{x:L-10,y:cy+3.5,class:"axis-txt","text-anchor":"end"}); t.textContent = r.k;
    svg.appendChild(t);
  });
  /* Anchor the extreme labels inward so they cannot clip at the viewBox edge. */
  [[-mx,"start"],[0,"middle"],[mx,"end"]].forEach(([v,anchor]) => {
    const t = sv("text",{x:x(v),y:H-12,class:"axis-txt","text-anchor":anchor});
    t.textContent = v===0 ? "0" : (v<0?"−":"+") + kfmt(Math.abs(v)); svg.appendChild(t);
  });
}

/* ============================================================
   SHARED FRAGMENTS
   ============================================================ */
function chip(decision){
  const d = decision || "none";
  return el("span", { class:`chip ${d}` }, [ el("span",{class:"dot"}), DEC_LABEL[d] || "Not assessed" ]);
}
function confMeter(c){
  const n = CONF_RANK[c] || 0;
  return el("span",{class:"conf"},[
    el("span",{class:"conf-ticks"}, [1,2,3,4,5].map(i => el("span",{class:"conf-tick"+(i<=n?" on":"")}))),
    el("span",{class:"conf-lbl"}, c),
  ]);
}
function bandCell(a){
  if (a.value === null || a.value === undefined)
    return el("span",{class:"band"},[ el("span",{class:"band-rng"},"no defensible value") ]);
  const [lo,hi] = interval(a.value, a.confidence, a.key);
  const span = hi - lo || 1;
  const pos = 6 + ((a.value - lo)/span) * 88;
  return el("span",{class:"band"},[
    el("span",{class:"band-track"},[
      el("span",{class:"band-fill",style:"left:6%;right:6%"}),
      el("span",{class:"band-pt",style:`left:${pos.toFixed(1)}%`}),
    ]),
    el("span",{class:"band-rng"}, `${assumptionValue({...a,value:lo})} – ${assumptionValue({...a,value:hi})}`),
  ]);
}

/* ============================================================
   STATE
   ============================================================ */
const S = { ventures:null, venture:null, runtime:null, draft:null, pendingShift:null, recExpanded:false, founderExpanded:false };

/* ============================================================
   VIEW: ventures index
   ============================================================ */
async function viewIndex(){
  setCrumbs([]);
  const view = $("#view");
  view.innerHTML = `<div class="skeleton">Loading ventures…</div>`;
  let list;
  try { list = await api("/api/ventures"); }
  catch (e) { view.innerHTML = ""; view.append(errorCard("Could not load ventures", e.message)); return; }
  S.ventures = list;

  view.innerHTML = "";
  view.append(el("div",{class:"page-head"},[
    el("div",{},[
      el("h1",{},"Ventures"),
    ]),
    el("div",{class:"spacer"}),
    el("div",{class:"row"},[
      el("button",{class:"btn",onclick:loadDemo},"Load demo"),
      el("a",{class:"btn primary",href:"#/new"},"New venture"),
    ]),
  ]));

  // The recommendation algorithm is the centerpiece of the dashboard — render it first, above
  // the venture list, so the founder sees what Cogen recommends they build next before the
  // history of what they've already explored.
  await renderFounderInsights(view);

  if (!list.length){
    view.append(el("div",{class:"empty"},[
      el("h2",{},"No ventures yet"),
      el("a",{class:"btn primary",href:"#/new"},"Start a venture"),
    ]));
    return;
  }

  const wrap = el("div",{class:"vlist"});
  list.forEach(v => {
    const uw = v.underwriting;
    wrap.append(el("a",{class:`vcard ${uw ? uw.decision : "none"}`, href:`#/v/${v.id}/position`},[
      el("button",{class:"vcard-del", title:"Delete venture", "aria-label":`Delete ${v.intake.idea}`,
        onclick:(e)=>{ e.preventDefault(); e.stopPropagation(); deleteVenture(v.id, v.intake.idea); }},"×"),
      el("div",{class:"vcard-top"},[
        chip(uw && uw.decision),
        el("span",{class:"vcard-updated"}, `updated ${ago(v.updated_at)}`),
      ]),
      el("div",{class:"vcard-idea"}, v.intake.idea),
      el("div",{class:"vcard-meta"},[
        el("span",{}, v.intake.location),
        el("span",{class:"dim"},"·"),
        el("span",{}, v.intake.currency || "no currency"),
        v.parent_venture_id ? el("span",{class:"fork-tag"}, v.fork_label || "fork") : null,
      ]),
      // needs_data means no simulation could run at all — its break_even_probability_12m is a
      // safe placeholder (0.0), not a computed result, so showing it as a bold number would read
      // as "will fail" rather than "unknown." Only a decision that actually ran gets the number.
      el("div",{class:"vcard-stat"}, (uw && uw.decision !== "needs_data") ? [
        el("span",{class:"vcard-num"}, pct(uw.break_even_probability_12m)),
        el("span",{class:"vcard-numlbl"},"12-month survival"),
      ] : [
        el("span",{class:"vcard-numlbl"}, uw ? "insufficient evidence to model" : "not analysed yet"),
      ]),
    ]));
  });
  view.append(wrap);
}

// Founder model + weekly recommendations — the cross-venture, cross-session memory surfaced on
// the dashboard. The recommendation is the hero: a full agent that explored in the founder's
// interest direction and returned a numbered, reasoned recommendation. The founder profile is a
// compact sidebar that explains WHY the recommendation fits this person.
async function renderFounderInsights(view, opts){
  const auto = !(opts && opts.auto === false);
  // Replace any previous render, but keep the section pinned right under the page head — never
  // let a background re-render (auto-run completion) drop it below the venture list.
  view.querySelectorAll(".founder-insights").forEach(n => n.remove());
  const section = el("div",{class:"founder-insights"});
  const vlist = view.querySelector(".vlist");
  if (vlist) view.insertBefore(section, vlist);
  else view.append(section);
  let model, recs;
  try { model = await api("/api/founder/model"); } catch (_) { model = null; }
  try { recs = await api("/api/founder/recommendations"); } catch (_) { recs = []; }

  const p = (model && model.profile) || {};
  const interests = (model && model.interests) || [];

  // --- Hero: the weekly recommendation ---
  // The recommendation runs itself and stays out of the way: a compact header row that captures
  // the essentials (this week's direction, why it fits, the headline figure) in a small window
  // above the venture list, with a single expand to read the full researched account. On load, if
  // the recommendation is missing or stale the dashboard fires the agent in the background and
  // swaps in the result when it lands. No button — the founder never has to ask for it.
  const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
  const stale = !recs.length || (Date.now() - new Date(recs[0].created_at).getTime()) > WEEK_MS;
  const latest = recs[0];
  const hero = el("div",{class:"rec-hero" + (S.recExpanded ? " open" : "")});
  hero.append(el("div",{class:"rec-hero-head"},[
    el("div",{class:"rec-hero-title"},"What to build next"),
    el("span",{class:"rec-hero-badge"},"Weekly · AI-researched"),
    el("div",{class:"spacer"}),
    el("button",{class:"rec-expand",type:"button",title:S.recExpanded?"Collapse":"Read the full recommendation",
      onclick:()=>{ S.recExpanded = !S.recExpanded; if (view.isConnected) renderFounderInsights(view,{auto}); }},
      S.recExpanded ? "Hide details ▴" : "Read more ▾"),
  ]));
  if (stale){
    hero.append(el("div",{class:"rec-meta"},[
      el("span",{class:"dim"},"Researching this week's recommendation from your venture history…"),
    ]));
  } else if (!latest){
    hero.append(el("div",{class:"rec-empty"},"No recommendation yet — the agent is preparing one."));
  } else {
    // Compact essentials: the recommendation's headline and the single most decision-relevant
    // figure, so the founder sees "what + why" at a glance without scrolling a long account.
    const essentials = essenceOf(latest.text);
    const count = model.ventures_seen || latest.venture_count;
    hero.append(el("div",{class:"rec-meta"},[
      el("span",{class:"dim"},`Generated ${ago(latest.created_at)} · based on ${latest.venture_count} venture(s)`),
    ]));
    hero.append(el("div",{class:"rec-essentials"}, factlines(essentials)));
    if (S.recExpanded){
      hero.append(el("div",{class:"rec-divider"}));
      hero.append(el("div",{class:"rec-body",html:mdLite(latest.text)}));
    }
  }
  section.append(hero);

  // --- Sidebar: "the founder as the agent sees you" ---
  // Written as prose narration in the agent's own voice (the same voice it uses in chat), not as a
  // disconnected data grid. Defaults to a compact summary; expands to the full narrated profile.
  const profileCard = el("div",{class:"card founder-card"});
  profileCard.append(el("div",{class:"founder-card-head"},[
    el("div",{class:"card-title"},"How I've come to see you"),
    el("div",{class:"spacer"}),
    el("button",{class:"rec-expand",type:"button",title:S.founderExpanded?"Collapse":"Read the full profile",
      onclick:()=>{ S.founderExpanded = !S.founderExpanded; if (view.isConnected) renderFounderInsights(view,{auto}); }},
      S.founderExpanded ? "▴" : "▾"),
  ]));
  if (!model || !model.ventures_seen){
    profileCard.append(el("div",{class:"founder-narration"},
      "You haven't taken me through a venture yet. Start one and I'll learn your capital, your appetite for risk, and the directions you actually care about — then shape every recommendation to fit."));
  } else {
    const c0 = (model.currencies||[""])[0]||"";
    const clauses = [];
    // Capital
    if (p.typical_capital != null)
      clauses.push(`typically works with around ${money(p.typical_capital,c0)} of capital`);
    // Risk tolerance (human phrasing)
    if (p.loss_tolerance_bucket){
      clauses.push({
        conservative: "keeps the downside tightly capped",
        moderate: "takes measured risk but wants a real safety cushion",
        aggressive: "comfortably swings at a bigger upside",
        very_aggressive: "is happy to back a bold bet",
      }[p.loss_tolerance_bucket] || "takes a moderate risk");
    }
    // Time commitment
    if (p.dominant_time_commitment)
      clauses.push(p.dominant_time_commitment === "full-time"
        ? "can give a venture full-time attention"
        : `builds on a ${p.dominant_time_commitment} basis`);
    // Open with a single narrated sentence that weaves the profile together.
    const opening = clauses.length
      ? clauses.slice(0,2).reduce((acc, c, i) =>
          i === 0 ? c : i === clauses.slice(0,2).length-1 ? `${acc}, and ${c}` : `${acc}, ${c}`, "") + "."
      : "I don't have much to go on yet.";
    const outcomes = model.outcomes || [];
    const withSurvival = outcomes.filter(o => o.survival != null);
    const avg = withSurvival.length ? withSurvival.reduce((s,o)=>s+o.survival,0)/withSurvival.length : null;
    // --- Compact summary (always visible): the headline of who you are. ---
    profileCard.append(el("div",{class:"founder-narration"}, "So you're the kind of founder who " + opening));
    if (interests.length){
      profileCard.append(el("p",{class:"founder-interests"},[
        el("span",{class:"dim"},"You keep gravitating toward "),
        ...interests.slice(0,2).map((i,ix)=> [
          el("strong",{}, i),
          ix === 0 && interests.length > 1 ? el("span",{class:"dim"}," and ") : null,
        ]).flat().filter(Boolean),
      ]));
    }
    if (avg != null){
      profileCard.append(el("p",{class:"founder-num"},
        `Your ideas average ${pct(avg)} modelled 12-month survival before any changes.`));
    }
    // --- Full narrated profile (expanded): decision history + narrated trace + interests. ---
    if (S.founderExpanded){
      profileCard.append(el("div",{class:"rec-divider"}));
      if (avg != null){
        profileCard.append(el("p",{}, `Across ${withSurvival.length} ventures you've taken me through, your ideas have averaged around ${pct(avg)} modelled 12-month survival before any changes — so I won't pretend a weak idea is strong, and I'll surface the rescue paths that genuinely lift the odds.`));
      }
      if (interests.length){
        profileCard.append(el("p",{class:"founder-interests"},[
          el("span",{class:"dim"},"You keep gravitating toward "),
          ...interests.map((i,ix)=> [
            el("strong",{}, i),
            ix < interests.length-1 ? el("span",{class:"dim"}, ix < interests.length-2 ? ", " : " and ") : null,
          ]).flat().filter(Boolean),
          el("span",{class:"dim"},", so that's where I'll look first when I recommend what to build next."),
        ]));
      }
      const recent = outcomes.slice(0,3);
      if (recent.length){
        profileCard.append(el("div",{class:"founder-trace",style:"margin-top:12px"},[
          el("div",{class:"founder-trace-t"},"Where you've been"),
          ...recent.map(o =>
            el("div",{class:"founder-trace-row"},[
              el("span",{class:"founder-trace-idea"}, o.idea),
              el("span",{class:"dim"}, o.location),
              o.decision ? el("span",{class:"founder-trace-dec"}, DEC_LABEL[o.decision]||o.decision) : null,
              o.survival != null ? el("span",{class:"dim mono"}, pct(o.survival)) : null,
            ])),
        ]));
      }
    }
  }
  section.append(profileCard);

  // Fire the weekly agent once per dashboard visit when the recommendation is missing or stale.
  // The guard prevents parallel double-fires if the founder navigates while research is running;
  // when it lands, the hero re-renders in place with the fresh recommendation. The re-render
  // passes auto:false so a completed run can never re-trigger itself.
  if (auto && stale && !renderFounderInsights._generating){
    renderFounderInsights._generating = true;
    api("/api/founder/recommendations", { method:"POST" })
      .then(() => {
        renderFounderInsights._generating = false;
        if (view.isConnected) renderFounderInsights(view, { auto:false });
      })
      .catch(() => { renderFounderInsights._generating = false; });
  }

  // The operating model stays current on its own: while the dashboard is open and visible, re-poll
  // the founder model every few minutes so the profile, interests and decision history update as
  // the working twin learns more, without the founder ever having to reload or click anything.
  if (view.isConnected){
    clearTimeout(renderFounderInsights._timer);
    renderFounderInsights._timer = setTimeout(() => {
      if (document.visibilityState === "visible") renderFounderInsights(view, { auto:true });
    }, 5 * 60 * 1000);
  }
}

async function deleteVenture(id, label){
  if (!confirm(`Delete "${label}"?\n\nThis permanently removes the venture, its evidence, and its decision history. This cannot be undone.`)) return;
  try {
    await api(`/api/ventures/${id}`, { method:"DELETE" });
    toast("Venture deleted.");
    S.ventures = null;
    viewIndex();
  } catch (e) { toast(e.message, true); }
}

async function loadDemo(){
  toast("Creating demo venture; research is running in the background…");
  try {
    const v = await api("/api/demo", { method:"POST" });
    location.hash = `#/v/${v.id}/position`;
  } catch (e) { toast(e.message, true); }
}

function errorCard(title, msg){
  return el("div",{class:"notice bad"},[
    el("span",{class:"t"}, title),
    el("span",{class:"b"}, msg),
  ]);
}

/* ============================================================
   VIEW: progressive intake
   ============================================================ */
const FIELD_LABEL = {
  location:"Location", country:"Country", currency:"Currency",
  available_capital:"Available capital", protected_reserve:"Protected reserve",
  target_monthly_owner_income:"Owner income target", max_acceptable_loss:"Max acceptable loss",
  launch_target_months:"Launch window (months)",
};
const NUMERIC = new Set(["available_capital","protected_reserve","target_monthly_owner_income","max_acceptable_loss","launch_target_months"]);

async function viewNew(){
  setCrumbs([{ label:"New venture" }]);
  const view = $("#view");
  if (!S.draft) S.draft = { id:null, idea:"", business_type:"", known:{}, next:null, missing:[], started:false };
  const d = S.draft;

  view.innerHTML = "";
  view.append(el("div",{class:"page-head"},[el("div",{},[
    el("h1",{},"New venture"),
  ])]));

  const box = el("div",{class:"intake"});
  view.append(box);

  if (!d.started){
    const idea = el("textarea",{ placeholder:"e.g. Open a specialty coffee shop with a small pastry counter", value:d.idea });
    const bt   = el("input",{ placeholder:"e.g. specialty coffee retail", value:d.business_type });
    const beginBtn = el("button",{class:"btn primary"},"Begin");
    const go = async () => {
      if (!idea.value.trim() || idea.value.trim().length < 3) return toast("Describe the idea first.", true);
      d.idea = idea.value.trim();
      d.business_type = bt.value.trim() || "general";
      beginBtn.disabled = true; beginBtn.classList.add("busy");
      try {
        const draft = await api("/api/intake", { method:"POST", body: JSON.stringify({ idea:d.idea, known:{} }) });
        d.id = draft.id; d.next = draft.next_question; d.missing = draft.missing_material_fields; d.started = true;
        viewNew();
      } catch (e) {
        toast(e.message, true);
        beginBtn.disabled = false; beginBtn.classList.remove("busy");
      }
    };
    beginBtn.addEventListener("click", go);
    box.append(el("div",{class:"qcard"},[
      el("div",{class:"qtext"},"What do you want to build?"),
      el("div",{class:"stack"},[
        el("label",{class:"field"},["The idea", idea]),
        el("label",{class:"field"},["Business type", bt]),
        el("span",{class:"hint"},"Plain language is fine. You will not be asked for a business plan."),
      ]),
      el("div",{class:"row",style:"margin-top:18px"},[ beginBtn ]),
    ]));
    return;
  }

  /* answered so far */
  const answered = el("div",{class:"answered"});
  Object.entries(d.known).forEach(([k,v]) => {
    answered.append(el("span",{class:"ans"},[
      el("span",{class:"k"}, k),
      el("span",{class:"v"}, String(v)),
      el("button",{ title:"Clear", onclick: async (evt) => {
        const btn = evt.currentTarget; btn.disabled = true;
        delete d.known[k];
        await refreshDraft(btn);
      } },"×"),
    ]));
  });
  if (answered.children.length) box.append(answered);

  const total = Object.keys(FIELD_LABEL).length;
  const done = total - (d.missing?.length || 0);
  box.append(el("div",{class:"progress"},[ el("i",{style:`width:${(done/total*100).toFixed(0)}%`}) ]));

  if (d.next && d.missing.length){
    const field = d.missing[0];
    const isNum = NUMERIC.has(field);
    const input = el("input", isNum
      ? { type:"number", min:"0", step:"any", placeholder:"0" }
      : { type:"text", placeholder: field === "currency" ? "USD" : "" });
    if (field === "currency") input.setAttribute("maxlength","3");

    const contBtn = el("button",{class:"btn primary"},"Continue");
    const submit = async () => {
      const raw = input.value.trim();
      if (!raw) return toast("Enter a value, or it stays unknown.", true);
      d.known[field] = isNum ? Number(raw) : (field === "currency" ? raw.toUpperCase() : raw);
      contBtn.disabled = true; contBtn.classList.add("busy"); input.disabled = true;
      await refreshDraft(contBtn, input);
    };
    contBtn.addEventListener("click", submit);
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); submit(); } });

    const card = el("div",{class:"qcard"},[
      el("div",{class:"eyebrow"}, FIELD_LABEL[field] || field),
      el("div",{class:"qtext"}, d.next),
      input,
    ]);
    if (field === "currency"){
      const sug = el("div",{class:"suggest"});
      ["USD","EUR","GBP","KES","AUD","INR","NGN","ZAR"].forEach(c =>
        sug.append(el("button",{ onclick: () => { input.value = c; submit(); } }, c)));
      card.append(sug);
    }
    card.append(el("div",{class:"row",style:"margin-top:18px"},[
      contBtn,
      el("span",{class:"hint"}, `${d.missing.length} question${d.missing.length>1?"s":""} left`),
    ]));
    box.append(card);
    input.focus();
    return;
  }

  /* complete */
  const createBtn = el("button",{class:"btn primary"},"Create venture");
  createBtn.addEventListener("click", () => createVenture(createBtn));
  box.append(el("div",{class:"qcard"},[
    el("div",{class:"qtext"},"That is everything I need to open the twin."),
    el("p",{class:"hint",style:"margin-bottom:16px"},"Nothing in the real world is committed until you tell it to act."),
    el("div",{class:"row"},[
      createBtn,
      el("button",{class:"btn",onclick:()=>{ S.draft=null; viewNew(); }},"Start over"),
    ]),
  ]));

  async function refreshDraft(busyBtn, busyInput){
    try {
      const draft = await api(`/api/intake/${d.id}`, { method:"POST", body: JSON.stringify({ idea:d.idea, known:d.known }) });
      d.next = draft.next_question; d.missing = draft.missing_material_fields;
      viewNew();
    } catch (e) {
      toast(e.message, true);
      if (busyBtn){ busyBtn.disabled = false; busyBtn.classList.remove("busy"); }
      if (busyInput){ busyInput.disabled = false; }
    }
  }
}

async function createVenture(btn){
  if (btn){ btn.disabled = true; btn.classList.add("busy"); }
  const d = S.draft, k = d.known;
  const payload = {
    idea: d.idea,
    business_type: d.business_type || "general",
    location: k.location,
    country: k.country || null,
    currency: k.currency || null,
    launch_target_months: Number(k.launch_target_months) || 4,
    founder: {
      available_capital: Number(k.available_capital),
      protected_reserve: Number(k.protected_reserve) || 0,
      debt_available: 0,
      target_monthly_owner_income: Number(k.target_monthly_owner_income) || 0,
      max_acceptable_loss: k.max_acceptable_loss !== undefined ? Number(k.max_acceptable_loss) : null,
      time_commitment: "full-time",
      experience: null,
    },
  };
  try {
    const v = await api("/api/ventures", { method:"POST", body: JSON.stringify(payload) });
    S.draft = null;
    toast("Venture created — the agent is already getting to work.");
    location.hash = `#/v/${v.id}/agent`;
  } catch (e) {
    toast(e.message, true);
    if (btn){ btn.disabled = false; btn.classList.remove("busy"); }
  }
}

/* ============================================================
   VIEW: workspace
   ============================================================ */
const TABS = [
  ["agent","Agent"], ["position","Position"], ["model","Model"], ["evidence","Evidence"],
  ["sandbox","Sandbox"], ["roadmap","Roadmap"], ["forks","Forks"],
];

async function viewVenture(id, tab){
  const view = $("#view");
  if (!S.venture || S.venture.id !== id){
    view.innerHTML = `<div class="skeleton">Loading venture…</div>`;
    try { S.venture = await api(`/api/ventures/${id}`); }
    catch (e) { view.innerHTML = ""; view.append(errorCard("Could not load venture", e.message)); return; }
  }
  const v = S.venture;
  setCrumbs([{ label:v.intake.idea }]);

  view.innerHTML = "";
  view.classList.toggle("agent-fill", tab === "agent");
  const uw = v.underwriting;

  /* header */
  const locParts = [v.intake.location, v.intake.currency].filter(Boolean);
  const locEl = el("div",{class:"loc"},[
    el("span",{}, locParts.join(" · ")),
    v.parent_venture_id ? el("span",{class:"fork-badge"}, `fork${v.fork_label ? " · "+v.fork_label : ""}`) : null,
  ]);
  view.append(el("div",{class:"wshead"},[
    el("div",{},[
      el("h1",{}, v.intake.idea),
      locEl,
    ]),
    el("div",{class:"spacer"}),
    el("div",{class:"row"},[
      chip(uw && uw.decision),
      el("a",{class:"btn primary",href:`#/v/${v.id}/agent`},"Talk to the agent"),
    ]),
  ]));

  view.append(el("div",{class:"wsmetrics"},[
    metric("Evidence coverage", uw ? pct(uw.evidence_coverage) : "—"),
    metric("Modelled survival", uw ? pct(uw.break_even_probability_12m) : "—"),
    metric("Evidence records", String(v.evidence.length)),
    metric("Critical unknowns", uw ? String(uw.critical_unknowns.length) : "—"),
    metric("Status", v.status),
  ]));

  const nav = el("nav",{class:"tabs"});
  TABS.forEach(([key,label]) =>
    nav.append(el("a",{ class: key===tab?"on":"", href:`#/v/${v.id}/${key}` }, label)));
  view.append(nav);

  const body = el("div",{class:"stack-l"});
  view.append(body);

  ({ agent:tabAgent, position:tabPosition, model:tabModel, evidence:tabEvidence, sandbox:tabSandbox,
     roadmap:tabRoadmap, forks:tabForks }[tab] || tabAgent)(body, v);
}

const metric = (k, v) => el("div",{class:"wsm"},[ el("span",{class:"k"},k), el("span",{class:"v"},v) ]);

/* ---------- tab: agent (engagement pane) ---------- */
// Tools that only read state — never establish or change a fact. Anything else succeeding this
// turn counts as "did something real"; see the grounded-response check in send() below.
const READ_ONLY_TOOLS = new Set(["inspect_venture","inspect_audit_trail","plan_venture_intake"]);
// The backend passes the raw tool payload through only for create_venture/fork_configuration; ADK
// wraps a tool's string return under some key (name not guaranteed), so scan values rather than
// assume one — a valid venture parses to an object with both an id and intake.idea.
// Shared between the live SSE stream and history replay (GET .../agent/history returns the same
// event shapes) so there is exactly one place that knows how an agent event becomes a hist item —
// verified live that the two had drifted before this existed, which is how a reloaded tab ended up
// showing nothing at all despite the agent's own memory (DatabaseSessionService) surviving fine.
function applyAgentEvent(evt, hist, turnState, ventureId){
  if (evt.type === "user") hist.push({ kind:"user", text:evt.text, attachments:evt.attachments });
  else if (evt.type === "tool_call") hist.push({ kind:"call", name:evt.name, args:evt.args });
  else if (evt.type === "tool_result"){
    hist.push({ kind:"result", name:evt.name });
    turnState.succeeded.push(evt.name);
    if (evt.name === "create_venture"){
      const ref = extractVentureRef(evt.result);
      if (ref && (!ventureId || ref.id !== ventureId)) {
        hist.push({ kind:"venture-link", id:ref.id, idea:ref.idea });
      }
    } else if (evt.name === "fork_configuration"){
      const ref = extractVentureRef(evt.result);
      if (ref) {
        hist.push({ kind:"fork-link", id:ref.id, idea:ref.idea });
        // An approved alternative (fork) auto-opens its own tab, clearly labelled as a fork, so
        // the founder immediately sees the new slate it inherited — aware of the parent it came
        // from (the fork-link callout names it) but a fresh venture to assess on its own.
        setTimeout(() => { location.hash = `#/v/${ref.id}/position`; }, 400);
      }
    }
    // add_founder_evidence/apply_material_change carry a citation (assumption_label, source_title,
    // source_url) — held here rather than rendered as its own log line, so it lands as a numbered
    // source chip on the reply that actually discusses the fact, not floating disconnected from it.
    const citation = extractCitation(evt.result);
    if (citation){
      turnState.citations = turnState.citations || [];
      turnState.citations.push(citation);
    }
    // run_sandbox_experiment's dispatch ack, turned into a "view the experiment" link chip — same
    // slot as an evidence citation, just pointing at the Sandbox tab instead of an external source.
    const sandboxTag = ventureId ? extractSandboxTag(evt.result) : null;
    if (sandboxTag){
      turnState.citations = turnState.citations || [];
      turnState.citations.push({
        assumption_label: "View experiment in Sandbox",
        source_url: `#/v/${ventureId}/sandbox`,
        _sandbox: true,
      });
    }
    // update_agent_todos / list_agent_todos return the working plan — capture it so the chat log
    // can render a live Copilot-style checklist that stays as the agent works.
    if ((evt.name === "update_agent_todos" || evt.name === "list_agent_todos") && ventureId){
      try {
        const payload = typeof evt.result === "string" ? JSON.parse(evt.result) : evt.result;
        const todos = Array.isArray(payload) ? payload : (payload.todos || []);
        if (todos.length) turnState.todos = todos;
      } catch (_) {}
    }
  }
  else if (evt.type === "tool_error") hist.push({ kind:"resulterr", name:evt.name });
  else if (evt.type === "final"){
    const grounded = turnState.succeeded.some(n => !READ_ONLY_TOOLS.has(n));
    const citations = turnState.citations || [];
    hist.push({ kind:"agent", text:evt.text, grounded, citations });
    turnState.succeeded = [];
    turnState.citations = [];
  }
  else if (evt.type === "text") hist.push({ kind:"narrate", text:evt.text });
  else if (evt.type === "retry") hist.push({ kind:"retrying", attempt:evt.attempt, of:evt.of });
  else if (evt.type === "error") hist.push({ kind:"error", text:`Something went wrong: ${evt.message}` });
}

const SPECIALIST_LABELS = {
  finance: "Finance", market: "Market", regulatory: "Regulatory",
  execution: "Execution", adversary: "Adversary (stress test)",
};
// The full specialist pass is the one operation opaque enough that "working…" alone reads as
// stalled — it runs for minutes with no other feedback. This is the agent's actual plan, not a
// decorative checklist: sourced live from WorkflowRunner's own checkpoint records, so it can only
// ever show what has genuinely finished, never guess ahead of it.
function renderPlanChecklist(progress, ventureId){
  const done = new Set(progress.specialists_done || []);
  const total = progress.specialists_total && progress.specialists_total.length
    ? progress.specialists_total : Object.keys(SPECIALIST_LABELS);
  return el("div",{class:"plan"}, total.map(role => {
    const panelSlot = el("div",{style:"display:none;margin:4px 0 4px 21px"});
    const row = el("details",{class:"plan-row"+(done.has(role)?" done":"")});
    row.append(el("summary",{},[
      el("span",{class:"plan-ico"}, done.has(role) ? "✓" : "·"),
      el("span",{}, SPECIALIST_LABELS[role] || role),
    ]));
    row.append(panelSlot);
    // Lazy, on open, rather than fetched for all 5 roles every 2.5s poll — matches the sandbox
    // runs panel's pattern. A role that hasn't started yet just has nothing to show.
    row.addEventListener("toggle", async () => {
      if (!row.open) return;
      panelSlot.style.display = "block";
      panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Loading…"));
      try {
        const runs = await api(`/api/ventures/${ventureId}/subagents?kind=specialist`);
        const run = runs.find(r => r.role === role);
        if (!run){ panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Not started yet.")); return; }
        const events = await api(`/api/ventures/${ventureId}/subagents/${run.id}/events`);
        panelSlot.innerHTML = ""; panelSlot.append(renderSubagentPanel(events));
      } catch (_) { panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Could not load this specialist's steps.")); }
    });
    return row;
  }));
}

// Copilot-style working todo list: the agent's live, checkable plan. Rendered as a compact checklist
// in the chat log (or above it) that the agent updates as it completes each item. Clicking a box
// marks it done; the agent owns the content and the completion logic, the founder can also tick one.
function renderTodoChecklist(todos, ventureId, onRefresh){
  const wrap = el("div",{class:"todo-card"});
  wrap.append(el("div",{class:"todo-card-head"},[
    el("span",{class:"todo-card-title"},"Working plan"),
    el("span",{class:"dim todo-count"},`${todos.filter(t=>t.status==="done").length}/${todos.length}`),
  ]));
  const list = el("div",{class:"todo-list"});
  todos.forEach(todo => {
    const done = todo.status === "done";
    const box = el("button",{class:"todo-box" + (done?" done":""),type:"button","aria-label":done?"Mark not done":"Mark done",
      onclick: async () => {
        // Optimistically flip in place, then persist; re-render via onRefresh on success so the
        // checklist reflects the new state without a full page reload.
        todo.status = done ? "pending" : "done";
        if (onRefresh) onRefresh();
        try {
          await api(`/api/ventures/${ventureId}/todos`, { method:"POST", body: JSON.stringify(todos) });
        } catch (_) { todo.status = done ? "done" : "pending"; if (onRefresh) onRefresh(); }
      }}, done ? "✓" : "");
    list.append(el("div",{class:"todo-row"+(done?" done":"")},[
      box,
      el("span",{class:"todo-text"}, todo.title),
    ]));
  });
  wrap.append(list);
  return wrap;
}

// One fact at a time, not a wall of bundled questions — the founder answers or skips, it slides to
// the next, and only the final step actually sends anything. `onSubmit` receives {key: answerText}
// for every item that got a real (non-skipped) answer.
function renderQuestionStepper(items, onSubmit){
  let idx = 0;
  const answers = {};
  const wrap = el("div",{class:"qstep"});
  function draw(){
    wrap.innerHTML = "";
    const item = items[idx];
    const isLast = idx === items.length - 1;
    const box = el("input",{type:"text",class:"qstep-input",placeholder:"Type your answer, or skip…"});
    box.value = answers[item.key] || "";
    const advance = () => {
      const val = box.value.trim();
      if (val) answers[item.key] = val; else delete answers[item.key];
      if (isLast) onSubmit(answers);
      else { idx++; draw(); }
    };
    const backBtn = el("button",{class:"btn sm",type:"button"},"Back");
    backBtn.disabled = idx === 0;
    backBtn.addEventListener("click", () => { if (idx > 0){ idx--; draw(); } });
    const skipBtn = el("button",{class:"btn sm",type:"button"},"Skip");
    skipBtn.addEventListener("click", () => { delete answers[item.key]; if (isLast) onSubmit(answers); else { idx++; draw(); } });
    const nextBtn = el("button",{class:"btn sm primary",type:"button"}, isLast ? "Send answers" : "Next");
    nextBtn.addEventListener("click", advance);
    box.addEventListener("keydown", e => { if (e.key === "Enter"){ e.preventDefault(); advance(); } });
    wrap.append(
      el("div",{class:"qstep-progress"}, `Fact ${idx+1} of ${items.length}`),
      el("div",{class:"qstep-label"}, item.label || item.key),
      box,
      el("div",{class:"qstep-actions"}, [backBtn, skipBtn, nextBtn]),
    );
    box.focus();
  }
  draw();
  return wrap;
}

function extractVentureRef(payload){
  if (!payload || typeof payload !== "object") return null;
  for (const val of Object.values(payload)){
    try {
      const parsed = typeof val === "string" ? JSON.parse(val) : val;
      if (parsed && parsed.id && parsed.intake && parsed.intake.idea) return { id:parsed.id, idea:parsed.intake.idea };
    } catch (_) { /* not the venture payload */ }
  }
  return null;
}
// Same scan-don't-assume extraction as extractVentureRef, for add_founder_evidence/
// apply_material_change's compact citation envelope ({recorded:{...}, underwriting:{...}}).
function extractCitation(payload){
  if (!payload || typeof payload !== "object") return null;
  for (const val of Object.values(payload)){
    try {
      const parsed = typeof val === "string" ? JSON.parse(val) : val;
      if (parsed && parsed.recorded && parsed.recorded.assumption_label) return parsed.recorded;
    } catch (_) { /* not the citation payload */ }
  }
  return null;
}
// Same scan-don't-assume extraction, for run_sandbox_experiment's {status:"dispatched", run_id,...}
// dispatch ack — turned into a link chip pointing straight at that run in the Sandbox tab, so a
// founder reading "I tested X" in chat doesn't have to go hunt for where that actually happened.
function extractSandboxTag(payload){
  if (!payload || typeof payload !== "object") return null;
  for (const val of Object.values(payload)){
    try {
      const parsed = typeof val === "string" ? JSON.parse(val) : val;
      if (parsed && parsed.run_id && parsed.status === "dispatched") return { run_id: parsed.run_id };
    } catch (_) { /* not the sandbox dispatch payload */ }
  }
  return null;
}
const prettyToolName = name => String(name || "").replace(/_/g," ");
function summarizeArgs(args){
  if (!args || typeof args !== "object") return "";
  return Object.entries(args).slice(0,3).map(([k,val]) => {
    let s = typeof val === "string" ? val : JSON.stringify(val);
    if (s && s.length > 44) s = s.slice(0,44) + "…";
    return `${k}=${s}`;
  }).join("  ");
}

// One live connection at a time for whichever venture's Agent tab is currently mounted — opened
// in tabAgent, closed on every route change (including navigating to a different venture) so it
// never accumulates open connections across a session.
let agentSubscription = null;
function closeAgentSubscription(){
  if (agentSubscription){ agentSubscription.close(); agentSubscription = null; }
}

// Shared between the main chat log and any subagent's own inspect panel (Sandbox tab, a
// specialist's expandable checklist row) — a subagent's SubagentEvent rows get turned into the
// same item shapes via applyAgentEvent before reaching these, so one rendering path serves both.
const GROUPABLE = new Set(["call","result","resulterr","retrying"]);

// Rendered as continuous prose punctuated by small, collapsed "Ran commands" disclosures — not a
// mechanical log of every call. Tool activity is real and inspectable (expand to see it), but the
// account of what happened and why is the primary content, the same way a person narrating their
// own work would talk about what they found, not recite every keystroke.
function groupHistory(items){
  const blocks = [];
  for (const item of items){
    if (GROUPABLE.has(item.kind)){
      const last = blocks[blocks.length - 1];
      if (last && last.type === "commands") last.items.push(item);
      else blocks.push({ type:"commands", items:[item] });
    } else {
      blocks.push({ type:"prose", item });
    }
  }
  return blocks;
}
function renderCommandGroup(items, isLast){
  const failed = items.some(i => i.kind === "resulterr");
  const stillRetrying = items[items.length - 1]?.kind === "retrying";
  // A call and its own result can land in different groups when narration falls between them,
  // so a lone result has no "call" item to count. Distinct tool names survive that split.
  const calls = new Set(items.filter(i => i.name).map(i => i.name)).size || items.length;
  const label = stillRetrying ? "Retrying…"
    : failed ? `Ran ${calls} command${calls===1?"":"s"} — one failed`
    : `Ran ${calls} command${calls===1?"":"s"}`;
  return el("details",{ class:"cmdgroup"+(failed?" bad":""), open:isLast },[
    el("summary",{},[ el("span",{class:"ico"},"›"), el("span",{}, label) ]),
    el("div",{class:"cmdlist"}, items.map(renderItem)),
  ]);
}
// One numbered chip per fact this reply's turn actually recorded — never rendered inline in the
// model's own prose (that would mean trusting the model to correctly pair a claim with a URL, and
// mdLite deliberately never renders model-supplied links at all — see its own docstring). Sourced
// entirely from the structured evidence the tool call itself returned, so a chip can never point
// at a source the model merely claimed rather than one this turn actually wrote to the ledger.
// Linkable (real source_url) and non-linkable (a founder statement or an unsourced model estimate)
// render distinctly on purpose — the same distinction the app's own evidence policy enforces
// everywhere else: a model estimate is not verified evidence just because it's presented fluently.
function renderCitations(citations){
  if (!citations || !citations.length) return null;
  return el("div",{class:"citations"}, citations.map((c, i) => {
    const label = c.assumption_label || c.assumption_key || "Evidence";
    const title = [c.claim, c.source_title].filter(Boolean).join(" — ");
    const chipKids = [ el("span",{class:"n"}, c._sandbox ? "▸" : String(i + 1)), el("span",{}, label) ];
    if (c._sandbox) // in-app hash link: same-tab navigation via the router, never a new tab
      return el("a",{class:"citation-chip",href:c.source_url,title}, chipKids);
    return c.source_url
      ? el("a",{class:"citation-chip",href:c.source_url,target:"_blank",rel:"noopener",title}, chipKids)
      : el("span",{class:"citation-chip nolink",title}, chipKids);
  }));
}
function renderItem(item, isPending){
  if (item.kind === "user"){
    const kids = [];
    if (item.attachments && item.attachments.length){
      kids.push(el("div",{class:"msg-attachments"}, item.attachments.map(a =>
        el("img",{src:`data:${a.mime_type};base64,${a.data}`, alt:"attached image"})
      )));
    }
    if (item.text) kids.push(el("div",{class:"msg-bubble"}, item.text));
    return el("div",{class:"msg user"}, kids);
  }
  if (item.kind === "agent"){
    const textEl = el("div",{class:"msg-text",html:mdLite(item.text)});
    const bubbleKids = isPending
      ? [ el("div",{class:"pending-label"},[ el("span",{class:"dot"}), "Needs your answer to continue" ]), textEl ]
      : [ textEl ];
    const citationsEl = renderCitations(item.citations);
    if (citationsEl) bubbleKids.push(citationsEl);
    const kids = [ el("div",{class:"msg-bubble"}, bubbleKids) ];
    if (item.grounded === false)
      kids.push(el("div",{class:"ungrounded"},"No evidence was written or fact established this turn — read this as unverified until it does."));
    return el("div",{class:"msg agent"+(isPending?" pending-question":"")}, kids);
  }
  if (item.kind === "error") return el("div",{class:"msg err"},[ el("div",{class:"msg-bubble"}, item.text) ]);
  if (item.kind === "system") return el("div",{class:"sysnote"}, item.text);
  if (item.kind === "venture-link")
    return el("div",{class:"venture-callout"},[
      el("span",{},"New venture created — "),
      el("a",{href:`#/v/${item.id}/agent`}, item.idea),
      el("span",{}," →"),
    ]);
  if (item.kind === "fork-link")
    return el("div",{class:"venture-callout"},[
      el("span",{},"Venture configuration forked — "),
      el("a",{href:`#/v/${item.id}/agent`}, item.idea),
      el("span",{}," →"),
    ]);
  // A non-final response is a real, distinct step the model produced before or between tool
  // calls — its own brief account of what it's doing, not the final answer. Rendered exactly
  // like the final message (same prose, no bubble chrome) so the transcript reads as one
  // continuous account building toward the answer, not narration subordinate to a "real" reply.
  if (item.kind === "narrate") return el("div",{class:"msg agent"},[ el("div",{class:"msg-bubble"},[ el("div",{class:"msg-text",html:mdLite(item.text)}) ]) ]);
  if (item.kind === "retrying")
    return el("div",{class:"step retrying"},[ el("span",{class:"ico"},"↻"), el("span",{}, `No usable response — retrying (attempt ${item.attempt}/${item.of})`) ]);
  if (item.kind === "call")
    return el("div",{class:"step call"},[ el("span",{class:"ico"},"→"), el("span",{}, prettyToolName(item.name)), el("span",{class:"args"}, summarizeArgs(item.args)) ]);
  if (item.kind === "resulterr")
    return el("div",{class:"step resulterr"},[ el("span",{class:"ico"},"✕"), el("span",{}, `${prettyToolName(item.name)} failed — retrying`) ]);
  return el("div",{class:"step result"},[ el("span",{class:"ico"},"✓"), el("span",{}, `${prettyToolName(item.name)} returned`) ]);
}

// Turns a subagent run's raw SubagentEvent rows (fetched from /subagents/{run_id}/events) into
// the exact same narration UI the chat log uses — applyAgentEvent already knows how to fold a
// flat {type, ...fields} event into hist items; a SubagentEvent is that same shape one level
// down, under `payload`, so flatten it the same way the SSE client-side parsing does per frame.
function renderSubagentPanel(events){
  const items = [];
  const turnState = { succeeded: [] };
  events.forEach(e => applyAgentEvent({ type:e.type, ...e.payload }, items, turnState));
  if (!items.length) return el("div",{class:"dim",style:"padding:8px 2px"},"No steps recorded yet.");
  const wrap = el("div",{class:"subagent-panel"});
  const blocks = groupHistory(items);
  blocks.forEach((b,i) => wrap.append(
    b.type === "commands" ? renderCommandGroup(b.items, i === blocks.length-1) : renderItem(b.item, false)
  ));
  return wrap;
}

// Compact "where does this venture stand" card shown at the top of the Agent tab when there is
// no conversation transcript yet but the venture has already been analysed. Gives a founder who
// lands here (or returns after the agent worked in the background) immediate observability of the
// decision, survival, coverage and open critical unknowns — instead of a blank log.
function renderAgentStateSummary(v){
  const uw = v.underwriting;
  const c = cur(v);
  const rows = [];
  rows.push(el("div",{class:"sum-row"},[
    el("span",{class:"sum-k"},"Decision"),
    chip(uw && uw.decision),
  ]));
  if (uw && uw.decision !== "needs_data"){
    rows.push(el("div",{class:"sum-row"},[
      el("span",{class:"sum-k"},"12-month survival"),
      el("span",{class:"sum-v"}, pct(uw.break_even_probability_12m)),
    ]));
  }
  rows.push(el("div",{class:"sum-row"},[
    el("span",{class:"sum-k"},"Evidence coverage"),
    el("span",{class:"sum-v"}, uw ? pct(uw.evidence_coverage) : "—"),
  ]));
  rows.push(el("div",{class:"sum-row"},[
    el("span",{class:"sum-k"},"Evidence records"),
    el("span",{class:"sum-v"}, String(v.evidence.length)),
  ]));
  const unknowns = (uw && uw.critical_unknowns) || [];
  rows.push(el("div",{class:"sum-row"},[
    el("span",{class:"sum-k"},"Critical unknowns"),
    el("span",{class:"sum-v"}, unknowns.length ? String(unknowns.length) : "none"),
  ]));
  const kids = [ el("div",{class:"sum-title"},"Current venture state"), el("div",{class:"sum-grid"}, rows) ];
  if (unknowns.length){
    kids.push(el("div",{class:"sum-unknowns"}, unknowns.slice(0,4).map(u => el("div",{class:"sum-unknown"}, u))));
  }
  if (uw && uw.rationale && uw.rationale.length){
    kids.push(el("div",{class:"sum-rationale"}, uw.rationale[0]));
  }
  return el("div",{class:"agent-summary"}, kids);
}

function tabAgent(root, v){
  S.chat = S.chat || {};
  S.chatLoaded = S.chatLoaded || new Set();
  const hist = S.chat[v.id] = S.chat[v.id] || [];

  const card = el("div",{class:"card chatwrap"});
  const log = el("div",{class:"chatlog"});
  card.append(log);

  const input = el("textarea",{class:"composer-input",placeholder:"Ask it to research something, hand it a fact, or tell it what to do next…",rows:"1"});
  const sendBtn = el("button",{class:"composer-send",type:"button","aria-label":"Send"},"↑");
  const ATTACHMENT_TYPES = "image/png,image/jpeg,image/webp,image/gif,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024;
  const fileInput = el("input",{type:"file",accept:ATTACHMENT_TYPES,multiple:true,style:"display:none"});
  const attachBtn = el("button",{class:"composer-icon-btn",type:"button",title:"Attach an image, PDF or document","aria-label":"Attach a file"},"📎");
  const previewRow = el("div",{class:"attach-preview"});
  let pending = []; // { mimeType, data (base64, no prefix), url (for the thumbnail) }
  const isImage = m => /^image\//.test(m);
  function renderPreviews(){
    previewRow.innerHTML = "";
    previewRow.style.display = pending.length ? "flex" : "none";
    previewRow.classList.toggle("docs", pending.some(p => !isImage(p.mimeType)));
    pending.forEach((p, i) => previewRow.append(el("div",{class:"attach-chip"} ,[
      isImage(p.mimeType)
        ? el("img",{src:p.url, alt:"pending attachment"})
        : el("span",{class:"attach-doc"}, p.mimeType.includes("pdf") ? "📄 PDF" : "📝 DOCX"),
      el("button",{type:"button",class:"attach-remove",onclick:() => { pending.splice(i,1); renderPreviews(); }},"×"),
    ])));
  }
  renderPreviews();
  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const files = [...fileInput.files].slice(0, 4 - pending.length);
    fileInput.value = "";
    for (const file of files){
      if (file.size > MAX_ATTACHMENT_BYTES){ alert(`${file.name} is too large (max 15MB).`); continue; }
      const reader = new FileReader();
      reader.onload = () => {
        const url = reader.result; // data:<mime>;base64,<data>
        const data = url.slice(url.indexOf(",") + 1);
        pending.push({ mimeType: file.type, name: file.name, data, url });
        renderPreviews();
      };
      reader.readAsDataURL(file);
    }
  });
  // Filled in once history has had a chance to load, so a returning founder with real prior
  // conversation never sees these next to an empty log for a split second before it's replaced.
  const sugSlot = el("div",{});
  card.append(sugSlot);

  card.append(
    el("div",{class:"composer"},[
      previewRow,
      el("div",{class:"composer-row"},[ attachBtn, fileInput, input, sendBtn ]),
    ]),
    el("div",{class:"hint2"},"Enter to send · Shift+Enter for a new line · 📎 to attach a photo. /research, /sandbox <scenario>, /fork <alternative> invoke that action directly. Irreversible roadmap steps still need your explicit approval regardless of what you tell it here."),
  );
  root.append(card);
  const autoGrow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; };
  input.addEventListener("input", autoGrow);
  autoGrow();

  renderLog();
  // Carry forward any persisted working plan from a previous session (Copilot-style todos), so a
  // returning founder sees the checklist the agent left, not a blank log.
  api(`/api/ventures/${v.id}/todos`).then(todos => {
    if (todos && todos.length){ S.chat[v.id] = S.chat[v.id] || {}; S.chat[v.id].todos = todos; renderLog(); }
  }).catch(()=>{});
  // A returning founder's history fetch, and a fresh venture's kickoff decision, both take a real
  // round trip before anything else appears — verified live at ~2-3s for history alone. Without
  // this, that whole window rendered as a totally empty log, indistinguishable from the page having
  // done nothing. Cleared by whichever branch of loadHistoryThenMaybeKickoff below resolves first:
  // real history replaces it via renderLog(), a kickoff replaces it via send()'s own renderLog(),
  // and the plain "nothing to show yet" case removes it explicitly.
  const initialLoading = el("div",{class:"chat-loading"},[ el("span",{class:"dots"},[el("span"),el("span"),el("span")]), "Loading" ]);
  if (!hist.length){ log.append(initialLoading); log.scrollTop = log.scrollHeight; }
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey){ e.preventDefault(); send(); } });
  // send()'s first argument is the literal message text when given one — never pass the click
  // event straight through, or "prefilled" becomes the Event object and .trim() crashes on it.
  sendBtn.addEventListener("click", () => send());

  loadHistoryThenMaybeKickoff();

  // A sandbox/specialist subagent dispatched from this chat wakes the agent on its own once it
  // finishes (see app/main.py's _wake_main_agent) — this is what delivers that turn live, while
  // the tab stays open, instead of only appearing on the next reload via /agent/history replay.
  closeAgentSubscription();
  agentSubscription = new EventSource(`/api/ventures/${v.id}/agent/subscribe`);
  const subscriptionTurnState = { succeeded: [] };
  agentSubscription.onmessage = (msg) => {
    let evt; try { evt = JSON.parse(msg.data); } catch (_) { return; }
    if (evt.type === "done"){ subscriptionTurnState.succeeded = []; return; }
    applyAgentEvent(evt, hist, subscriptionTurnState, v.id);
    renderLog();
    log.scrollTop = log.scrollHeight;
  };

  // The agent's own memory survives a reload (DatabaseSessionService) — until this, the visible
  // transcript did not, because nothing ever read it back. Verified live: a real due-diligence
  // pass with genuine narration vanished completely on reload, leaving a blank log even though the
  // agent still remembered everything. Runs before the kickoff decision below, not after, so a
  // venture that already has real conversation on file never gets asked twice.
  async function loadHistoryThenMaybeKickoff(){
    if (!hist.length && !S.chatLoaded.has(v.id)){
      try {
        const server = await api(`/api/ventures/${v.id}/agent/history`);
        if (server && server.length){
          const turnState = { succeeded: [] };
          server.forEach(evt => applyAgentEvent(evt, hist, turnState, v.id));
          renderLog(); log.scrollTop = log.scrollHeight;
        }
      } catch (_) { /* no history yet, or couldn't load — fall through to the normal gate below */ }
      S.chatLoaded.add(v.id);
    }

    // A venture that has never been analysed or touched starts the conversation itself the moment
    // this renders — "takes ownership" means it is already moving when the founder arrives, not
    // waiting for a first message. Gated three ways so a page reload (or several) can never re-fire
    // a live model call against the same venture: (a) only while there is no underwriting AND no
    // evidence yet, and history (just loaded above) came back empty too; (b) a localStorage flag
    // set BEFORE the call starts, so this browser never asks twice even if the first attempt fails
    // to produce anything; (c) the flag is set unconditionally by the gate check itself, not by a
    // successful response, so a failed first attempt still counts as "asked".
    const kickoffKey = `cogen-kickoff-${v.id}`;
    let alreadyAsked = false;
    try { alreadyAsked = localStorage.getItem(kickoffKey) === "1"; } catch (_) {}
    const needsKickoff = !hist.length && !v.underwriting && v.evidence.length === 0 && !alreadyAsked;

    if (!hist.length && !needsKickoff){
      initialLoading.remove();
      const sug = el("div",{class:"suggest-row"});
      [
        "Do full due diligence on this venture and tell me if I should commit capital.",
        "What's the single biggest risk here, and what would resolve it?",
        "A competitor just opened two blocks away — what does that change?",
      ].forEach(s => sug.append(el("button",{ onclick:()=>{ input.value = s; send(); } }, s)));
      sugSlot.append(sug);
    }

    if (needsKickoff){
      try { localStorage.setItem(kickoffKey, "1"); } catch (_) {}
      // This exact string is the only call site for the auto-kickoff, so it can name the tool
      // directly instead of leaning on the model to infer it from "do full due diligence" — verified
      // live that leaving the inference to a cheap model was inconsistent: one run skipped the
      // specialist pass and died after a single ad-hoc search, another skipped it but happened to
      // cover similar ground anyway. Naming run_specialist_research removes that coin flip for the
      // one turn every venture is guaranteed to hit.
      send(
        "This is a brand-new venture with no research yet. Call run_specialist_research now to run "
        + "the full five-specialist pass before anything else. Once it returns, read the result, "
        + "close any remaining gaps yourself with search_web and browse_page_for_details, then tell "
        + "me what you found and what's still open, or ask the one thing you genuinely cannot get "
        + "yourself. No introduction — get straight to it.",
        { silent:true },
      );
    }
  }

  // A turn that ends the same way Claude Code stops for a permission or a clarifying answer:
  // set apart from ambient narration, and unmistakably the one thing blocking further progress.
  // Only the most recent message can still be pending — once anything follows it, it's resolved
  // history, not a live prompt — so this is computed fresh per render, never stored on the item.
  function pendingQuestion(){
    const last = hist[hist.length - 1];
    return last && last.kind === "agent" && last.text.trim().endsWith("?") ? last : null;
  }
  function renderLog(){
    log.innerHTML = "";
    const blocks = groupHistory(hist);
    const pending = pendingQuestion();
    // Observability: a venture that has been analysed but has no conversation transcript yet
    // (e.g. created via API, or the founder landed here before ever chatting) must not read as a
    // blank void. Show a compact state summary up front so the founder immediately sees where
    // the venture stands — decision, survival, coverage, and the open critical unknowns — before
    // the suggestion chips. This is the same data the Position/Evidence tabs show, surfaced in
    // the conversation pane so the agent tab is never empty.
    if (!hist.length && v.underwriting){
      log.append(renderAgentStateSummary(v));
    }
    // The agent's working Copilot-style plan, if any — pinned at the top so it reads as the live
    // checklist that drives the conversation, not a buried log line.
    const todos = (S.chat[v.id] && S.chat[v.id].todos) || [];
    if (todos.length){
      log.append(renderTodoChecklist(todos, v.id, renderLog));
    }
    blocks.forEach((b,i) => log.append(
      b.type === "commands" ? renderCommandGroup(b.items, i === blocks.length-1) : renderItem(b.item, b.item === pending)
    ));
    // The venture's own weak/critical assumptions — the exact same list Position's Blindspots
    // card uses — become the step-through facts. Structured and reliable, unlike trying to parse
    // "how many separate things is the agent actually asking" back out of free prose.
    const weak = pending
      ? (v.assumptions || []).filter(a => a.critical && (a.value === null || ["unknown","low"].includes(a.confidence)))
      : [];
    if (weak.length){
      log.append(renderQuestionStepper(weak, (answers) => {
        const lines = weak.filter(w => answers[w.key]).map(w => `${w.label || w.key}: ${answers[w.key]}`);
        send(lines.length ? lines.join("\n")
          : "I don't have answers for these right now — use your best model estimates and continue.");
      }));
    }
    input.placeholder = pending
      ? "Type your answer…"
      : "Ask it to research something, hand it a fact, or tell it what to do next…";
  }
  async function send(prefilled, opts){
    const opt = opts || {};
    const text = (prefilled !== undefined ? prefilled : input.value).trim();
    const attachments = prefilled !== undefined ? [] : pending.map(p => ({ mime_type:p.mimeType, data:p.data, name:p.name || "" }));
    if ((!text && !attachments.length) || sendBtn.disabled) return;
    if (!opt.silent) hist.push({ kind:"user", text, attachments: attachments.length ? attachments : undefined });
    input.value = ""; autoGrow(); pending = []; renderPreviews();
    input.disabled = true; sendBtn.disabled = true;
    renderLog(); log.scrollTop = log.scrollHeight;

    const startedAt = Date.now();
    const timerEl = el("span",{class:"timer"},"0:00");
    const planSlot = el("div",{});
    const thinking = el("div",{class:"thinking"},[
      el("div",{class:"thinking-row"},[ el("span",{class:"dots"},[el("span"),el("span"),el("span")]), "working", timerEl ]),
      planSlot,
    ]);
    // Shown immediately, before the fetch even starts — not after the first SSE byte lands.
    // Verified live: a fresh venture's auto-kickoff can take several real seconds (session
    // creation, the model's first decision) before any tool_call/text event arrives at all, and
    // until now nothing appeared on screen for that whole window — no spinner, no text, nothing —
    // which reads exactly like the page did nothing when "Create venture" was clicked.
    log.append(thinking); log.scrollTop = log.scrollHeight;
    const timerId = setInterval(() => {
      const secs = Math.floor((Date.now() - startedAt) / 1000);
      timerEl.textContent = `${Math.floor(secs/60)}:${String(secs%60).padStart(2,"0")}`;
    }, 1000);
    // The five-specialist research pass is the one step long and opaque enough that "working…"
    // alone reads as stalled — it runs for real minutes with nothing else on screen. Poll the
    // live checkpoint state only while that specific tool is actually in flight, so the founder
    // sees each specialist finish instead of staring at a spinner with no sense of progress.
    let progressId = null;
    function stopProgress(){ if (progressId){ clearInterval(progressId); progressId = null; } planSlot.innerHTML = ""; }
    function startProgress(){
      if (progressId) return;
      const poll = async () => {
        try {
          const p = await api(`/api/ventures/${v.id}/research/progress`);
          if (p && p.status === "running"){
            planSlot.innerHTML = "";
            planSlot.append(renderPlanChecklist(p, v.id));
            log.scrollTop = log.scrollHeight;
          }
        } catch (_) { /* transient — next poll will retry */ }
      };
      poll();
      progressId = setInterval(poll, 2500);
    }
    // Verified live, twice: this model's own prose will confidently describe research it never
    // did and evidence it never wrote — once naming a specific fabricated source, once with zero
    // tool calls beyond a read. Prompting alone did not fully stop it. This is a mechanical,
    // not-fooled-by-wording backstop: track which tools actually succeeded this turn, and if the
    // final answer arrives without a single one that can establish or change a fact, say so
    // under the message itself rather than let the prose stand unchallenged.
    const turnState = { succeeded: [] };

    try {
      const res = await fetch(`/api/ventures/${v.id}/agent/message`, {
        method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ message:text, attachments }),
      });
      if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);
      const reader = res.body.getReader(), decoder = new TextDecoder();
      let buf = "", finished = false;
      while (!finished){
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream:true });
        const frames = buf.split("\n\n");
        buf = frames.pop();
        for (const frame of frames){
          const line = frame.split("\n").find(l => l.startsWith("data: "));
          if (!line) continue;
          let evt; try { evt = JSON.parse(line.slice(6)); } catch(_) { continue; }
          if (evt.type === "done") finished = true;
          else {
            applyAgentEvent(evt, hist, turnState, v.id);
            if (evt.type === "tool_call" && evt.name === "run_specialist_research") startProgress();
            else if ((evt.type === "tool_result" || evt.type === "tool_error") && evt.name === "run_specialist_research") stopProgress();
          }
          renderLog();
          if (!finished) log.append(thinking);
          log.scrollTop = log.scrollHeight;
        }
      }
    } catch (e) {
      hist.push({ kind:"error", text:`Connection error: ${e.message}` });
      renderLog();
    }
    clearInterval(timerId); stopProgress();
    // Capture any working-plan the agent set this turn (Copilot-style todo list) and re-render so
    // the pinned checklist reflects the latest state.
    if (turnState.todos){
      S.chat[v.id] = S.chat[v.id] || {};
      S.chat[v.id].todos = turnState.todos;
    }
    input.disabled = false; sendBtn.disabled = false;
    log.scrollTop = log.scrollHeight; input.focus();
    if (turnState.todos) renderLog();

    // A tool call may have mutated the venture — refresh so every other tab and the header
    // metrics reflect it without the user having to manually reload.
    try { S.venture = await api(`/api/ventures/${v.id}`); route(); } catch (_) {}
  }
}

/* ---------- tab: position ---------- */
// Shared by the manual "Test in sandbox" click and the automatic already-tested check below —
// one rendering for a real SandboxExperiment result either way.
function showRescueResult(result, exp, viaAgent){
  result.className = "rescue-result tested";
  result.innerHTML = "";
  result.append(
    chip(exp.scenario_decision), " ",
    `survival ${pct(exp.baseline_probability)} → ${pct(exp.scenario_probability)} · ${nfmt(exp.simulation_runs)} runs`,
    viaAgent ? el("span",{class:"dim"}," (already tested by the agent)") : null,
  );
}
// Instant, deterministic prose account of the decision — rendered immediately (no network round
// trip) so the founder reads a narrated verdict on first open, before the LLM narrative lands.
function positionNarrative(v, det, uw, c, decision){
  const out = [];
  if (decision === "needs_data"){
    out.push("The model is incomplete. Required financial assumptions are still missing, and I won't invent them just to produce a number — give me the facts and I'll re-run it.");
    return out;
  }
  if (!det){ out.push("I can't yet tell you how this lands — there's not enough on file to model the cash path."); return out; }
  if (det.remaining < 0){
    out.push(`This configuration fails at the door. The business is modelled to generate ${money(det.yearOne,c)} across year one, but opening it takes ${money(inputsOf(v).setup_costs.value,c)} against only ${money(det.usable,c)} of usable capital — a ${money(-det.remaining,c)} shortfall before a single month trades.`);
  } else {
    out.push(`Modelled survival to month twelve is ${pct(uw.break_even_probability_12m)} at ${pct(uw.evidence_coverage)} evidence coverage. You open with ${money(det.remaining,c)} of cushion after setup against a ${money(inputsOf(v).setup_costs.value,c)} buildout.`);
  }
  const weak = (v.assumptions||[]).filter(a => a.critical && (a.value === null || ["unknown","low"].includes(a.confidence)));
  if (weak.length){
    const names = weak.slice(0,2).map(a => a.label).join(" and ");
    out.push(`The real hold-up is ${weak.length === 1 ? "one assumption" : "a couple of assumptions"} I can't yet trust: ${names}. Until that resolves, this stays held at Conditional, no matter how good the arithmetic looks.`);
  }
  return out;
}
function tabPosition(root, v){
  const uw = v.underwriting, c = cur(v);
  if (!uw){
    root.append(el("div",{class:"empty"},[
      el("h2",{},"Not analysed yet"),
      el("a",{class:"btn primary",href:`#/v/${v.id}/agent`},"Go talk to the agent"),
    ]));
    return;
  }
  const det = deterministic(v);
  const decision = uw.decision || "needs_data";

  /* ---------- decision-transition / invalidation narration, if we just caused one ---------- */
  if (S.pendingShift && S.pendingShift.ventureId === v.id){
    const { from, to, cause, invalidated } = S.pendingShift;
    S.pendingShift = null;

    if (from !== to){
      let why;
      if (det && det.remaining < 0)
        why = `Usable capital is now ${money(det.usable,c)} against a setup cost of ${money(inputsOf(v).setup_costs.value,c)}. The capital gate fails before any simulation runs, regardless of demand or margin.`;
      else if (to === "conditional" && uw.critical_unknowns.length)
        why = `The capital gate clears, but ${uw.critical_unknowns.length} critical assumption${uw.critical_unknowns.length>1?"s remain":" remains"} weak — see Blindspots below. The decision is held at Conditional until they resolve.`;
      else
        why = `Modelled survival is now ${pct(uw.break_even_probability_12m)} at ${pct(uw.evidence_coverage)} evidence coverage under the changed configuration.`;
      root.append(el("div",{class:"shift"},[
        el("span",{class:"shift-t"},[ "Decision moved: ", chip(from), " → ", chip(to), el("span",{class:"dim mono"}, cause) ]),
        el("span",{class:"shift-b"}, why),
      ]));
    }
    if (invalidated?.length){
      root.append(el("div",{class:"shift"},[
        el("span",{class:"shift-t"},[
          from === to ? "This didn't move the decision, but it broke something: " : "It also invalidated: ",
          invalidated.join(", "),
        ]),
        el("span",{class:"shift-b"},
          `Changing ${cause ? `"${cause}"` : "this fact"} affects assumptions that depend on it — they've been marked stale and downgraded rather than left silently wrong. Re-resolve them the same way as any other blindspot below.`),
      ]));
    }
  }

  /* ---------- hero ---------- */
  // The instant lead is a deterministic prose narrative (no network), so the founder sees a
  // substantive, narrated decision the moment the tab opens — not a bare number. The LLM-authored
  // narrative (app/narrative.py) then upgrades it in place once it lands.
  const lede = el("div",{class:"verdict-lede"});
  lede.append(...positionNarrative(v, det, uw, c, decision).map(p => el("p",{}, p)));

  const hero = el("div",{class:`hero ${decision}`},[
    el("div",{class:"hero-top"},[ chip(decision), el("span",{class:"asof"}, `as of ${new Date(uw.calculated_at).toLocaleString()}`) ]),
    el("div",{class:"verdict"},[ lede ]),
  ]);
  const vwrap = hero.querySelector(".verdict");
  if (det){
    vwrap.append(el("div",{class:"mechanism"},[
      mrow("Available capital", money(v.intake.founder.available_capital, c)),
      mrow("Less protected reserve", "−" + money(v.intake.founder.protected_reserve, c)),
      mrow("Usable capital", money(det.usable, c)),
      mrow("Less setup cost", "−" + money(inputsOf(v).setup_costs.value, c)),
      mrow("Capital at opening", money(det.remaining, c), true, det.remaining < 0 ? "neg" : "pos"),
    ]));
  }
  if (uw.rationale?.length)
    vwrap.append(el("div",{class:"reasons"}, uw.rationale.map(r => el("div",{class:"reason"}, r))));
  root.append(hero);

  // The LLM-authored narrative (see app/narrative.py) replaces the templated one-liner above once
  // it loads — the same voice the agent already narrates with in chat, applied here instead of a
  // dashboard-only sentence, with the mechanism table and reasons below now genuinely supporting
  // detail rather than the whole account. The templated lede stays up while this loads (and stands
  // as the permanent fallback if synthesis is unavailable), so the tab is never empty or broken.
  api(`/api/ventures/${v.id}/narrative`).then(({ narrative }) => {
    if (!narrative || !lede.isConnected) return;
    const paras = narrative.split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
    if (!paras.length) return;
    lede.replaceWith(el("div",{class:"narrative"}, paras.map(p => el("p",{}, p))));
  }).catch(()=>{});

  /* ---------- blindspots: everything the decision does not yet see ---------- */
  const weak = v.assumptions.filter(a =>
    a.critical && (a.value === null || ["unknown","low"].includes(a.confidence)));
  const blindCard = el("div",{class:"card"},[
    el("div",{class:"card-title"},"Blindspots"),
    el("p",{class:"card-note"},"What the decision above does not yet account for, and the smallest real check that would resolve each one. While any of these stay weak, the strongest rating available is Conditional — favourable arithmetic alone cannot promote a venture past its own unknowns."),
    el("div",{class:"blindspots",style:"margin-top:14px",id:"blindlist"},
      weak.length ? weak.map(a => el("div",{class:"blind",title:`Internal key: ${a.key}`},[
        el("div",{class:"blind-t"}, a.label),
        el("div",{class:"blind-b"}, a.value === null
          ? `No defensible value has been established yet.`
          : `Currently ${a.confidence} confidence — too weak to rely on for a critical assumption.`),
      ])) : [ el("span",{class:"dim"},"No critical assumption is currently weak.") ]),
  ]);
  root.append(blindCard);
  if (weak.length){
    api(`/api/ventures/${v.id}/validation`).then(tasks => {
      const byKey = {}; tasks.forEach(t => { byKey[t.assumption_key] = t; });
      $("#blindlist", blindCard).querySelectorAll(".blind").forEach((node, i) => {
        const task = byKey[weak[i].key];
        if (task) node.append(el("ol",{class:"blind-p"}, task.protocol.map(p => el("li",{},p))));
      });
    }).catch(()=>{});
    api(`/api/ventures/${v.id}/specialists`).then(specs => {
      const rejected = specs.reduce((n,s)=>n+s.rejected_count, 0);
      if (rejected > 0){
        blindCard.append(el("p",{class:"card-note",style:"margin-top:12px"},
          `Also: ${rejected} candidate claim${rejected===1?"":"s"} were rejected by evidence policy during research — see the Evidence tab for what was refused and why.`));
      }
    }).catch(()=>{});
  }

  /* ---------- rescue paths: what would change the answer ---------- */
  if (decision === "reject" || decision === "conditional"){
    const candidates = rescueCandidates(v, det, uw);
    const rescueCard = el("div",{class:"card"},[
      el("div",{class:"card-title"},"What could make this work"),
      el("p",{class:"card-note"},
        candidates.length
          ? "Alternatives the agent tests in the sandbox — approve one and it opens as its own forked venture."
          : "No configuration change closes the gap here — see Blindspots."),
    ]);
    if (candidates.length){
      const list = el("div",{class:"rescues",style:"margin-top:14px"});
      const resultSlots = [];
      candidates.forEach(cand => {
        const proj = project(v, cand.shocks, 400);
        const projDet = deterministic(v, cand.shocks);
        const localDecision = proj ? decideLocal(proj.prob, uw.critical_unknowns.length, projDet.remaining) : null;
        const result = el("span",{class:"rescue-result"}, "Checking for an existing test…");
        resultSlots.push({ cand, result });
        // Approve-as-fork routes through the agent composer — the agent owns the sandbox and the
        // fork, the founder only approves. No manual experiment button: the sandbox is
        // agent-triggered, never founder-triggered.
        const approveBtn = el("button",{class:"btn sm"},"Approve as fork");
        approveBtn.addEventListener("click", () => {
          location.hash = `#/v/${v.id}/agent`;
          setTimeout(() => {
            const input = document.querySelector(".composer-input");
            if (!input) return;
            input.value = `I approve the "${cand.name}" alternative. Fork the venture with these changes: ${Object.entries(cand.shocks).map(([k,val])=>`${k}=${val}`).join(", ")}. Create the fork and start assessing it.`;
            input.focus();
          }, 500);
        });
        const row = el("div",{class:"rescue"},[
          el("div",{class:"rescue-t"},[
            el("span",{class:"rescue-name"}, cand.name),
            el("span",{class:"rescue-shift"},[
              chip(decision), el("span",{class:"arrow"},"→"),
              localDecision ? chip(localDecision) : el("span",{class:"dim"},"—"),
              proj ? el("span",{class:"dim mono"}, ` survival ${pct(uw.break_even_probability_12m)} → ${pct(proj.prob)}`) : null,
            ]),
          ]),
          el("div",{class:"rescue-d"}, cand.desc),
          el("div",{class:"rescue-patch mono"}, Object.entries(cand.shocks).map(([k,val])=>`${labelForKey(v,k)} → ${nfmt(val)}`).join("  ·  ")),
          el("div",{class:"rescue-cta"},[ approveBtn, result ]),
        ]);
        list.append(row);
      });
      rescueCard.append(list);
      // The agent may already have tested one of these on its own initiative (see
      // run_sandbox_experiment + the rescue_candidate the underwriting engine hands it) — check
      // for a real experiment sharing a shocked assumption before defaulting every card to "Not
      // tested yet" and a button nobody has clicked, which is exactly what founders were seeing
      // even after the agent had already done the work.
      api(`/api/ventures/${v.id}/sandbox`).then(experiments => {
        resultSlots.forEach(({ cand, result }) => {
          const candKeys = Object.keys(cand.shocks);
          const match = experiments
            .filter(exp => Object.keys(exp.shocks || {}).some(k => candKeys.includes(k)))
            .sort((a,b) => new Date(b.created_at) - new Date(a.created_at))[0];
          if (match) showRescueResult(result, match, true);
          else { result.textContent = "Not tested yet"; }
        });
      }).catch(() => { resultSlots.forEach(({ result }) => { result.textContent = "Not tested yet"; }); });
    }
    root.append(rescueCard);
  }
}
function mrow(l, val, total, cls){
  return el("div",{class:"mrow"+(total?" total":"")},[
    el("span",{class:"l"},l), el("span",{class:"num "+(cls||"")}, val),
  ]);
}

/* ---------- tab: model ---------- */
// Plain-language account of what the model computes, built from the same local projection that
// draws the charts. The point is to narrate the mechanism — what drives cash, where the risk is,
// and what a single number like "12% survival" actually means — so the charts read as support
// rather than an unexplained visual.
function modelNarrative(v, det, proj, c){
  const m = inputsOf(v);
  const out = [];
  if (!det || !m) return out;
  const revenue = det.revenue, profit = det.profit;
  // 1. The revenue engine.
  out.push(`At its steady state, this business turns around ${money(m.average_basket.value,c)} a sale, ${nfmt(m.transactions_per_day.value)} transactions a day across ${nfmt(m.days_open_month.value)} open days a month — about ${money(revenue,c)} of monthly revenue before anything is spent.`);
  // 2. What it keeps vs what it burns.
  out.push(`Of that, ${pct(m.gross_margin_pct.value)} is gross margin, but ${pct(m.shrinkage_pct.value)} is lost to shrinkage, so roughly ${money(profit,c)} clears the till each month after the rent of ${money(m.monthly_rent.value,c)}, payroll and utilities.`);
  // 3. The capital gate.
  if (det.remaining < 0){
    out.push(`The blocker is the door, not the engine: you open with ${money(det.usable,c)} of usable capital but a ${money(m.setup_costs.value,c)} setup cost, leaving you ${money(det.remaining,c)} short before a single month trades — the simulation can't even start.`);
  } else {
    out.push(`Capital clears the door: ${money(det.usable,c)} usable against a ${money(m.setup_costs.value,c)} setup leaves ${money(det.remaining,c)} as the cushion that has to absorb any slow start.`);
  }
  // 4. The modelled outcome, explained.
  if (proj){
    const medianEnd = proj.band[12].p50;
    out.push(`Across ${900} simulated paths, the median business ends month 12 near ${money(medianEnd,c)} in the bank, but only ${pct(proj.prob)} of paths both stay solvent the whole year and clear the founder's income target — that number is what the headline "12-month survival" actually measures, and ${pct(1-proj.prob)} of the time the model expects cash to run out or the target to be missed.`);
  }
  return out;
}

function tabModel(root, v){
  const m = inputsOf(v);
  const c = cur(v);
  if (!m){
    const missing = REQUIRED.filter(k => {
      const a = v.assumptions.find(x => x.key === k);
      return !a || a.value === null || a.value === undefined;
    });
    root.append(el("div",{class:"notice warn"},[
      el("span",{class:"t"},"The financial model is incomplete"),
      el("span",{class:"b"},`Still unset: ${missing.join(", ")}.`),
    ]));
  } else {
    const proj = project(v);
    const det = deterministic(v);
    // Narrated model overview — prose first, so the founder understands what the model actually
    // says before they read the charts. The charts then serve as the supporting artifact.
    root.append(el("div",{class:"card model-narrative"},[
      el("div",{class:"card-title"},"What the model is telling you"),
      ...modelNarrative(v, det, proj, c).map(p => el("p",{}, p)),
    ]));
    const charts = el("div",{class:"charts"});
    const fan = el("div",{class:"card"},[
      el("div",{class:"card-title"},"Cash trajectory, months 0–12"),
      el("p",{class:"card-note"},"Median path with the 10th–90th percentile band, sampling every input across its own confidence interval."),
      el("div",{class:"chart-scroll"},[ sv("svg",{ id:"fan", viewBox:"0 0 620 280", role:"img",
        "aria-label":"Projected cash position over twelve months" }) ]),
      el("div",{class:"legend"},[
        legend("Median path","background:var(--ink)"),
        legend("10th–90th percentile","background:var(--band);border:1px solid var(--band-edge)", true),
        legend("Solvency line","background:var(--series-b)"),
      ]),
    ]);
    const tor = el("div",{class:"card"},[
      el("div",{class:"card-title"},"What moves the outcome"),
      el("p",{class:"card-note"},"Swing in year-one cash when each input moves across its confidence interval."),
      el("div",{class:"chart-scroll"},[ sv("svg",{ id:"tor", viewBox:"0 0 400 280", role:"img",
        "aria-label":"Sensitivity of year-one cash to each input" }) ]),
      el("div",{class:"legend"},[
        legend("Raises cash","background:var(--series-a)", true),
        legend("Lowers cash","background:var(--series-b)", true),
      ]),
    ]);
    charts.append(fan, tor);
    root.append(charts);
    requestAnimationFrame(() => {
      if (proj) drawFan($("#fan"), proj.band);
      drawTornado($("#tor"), v);
    });
    root.append(el("p",{class:"card-note"},
      "Charts are a local projection of the same model for exploration. The authoritative decision is the server's underwriting result shown above."));
  }

  /* assumption ledger */
  root.append(ledgerCard(v));

  /* Reality changing (a real rent quote, a competitor opening) and correcting an assumption both
     go through the agent, the same as everything else that touches the twin — not a second,
     disconnected form for the founder to fill in by hand. */
  if (v.assumptions.length){
    root.append(el("div",{class:"card"},[
      el("div",{class:"card-title"},"Reality changed"),
      el("p",{class:"card-note"},`“The rent quote came in at ${c} 3,200” · “A competitor opened two blocks away.”`),
      el("div",{class:"row",style:"margin-top:14px"},[
        el("a",{class:"btn primary",href:`#/v/${v.id}/agent`},"Go talk to the agent"),
      ]),
    ]));
  }
}
const legend = (label, style, square) =>
  el("span",{class:"legend-item"},[ el("span",{class:"swatch"+(square?" sq":""),style}), label ]);

function ledgerCard(v){
  const wrap = el("div",{class:"card",style:"padding:0;overflow:hidden"});
  const led = el("div",{class:"ledger",style:"border:none;border-radius:0"});
  led.append(el("div",{class:"lrow head"},[
    el("span",{},"Assumption"), el("span",{style:"text-align:right"},"Value"),
    el("span",{},"Interval at confidence"), el("span",{},"Confidence"), el("span",{}),
  ]));
  v.assumptions.forEach((a, i) => {
    const evs = v.evidence.filter(e => e.assumption_key === a.key);
    const detail = el("div",{class:"detail",hidden:"hidden"});
    if (!evs.length) detail.append(el("div",{class:"ev"},[ el("span",{class:"ev-c dim"},"No evidence admitted for this assumption yet.") ]));
    evs.forEach(e => detail.append(el("div",{class:"ev"},[
      el("span",{class:"ev-c"}, e.claim),
      el("span",{class:"ev-m"},[
        el("span",{class:`tag ${e.evidence_type}`}, e.evidence_type),
        el("span",{class:"mono"}, `confidence: ${e.confidence}`),
        el("span",{}, e.source_title),
        e.source_url ? el("a",{href:e.source_url,target:"_blank",rel:"noopener",class:"mono"},"source ↗")
                     : el("span",{class:"mono dim"},"no source url"),
      ]),
    ])));
    const btn = el("button",{class:"lrow","aria-expanded":"false", title:`Internal key: ${a.key}`, onclick:()=>{
      const open = !detail.hidden; detail.hidden = open; btn.setAttribute("aria-expanded", String(!open));
    }},[
      el("span",{class:"lname"},[ el("span",{class:"n"},a.label) ]),
      el("span",{class:"lval num"},[ assumptionValue(a), a.unit ? el("span",{class:"u"}," "+a.unit) : null ]),
      bandCell(a), confMeter(a.confidence),
      el("span",{class:"crit"}, a.critical ? "CRIT" : ""),
    ]);
    led.append(btn, detail);
  });
  wrap.append(led);
  return wrap;
}

/* ---------- tab: evidence ---------- */
function tabEvidence(root, v){
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Admitted evidence"),
    el("p",{class:"card-note"},`${v.evidence.length} record${v.evidence.length===1?"":"s"} passed the admissibility gate.`),
  ]));
  root.append(ledgerCard(v));

  const panels = el("div",{class:"charts"});
  const specs = el("div",{class:"card"},[
    el("div",{class:"card-title"},"Specialist reports"),
    el("div",{class:"stack",style:"margin-top:12px",id:"specs"},[el("span",{class:"dim"},"Loading…")]),
  ]);
  const cons = el("div",{class:"card"},[
    el("div",{class:"card-title"},"Contradictions"),
    el("div",{class:"stack",style:"margin-top:12px",id:"cons"},[el("span",{class:"dim"},"Loading…")]),
  ]);
  panels.append(specs, cons);
  root.append(panels);

  api(`/api/ventures/${v.id}/specialists`).then(list => {
    const h = $("#specs", specs); h.innerHTML = "";
    if (!list.length) return h.append(el("span",{class:"dim"},"No specialist has run yet."));
    list.forEach(r => h.append(el("div",{class:"risk"},[
      el("span",{},[ el("strong",{}, r.role), el("span",{class:"dim"}, ` — ${r.finding_count} admitted, ${r.rejected_count} rejected`) ]),
    ])));
  }).catch(()=>{ $("#specs",specs).textContent = "Could not load."; });

  api(`/api/ventures/${v.id}/contradictions`).then(list => {
    const h = $("#cons", cons); h.innerHTML = "";
    if (!list.length) return h.append(el("span",{class:"dim"},"None recorded."));
    list.forEach(c => h.append(el("div",{class:"notice warn"},[
      el("span",{class:"t"}, labelForKey(v, c.assumption_key)),
      el("span",{class:"b"}, c.description),
    ])));
  }).catch(()=>{ $("#cons",cons).textContent = "Could not load."; });
}

/* ---------- tab: sandbox ---------- */
const SUBAGENT_STATUS_LABEL = {
  queued: "Queued", running: "Running…", succeeded: "Done", failed: "Failed", crashed: "Crashed",
};

// A sandbox scenario dispatched from chat is now a standalone subagent (app/sandbox_agent.py) —
// it can research a real comparable before shocking the model, runs without blocking the chat
// turn, and several can be in flight at once. This renders that: live status per run, expandable
// to the exact same narration UI the chat log uses, auto-refreshing while any run is still going.
function renderSandboxRuns(root, v){
  const list = el("div",{class:"stack"},[el("span",{class:"dim"},"Loading…")]);
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Scenario runs"),
    el("p",{class:"card-note"},"Dispatched from chat — runs in the background and reports back on its own."),
    el("div",{style:"margin-top:12px"},[list]),
  ]));
  let pollId = null;
  const eventsCache = new Map(); // run_id -> events, so an expanded panel doesn't refetch on every poll

  async function load(){
    let runs;
    try { runs = await api(`/api/ventures/${v.id}/subagents?kind=sandbox`); }
    catch (_) { list.innerHTML = ""; list.append(el("span",{class:"dim"},"Could not load scenario runs.")); return; }

    list.innerHTML = "";
    if (!runs.length){ list.append(el("span",{class:"dim"},"No scenario runs yet — ask the agent to test one.")); }
    runs.forEach(run => {
      const panelSlot = el("div",{style:"margin-top:10px;display:none"});
      const details = el("details",{class:"cmdgroup"+(run.status==="failed"||run.status==="crashed"?" bad":"")});
      details.append(el("summary",{},[
        el("span",{class:"ico"},"›"),
        el("span",{}, run.input_payload && run.input_payload.name || "Scenario"),
        el("span",{class:"dim",style:"margin-left:8px;font-size:11.5px"}, SUBAGENT_STATUS_LABEL[run.status] || run.status),
      ]));
      details.append(panelSlot);
      details.addEventListener("toggle", async () => {
        if (!details.open) return;
        panelSlot.style.display = "block";
        if (eventsCache.has(run.id)){ panelSlot.innerHTML = ""; panelSlot.append(renderSubagentPanel(eventsCache.get(run.id))); return; }
        panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Loading…"));
        try {
          const events = await api(`/api/ventures/${v.id}/subagents/${run.id}/events`);
          eventsCache.set(run.id, events);
          panelSlot.innerHTML = ""; panelSlot.append(renderSubagentPanel(events));
        } catch (_) { panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Could not load this run's steps.")); }
      });
      list.append(details);
    });

    const anyRunning = runs.some(r => r.status === "queued" || r.status === "running");
    if (anyRunning && !pollId) pollId = setInterval(load, 2500);
    if (!anyRunning && pollId){ clearInterval(pollId); pollId = null; }
  }
  load();
}

function tabSandbox(root, v){
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Test a scenario"),
    el("p",{class:"card-note"},`“What if rent goes to ${cur(v)} 4,500?”`),
    el("div",{class:"row",style:"margin-top:14px"},[
      el("a",{class:"btn primary",href:`#/v/${v.id}/agent`},"Go talk to the agent"),
    ]),
  ]));

  renderSandboxRuns(root, v);

  const list = el("div",{class:"stack",id:"exps"},[el("span",{class:"dim"},"Loading…")]);
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Past experiments"),
    el("p",{class:"card-note"},"Click one to read what the agent actually ran and found — the same narration it produced live."),
    el("div",{style:"margin-top:12px"},[list]),
  ]));
  loadExperiments();

  async function loadExperiments(){
    let runs = [];
    try { runs = await api(`/api/ventures/${v.id}/subagents?kind=sandbox`); } catch (_) {}
    try {
      const xs = await api(`/api/ventures/${v.id}/sandbox`);
      list.innerHTML = "";
      if (!xs.length) return list.append(el("span",{class:"dim"},"No experiments run yet."));
      xs.slice().reverse().forEach(x => {
        const dp = (x.scenario_probability ?? 0) - (x.baseline_probability ?? 0);
        // Each past experiment expands to the agent's own narrated account of that run — the
        // same SubagentEvent stream the live "Scenario runs" panel shows, matched by scenario
        // name. A founder can read what was actually researched and computed, not just a number.
        const panelSlot = el("div",{style:"margin-top:10px;display:none"});
        const details = el("details",{class:"cmdgroup"});
        details.append(el("summary",{},[
          el("span",{class:"ico"},"›"),
          el("span",{}, x.name),
          el("span",{class:"dim",style:"margin-left:8px;font-size:11.5px"},
            `${pct(x.baseline_probability)} → ${pct(x.scenario_probability)}`),
        ]));
        details.append(
          el("div",{style:"padding:6px 2px"},[
            el("div",{class:"row"},[
              chip(x.baseline_decision), el("span",{class:"dim"},"→"), chip(x.scenario_decision),
            ]),
            el("div",{class:"mono",style:"font-size:11.5px;color:var(--ink-2);margin-top:8px"},
              Object.entries(x.shocks).map(([k,val])=>`${labelForKey(v,k)} → ${nfmt(val)}`).join("  ·  ")),
            el("div",{class:"mono",style:"font-size:11.5px;color:var(--ink-3);margin-top:4px"},
              `survival ${pct(x.baseline_probability)} → ${pct(x.scenario_probability)} (${dp>=0?"+":"−"}${Math.abs(dp*100).toFixed(1)} pts) · ${nfmt(x.simulation_runs)} runs`),
          ]),
          panelSlot,
        );
        details.addEventListener("toggle", async () => {
          if (!details.open) return;
          panelSlot.style.display = "block";
          if (panelSlot.dataset.loaded === "1") return;
          panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Loading…"));
          const run = runs.find(r => r.input_payload && r.input_payload.name === x.name);
          if (!run){ panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"No narrated run recorded for this experiment.")); return; }
          try {
            const events = await api(`/api/ventures/${v.id}/subagents/${run.id}/events`);
            panelSlot.innerHTML = ""; panelSlot.append(renderSubagentPanel(events));
            panelSlot.dataset.loaded = "1";
          } catch (_) { panelSlot.innerHTML = ""; panelSlot.append(el("span",{class:"dim"},"Could not load this run's steps.")); }
        });
        list.append(details);
      });
    } catch (e) { list.innerHTML = ""; list.append(el("span",{class:"dim"},"Could not load experiments.")); }
  }
}

/* ---------- tab: roadmap ---------- */
function tabRoadmap(root, v){
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Execution gates"),
    el("p",{class:"card-note"},"Tell the agent when a step is done — irreversible ones need your explicit go-ahead first."),
  ]));
  const gates = el("div",{class:"gates"});
  v.roadmap.forEach(s => {
    const flags = el("div",{class:"flags"});
    if (s.irreversible) flags.append(el("span",{class:"tag"},"irreversible"));
    if (s.requires_user_approval) flags.append(el("span",{class:"tag"},"needs approval"));
    if (s.official_source_required) flags.append(el("span",{class:"tag official"},"official source"));
    gates.append(el("div",{class:`gate ${s.status}`},[
      el("span",{class:"phase"}, s.phase),
      el("div",{},[
        el("div",{class:"t"}, s.title),
        el("div",{class:"d"}, s.description),
        flags.children.length ? flags : null,
      ]),
      el("span",{class:"dim",style:"font-size:11.5px"}, s.status),
    ]));
  });
  root.append(gates);
}

/* ---------- tab: forks ---------- */
function tabForks(root, v){
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Fork this venture"),
    el("p",{class:"card-note"},"Ask the agent to branch a different location or format — the parent is never touched."),
    el("div",{class:"row",style:"margin-top:14px"},[
      el("a",{class:"btn primary",href:`#/v/${v.id}/agent`},"Go talk to the agent"),
    ]),
  ]));

  const list = el("div",{class:"vlist",id:"forklist"},[el("span",{class:"dim"},"Loading…")]);
  root.append(el("div",{class:"card"},[
    el("div",{class:"card-title"},"Existing forks"),
    el("div",{style:"margin-top:12px"},[list]),
  ]));
  api(`/api/ventures/${v.id}/forks`).then(fs => {
    list.innerHTML = "";
    if (!fs.length) return list.append(el("span",{class:"dim"},"No forks yet."));
    fs.forEach(f => list.append(el("a",{class:"vcard none",href:`#/v/${f.child_venture_id}/position`,onclick:()=>{S.venture=null;}},[
      el("div",{class:"vcard-top"},[ el("span",{class:"fork-tag"},"fork") ]),
      el("div",{class:"vcard-idea"}, f.label),
      el("div",{class:"vcard-meta"},[ el("span",{}, f.reason) ]),
      f.invalidated_assumptions.length
        ? el("div",{class:"vcard-meta mono"},[ el("span",{class:"dim"}, `invalidated: ${f.invalidated_assumptions.join(", ")}`) ])
        : null,
    ])));
  }).catch(()=>{ list.innerHTML=""; list.append(el("span",{class:"dim"},"Could not load forks.")); });
}

/* ============================================================
   ROUTER
   ============================================================ */
function setCrumbs(items){
  const c = $("#crumbs");
  c.innerHTML = "";
  items.forEach(it => { c.append(el("span",{class:"sep"},"/"), el("span",{class:"cur"}, it.label)); });
}

function route(){
  const h = location.hash.replace(/^#/, "") || "/";
  const parts = h.split("/").filter(Boolean);
  // Reset here, not just in viewVenture: every other route (index, new-venture) reaches this same
  // #view element, and none of them re-toggle the class themselves.
  $("#view").classList.remove("agent-fill");
  // Only tabAgent reopens this (right after this same close, if that's where we're headed) — for
  // every other destination this is the one place that actually closes it, so navigating off the
  // Agent tab never leaves a live connection (and its server-side queue) behind.
  closeAgentSubscription();
  if (parts[0] === "new") return viewNew();
  if (parts[0] === "v" && parts[1]) return viewVenture(parts[1], parts[2] || "position");
  S.venture = null;
  return viewIndex();
}
window.addEventListener("hashchange", route);

/* theme */
$("#theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  try { localStorage.setItem("cogen-theme", dark ? "light" : "dark"); } catch (_) {}
  route();
});
try {
  const saved = localStorage.getItem("cogen-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch (_) {}

// Drives .agent-fill's viewport-height calc — measured rather than hardcoded so the agent tab's
// locked layout still matches exactly if the topbar's own height ever changes.
const setTopbarHeightVar = () => {
  const tb = document.querySelector(".topbar");
  if (tb) document.documentElement.style.setProperty("--topbar-h", `${tb.offsetHeight}px`);
};
window.addEventListener("resize", setTopbarHeightVar);
setTopbarHeightVar();

route();
