const $ = (selector) => document.querySelector(selector);
const state = { venture: null };

const money = (n) => n == null ? "—" : new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(n);
const pct = (n) => n == null ? "—" : `${Math.round(n * 100)}%`;

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function formPayload(form) {
  const data = new FormData(form);
  return {
    idea: data.get("idea"),
    business_type: data.get("business_type"),
    location: data.get("location"),
    launch_target_months: Number(data.get("launch_months")),
    founder: {
      available_capital: Number(data.get("capital")),
      protected_reserve: Number(data.get("reserve") || 0),
      debt_available: 0,
      target_monthly_owner_income: Number(data.get("income") || 0),
      max_acceptable_loss: Number(data.get("max_loss") || 0),
      time_commitment: data.get("time_commitment"),
      experience: data.get("experience") || null,
    },
  };
}

function render(venture) {
  state.venture = venture;
  $("#dashboard").classList.remove("hidden");
  $("#analyzeButton").disabled = false;
  $("#ventureTitle").textContent = venture.intake.idea;
  $("#ventureMeta").textContent = `${venture.intake.location} · ${money(venture.intake.founder.available_capital)} available capital`;

  const uw = venture.underwriting;
  const decision = uw?.decision || "needs_data";
  const badge = $("#decisionBadge");
  badge.textContent = decision.replaceAll("_", " ").toUpperCase();
  badge.className = `decision-badge ${decision}`;
  $("#coverage").textContent = pct(uw?.evidence_coverage);
  $("#coverageBar").style.width = pct(uw?.evidence_coverage || 0);
  $("#probability").textContent = pct(uw?.break_even_probability_12m);
  $("#capitalRemaining").textContent = uw?.capital_remaining_after_setup == null ? "—" : `KES ${money(uw.capital_remaining_after_setup)}`;
  $("#unknownCount").textContent = String(uw?.critical_unknowns?.length ?? venture.assumptions.filter(a => a.critical && ["unknown","low"].includes(a.confidence)).length);

  $("#assumptions").innerHTML = venture.assumptions.map(a => `
    <div class="item">
      <div class="item-head"><span class="item-title">${escapeHtml(a.label)}</span><span class="conf ${a.confidence}">${a.confidence}</span></div>
      <div class="item-meta">${a.value == null ? "No defensible value yet" : `${money(a.value)} ${escapeHtml(a.unit || "")}`} ${a.critical ? "· CRITICAL" : ""}</div>
      ${a.source_note ? `<div class="item-meta">${escapeHtml(a.source_note)}</div>` : ""}
    </div>`).join("");

  $("#risks").innerHTML = (uw?.biggest_risks || ["Run underwriting to generate an adversarial risk ranking."])
    .map(r => `<div class="item">${escapeHtml(r)}</div>`).join("");
  $("#rationale").innerHTML = (uw?.rationale || ["No decision has been made yet."])
    .map(r => `<div class="item">${escapeHtml(r)}</div>`).join("");

  $("#roadmap").innerHTML = venture.roadmap.map(step => `
    <article class="roadmap-step ${step.status}">
      <div>
        <span class="phase">${escapeHtml(step.phase)} · ${escapeHtml(step.status.toUpperCase())}</span>
        <h3>${escapeHtml(step.title)}</h3>
        <p>${escapeHtml(step.description)}</p>
      </div>
      ${step.status === "ready" ? `<button class="button secondary complete-step" data-id="${step.id}">Mark complete</button>` : ""}
    </article>`).join("");

  $("#changeAssumption").innerHTML = venture.assumptions
    .filter(a => typeof a.value === "number")
    .map(a => `<option value="${a.key}">${escapeHtml(a.label)}</option>`).join("");

  document.querySelectorAll(".complete-step").forEach(button => {
    button.addEventListener("click", async () => {
      try {
        const updated = await api(`/api/ventures/${venture.id}/roadmap/${button.dataset.id}/complete`, { method: "POST" });
        render(updated);
        toast("Roadmap state updated.");
      } catch (err) { toast(err.message); }
    });
  });
  window.scrollTo({ top: $("#dashboard").offsetTop - 10, behavior: "smooth" });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
}

$("#ventureForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const venture = await api("/api/ventures", { method: "POST", body: JSON.stringify(formPayload(event.currentTarget)) });
    render(venture);
    toast("Venture persisted. It has not been approved yet.");
  } catch (err) { toast(err.message); }
});

$("#analyzeButton").addEventListener("click", async () => {
  if (!state.venture) return;
  const button = $("#analyzeButton");
  button.disabled = true;
  button.textContent = "Researching + attacking…";
  try {
    const venture = await api(`/api/ventures/${state.venture.id}/analysis/sync`, { method: "POST" });
    render(venture);
    toast("Underwriting complete. Weak facts remain weak on purpose.");
  } catch (err) { toast(err.message); }
  finally { button.disabled = false; button.textContent = "Attack the idea"; }
});

$("#demoButton").addEventListener("click", async () => {
  try {
    const venture = await api("/api/demo", { method: "POST" });
    render(venture);
    toast("Loaded deterministic demo. Values marked DEMO are not live market facts.");
  } catch (err) { toast(err.message); }
});

$("#applyChangeButton").addEventListener("click", async () => {
  if (!state.venture) return;
  const key = $("#changeAssumption").value;
  const newValue = Number($("#changeValue").value);
  const summary = $("#changeSummary").value.trim();
  if (!key || !Number.isFinite(newValue) || !summary) return toast("Choose an assumption, new value and explain what changed.");
  try {
    const venture = await api(`/api/ventures/${state.venture.id}/changes`, {
      method: "POST",
      body: JSON.stringify({ assumption_key: key, new_value: newValue, summary, confidence: $("#changeConfidence").value }),
    });
    render(venture);
    toast("Material change applied; downstream underwriting was recomputed.");
  } catch (err) { toast(err.message); }
});
