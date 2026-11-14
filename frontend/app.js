/* ================= CONSTANTS ================= */

const STATUS = {
  NEW:           { label: "New",           dot: "bg-[#8A8D93]" },
  CLEAN:         { label: "Clean",         dot: "bg-resolution" },
  AUTO_RESOLVED: { label: "Auto-resolved", dot: "bg-accent" },
  VENDOR_QUERY:  { label: "Vendor query",  dot: "bg-quirk" },
  NEEDS_REVIEW:  { label: "Needs review",  dot: "bg-warn" },
  REJECTED:      { label: "Rejected",      dot: "bg-danger" },
  RESOLVED:      { label: "Resolved",      dot: "bg-resolution" },
  HUMAN_APPROVED: { label: "Human approved", dot: "bg-resolution" },
};

const SEVERITY = {
  low:      { text: "text-[#A88A1A]", bar: "border-[#A88A1A]", tint: "bg-raised" },
  medium:   { text: "text-[#D2742A]", bar: "border-[#D2742A]", tint: "bg-raised" },
  high:     { text: "text-danger",    bar: "border-danger",    tint: "bg-raised" },
  critical: { text: "text-danger",    bar: "border-danger",    tint: "bg-danger/10" },
};

const ACTION = {
  AUTO_APPROVE:           { label: "Auto-approve",           text: "text-accent", bg: "bg-accent/10", border: "border-accent/40", fill: "bg-accent" },
  AUTO_APPROVE_WITH_NOTE: { label: "Auto-approve with note", text: "text-accent", bg: "bg-accent/10", border: "border-accent/40", fill: "bg-accent" },
  REQUEST_VENDOR_INFO:    { label: "Request vendor info",    text: "text-quirk",  bg: "bg-quirk/10",  border: "border-quirk/40",  fill: "bg-quirk" },
  REJECT:                 { label: "Reject",                 text: "text-danger", bg: "bg-danger/10", border: "border-danger/40", fill: "bg-danger" },
  ESCALATE:               { label: "Escalate to human",      text: "text-warn",   bg: "bg-warn/10",   border: "border-warn/40",   fill: "bg-warn" },
};

const MEMORY_TYPE = {
  resolution_case:     { label: "Past resolution",     text: "text-resolution", border: "border-resolution" },
  vendor_profile:      { label: "Vendor quirk",        text: "text-quirk",      border: "border-quirk" },
  approver_preference: { label: "Approver preference", text: "text-pref",       border: "border-pref" },
};

/* ================= STATE ================= */

let selectedId = null;
let invoicesCache = [];
let memoryOn = true;
let lastDraft = "";
let lastRecalled = [];
let lastCited = new Set();

function formatINR(amount) {
  if (amount === null || amount === undefined) return "—";
  return "₹" + amount.toLocaleString("en-IN");
}

/* ================= INBOX ================= */

function renderInbox(invoices) {
  const list = document.getElementById("inbox-list");
  document.getElementById("inbox-count").textContent = invoices.length;

  if (invoices.length === 0) {
    list.innerHTML = `<p class="p-4 text-sm text-faint">No invoices yet.</p>`;
    return;
  }

  list.innerHTML = invoices
    .map((inv) => {
      const isSelected = inv.id === selectedId;
      const st = STATUS[inv.status] || { label: inv.status, dot: "bg-faint" };
      return `
      <div data-id="${inv.id}"
           class="invoice-card relative px-4 py-3 border-b border-line cursor-pointer hover:bg-raised ${isSelected ? "bg-raised" : ""}">
        ${isSelected ? `<span class="absolute left-0 top-0 bottom-0 w-[2px] bg-accent"></span>` : ""}
        <div class="flex items-center justify-between">
          <span class="font-mono font-medium text-[12px] text-ink">${inv.id}</span>
          <span class="flex items-center gap-1.5 font-mono font-medium text-[11px] text-dim">
            <span class="w-1.5 h-1.5 rounded-full ${st.dot}"></span>${st.label}
          </span>
        </div>
        <div class="mt-1 text-[13px] text-dim">${inv.vendor_name}</div>
        <div class="mt-1.5 text-[16px] font-semibold tabular-nums">${formatINR(inv.amount)}</div>
        ${
          inv.exception_types.length
            ? `<div class="mt-2 flex flex-wrap gap-1">${inv.exception_types
                .map(
                  (t) =>
                    `<span class="font-mono text-[10px] px-1.5 py-0.5 rounded-sm border border-danger/30 text-danger">${t}</span>`
                )
                .join("")}</div>`
            : ""
        }
      </div>`;
    })
    .join("");
}

function setStatus(id, status) {
  const inv = invoicesCache.find((i) => i.id === id);
  if (inv) {
    inv.status = status;
    renderInbox(invoicesCache);
  }
}

/* ================= INVOICE DETAIL ================= */

function cell(value, bad) {
  return `<td class="px-3 py-2 tabular-nums ${bad ? "text-danger bg-danger/10" : "text-ink"}">${value}</td>`;
}

function vrow(label, value, bad, mono) {
  return `
    <div class="flex justify-between gap-4 py-2 border-b border-line text-[13px]">
      <span class="text-dim">${label}</span>
      <span class="text-right ${mono ? "font-mono text-[12px]" : ""} ${bad ? "text-danger font-medium" : "text-ink"}">${value}</span>
    </div>`;
}

function renderDetail(d) {
  const { invoice, po, grn, vendor, exceptions } = d;
  const panel = document.getElementById("detail-panel");

  const rows = invoice.lines
    .map((line) => {
      const poLine = po?.lines.find((l) => l.item === line.item);
      const grnLine = grn?.received.find((l) => l.item === line.item);
      const qtyBad =
        (poLine && poLine.qty !== line.qty) || (grnLine && grnLine.qty !== line.qty);
      const priceBad = !poLine || poLine.unit_price !== line.unit_price;
      return `
        <tr class="border-t border-line">
          <td class="px-3 py-2 text-dim">${line.item} · qty</td>
          ${cell(line.qty, qtyBad)}
          ${cell(poLine ? poLine.qty : "—", false)}
          ${cell(grnLine ? grnLine.qty : "—", false)}
        </tr>
        <tr class="border-t border-line">
          <td class="px-3 py-2 text-dim">${line.item} · unit price</td>
          ${cell(formatINR(line.unit_price), priceBad)}
          ${cell(poLine ? formatINR(poLine.unit_price) : "—", false)}
          ${cell("—", false)}
        </tr>`;
    })
    .join("");

  const totalBad = !po || po.total !== invoice.total;
  const gstinBad = invoice.gstin !== vendor.gstin;
  const bankBad = invoice.bank_account !== vendor.bank_account;

  const exceptionBlocks =
    exceptions.length === 0
      ? `<p class="text-[13px] text-resolution">No exceptions. Clean invoice.</p>`
      : exceptions
          .map((e) => {
            const sev = SEVERITY[e.severity] || SEVERITY.low;
            return `
            <div class="border-l-2 ${sev.bar} ${sev.tint} pl-3 pr-3 py-2.5 mb-2">
              <div class="flex items-center gap-3">
                <span class="font-mono font-medium text-[12px]">${e.type}</span>
                <span class="text-[10px] tracking-widest uppercase ${sev.text}">${e.severity}</span>
              </div>
              <div class="text-[13px] text-dim mt-0.5">${e.detail}</div>
            </div>`;
          })
          .join("");

  panel.innerHTML = `
    <div class="flex items-start justify-between mb-7">
      <div>
        <div class="eyebrow mb-1.5">Invoice</div>
        <h2 class="font-display text-2xl font-semibold tracking-tight">${invoice.id}</h2>
        <div class="text-[13px] text-dim mt-1">
          ${vendor.name} <span class="text-faint">·</span> ${invoice.invoice_number} <span class="text-faint">·</span> ${invoice.date}
        </div>
      </div>
      <div class="text-right">
        <div class="eyebrow mb-1.5">PO</div>
        <div class="font-mono font-medium text-[13px]">${invoice.po_number || `<span class="text-danger">MISSING</span>`}</div>
      </div>
    </div>

    <div class="mb-7">
      <div class="eyebrow mb-2">Exceptions</div>
      ${exceptionBlocks}
    </div>

    <div class="mb-7">
      <div class="eyebrow mb-2">Three-way match</div>
      <div class="border border-line rounded-md overflow-hidden">
        <table class="w-full text-[13px]">
          <thead class="bg-raised text-left">
            <tr>
              <th class="px-3 py-2"></th>
              <th class="px-3 py-2 eyebrow">Invoice</th>
              <th class="px-3 py-2 eyebrow">PO</th>
              <th class="px-3 py-2 eyebrow">GRN</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
            <tr class="border-t border-line font-semibold">
              <td class="px-3 py-2">Total</td>
              ${cell(formatINR(invoice.total), totalBad)}
              ${cell(po ? formatINR(po.total) : "—", false)}
              ${cell("—", false)}
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <div class="eyebrow mb-1">Vendor</div>
      <div class="border-t border-line">
        ${vrow("Name", `${vendor.name} <span class="text-faint font-mono text-[11px]">${vendor.id}</span>`, false, false)}
        ${vrow("Category · terms", `${vendor.category} · ${vendor.payment_terms}`, false, false)}
        ${vrow("GSTIN", gstinBad ? `${vendor.gstin} <span class="text-[11px]">(invoice: ${invoice.gstin})</span>` : vendor.gstin, gstinBad, true)}
        ${vrow("Bank", bankBad ? `${vendor.bank_account} <span class="text-[11px]">(invoice: ${invoice.bank_account})</span>` : vendor.bank_account, bankBad, true)}
      </div>
    </div>`;
}

async function selectInvoice(id) {
  selectedId = id;
  const panel = document.getElementById("detail-panel");
  panel.innerHTML = `<p class="text-sm text-faint">Loading…</p>`;
  renderDecisionIdle();
  try {
    renderInbox(invoicesCache);
    const detail = await api.getInvoice(id);
    renderDetail(detail);
  } catch (err) {
    panel.innerHTML = `<p class="text-sm text-danger">Could not load this invoice.</p>`;
    console.error(err);
  }
}

/* ================= DECISION + WHY ================= */

function renderDecisionIdle() {
  const panel = document.getElementById("decision-panel");
  const why = document.getElementById("why-panel");
  why.innerHTML = `<p class="text-sm text-faint">Recalled memories will appear here.</p>`;

  const inv = invoicesCache.find((i) => i.id === selectedId);
  if (inv && inv.status === "CLEAN") {
    panel.innerHTML = `<p class="text-sm text-faint">Clean invoice. No exceptions, so nothing for the agent to decide.</p>`;
    return;
  }

  panel.innerHTML = `
    <button id="process-btn"
      class="w-full py-2.5 rounded-md bg-accent text-bg text-[13px] font-semibold hover:opacity-90">
      Process with agent
    </button>`;
  document.getElementById("process-btn").onclick = processSelected;
}

async function processSelected() {
  const id = selectedId;
  const panel = document.getElementById("decision-panel");
  const steps = ["Detecting exceptions", "Recalling memory", "Deciding"];
  let i = 0;

  panel.innerHTML = `<p id="step-label" class="font-mono text-[12px] text-dim">${steps[0]}…</p>`;
  const timer = setInterval(() => {
    i = Math.min(i + 1, steps.length - 1);
    const el = document.getElementById("step-label");
    if (el) el.textContent = steps[i] + "…";
  }, 600);

  try {
    const d = await api.processInvoice(id, memoryOn);
    clearInterval(timer);
    setStatus(id, d.final_status);
    if (id !== selectedId) return d; // user clicked another invoice meanwhile
    renderDecision(d);
    renderWhy(d);
    renderReviewActions(d);
    refreshOps();
    return d;
  } catch (err) {
    clearInterval(timer);
    console.error(err);
    panel.innerHTML = `
      <p class="text-sm text-danger mb-3">The agent could not process this invoice.</p>
      <button id="process-btn" class="px-3 py-1.5 rounded-md border border-line text-[13px] hover:bg-raised">Try again</button>`;
    document.getElementById("process-btn").onclick = processSelected;
    return null;
  }
}

function normGuard(t) {
  if (typeof t === "string") {
    const m = t.match(/^(G\d+)\s*[:\-–]?\s*(.*)$/i);
    return m ? { code: m[1].toUpperCase(), why: m[2] } : { code: "", why: t };
  }
  return {
    code: t.code || t.rule || t.id || t.guardrail || "",
    why: t.why || t.reason || t.message || t.detail || t.description || "",
  };
}

function decisionHTML(d) {
  const dec = d.decision;
  const g = d.guardrails;
  const a = ACTION[dec.action] || ACTION.ESCALATE;
  const pct = Math.round((dec.confidence ?? 0) * 100);
  lastDraft = dec.draft_message || "";

  const downgrade =
    g.downgraded_from && g.downgraded_from !== dec.action
      ? `<p class="text-[13px] mb-2">Agent suggested
           <span class="font-mono text-[12px]">${g.downgraded_from}</span>.
           Safety rule <span class="font-mono text-[12px] text-warn">${normGuard(g.triggered[0] || {}).code}</span>
           changed it to <span class="font-mono text-[12px]">${dec.action}</span>.</p>`
      : "";

  const guardrails =
    g.triggered.length === 0
      ? `<p class="text-[12px] text-resolution">All safety rules passed</p>`
      : downgrade +
        g.triggered
          .map((raw) => {
            const t = normGuard(raw);
            return `
          <div class="border-l-2 border-warn bg-raised pl-3 pr-3 py-2 mb-2 text-[13px]">
            <span class="font-mono font-medium text-[12px] text-warn">${t.code}</span>
            <span class="text-dim">${t.why}</span>
          </div>`;
          })
          .join("");

  const draft = lastDraft
    ? `<div class="mt-4">
         <div class="flex items-center justify-between mb-1.5">
           <span class="eyebrow">Draft email</span>
           <button onclick="navigator.clipboard.writeText(lastDraft)" class="text-[12px] text-dim hover:text-ink">Copy</button>
         </div>
         <pre class="whitespace-pre-wrap text-[12px] text-dim bg-raised border border-line rounded-md p-3 font-sans">${lastDraft}</pre>
       </div>`
    : "";

  return `
    <div class="flex items-center justify-between mb-4">
      <span class="inline-flex items-center px-2.5 py-1 rounded-sm border ${a.border} ${a.bg} ${a.text} font-mono text-[12px] font-medium">${a.label}</span>
      <span class="font-mono text-[11px] text-faint">${(d.latency_ms / 1000).toFixed(1)}s · memory ${d.memory_used ? "on" : "off"}</span>
    </div>

    <div class="mb-4">
      <div class="flex justify-between mb-1.5">
        <span class="eyebrow">Confidence</span>
        <span class="font-mono text-[11px] text-dim">${pct}%</span>
      </div>
      <div class="h-1 bg-line rounded-full overflow-hidden">
        <div class="h-full ${a.fill}" style="width:${pct}%"></div>
      </div>
    </div>

    <p class="text-[13px] leading-relaxed mb-4">${dec.rationale}</p>
    ${guardrails}
    ${draft}`;
}

function renderDecision(d) {
  document.getElementById("decision-panel").innerHTML = decisionHTML(d);
}

function renderWhy(d) {
  const panel = document.getElementById("why-panel");
  lastRecalled = d.recalled;
  lastCited = new Set(d.decision.cited_case_ids);

  if (!d.recalled.length) {
    panel.innerHTML = `<p class="text-sm text-faint">No memory found: acting generically.</p>`;
    return;
  }

  panel.innerHTML = d.recalled
    .map((r, i) => {
      const t = MEMORY_TYPE[r.memory_type] || MEMORY_TYPE.vendor_profile;
      const isCited = r.case_id && lastCited.has(r.case_id);
      return `
      <div data-idx="${i}"
           class="memory-card border-l-2 ${t.border} bg-raised pl-3 pr-3 py-2.5 mb-2 cursor-pointer hover:bg-line/40 ${isCited ? "" : "opacity-50 hover:opacity-100"}">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] tracking-widest uppercase ${t.text}">${t.label}</span>
          ${r.case_id ? `<span class="font-mono text-[11px] px-1.5 rounded-sm border border-line text-ink">${r.case_id}</span>` : ""}
          ${isCited ? `<span class="text-[10px] uppercase tracking-widest text-accent ml-auto">cited</span>` : ""}
        </div>
        <p class="text-[12px] leading-relaxed text-dim line-clamp-3">${r.text}</p>
      </div>`;
    })
    .join("");
}

/* ================= HUMAN REVIEW ================= */

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => t.classList.add("hidden"), 4000);
}

function renderReviewActions(d) {
  if (d.final_status !== "NEEDS_REVIEW") return;
  document.getElementById("decision-panel").insertAdjacentHTML(
    "beforeend",
    `<div id="review-actions" class="mt-5 pt-4 border-t border-line">
       <div class="eyebrow mb-2">Human review</div>
       <button id="review-btn" class="w-full py-2.5 rounded-md bg-accent text-ink text-[13px] font-semibold hover:opacity-90">Review and decide</button>
     </div>`
  );
  document.getElementById("review-btn").onclick = openReview;
}

function openReview() {
  document.getElementById("review-invoice").textContent = selectedId;
  document.getElementById("review-note").value = "";
  document.getElementById("review-error").textContent = "";
  document.getElementById("review-precedent").checked = true;
  document.getElementById("review-modal").classList.remove("hidden");
  document.getElementById("review-note").focus();
}

function closeReview() {
  document.getElementById("review-modal").classList.add("hidden");
}

async function submitReview(action) {
  const id = selectedId;
  const note = document.getElementById("review-note").value.trim();
  const approver = document.getElementById("review-approver").value.trim() || "Priya Nair";
  const err = document.getElementById("review-error");
  const buttons = document.querySelectorAll(".review-btn");

  if (action === "APPROVE_WITH_NOTE" && !note) {
    err.textContent = "Add a note: it is what the agent learns from.";
    return;
  }

  err.textContent = "";
  buttons.forEach((b) => (b.disabled = true));
  try {
        const reusable = document.getElementById("review-precedent").checked;
      const res = await api.resolveInvoice(id, { action, note, approver, reusable_precedent: reusable });
      closeReview();
      // The backend returns AUTO_RESOLVED for human approvals too; show it as human-approved in the UI
      setStatus(id, res.status === "AUTO_RESOLVED" ? "HUMAN_APPROVED" : res.status);
      showToast(`Retained in Hindsight memory: ${res.case_id}`);
      refreshOps();
      const box = document.getElementById("review-actions");
      if (box && id === selectedId) {
      box.innerHTML = `
        <div class="eyebrow mb-1">Human review</div>
        <p class="text-[13px] text-resolution">Decided by ${approver}. Saved as <span class="font-mono">${res.case_id}</span>.</p>`;
    }
  } catch (e) {
    console.error(e);
    err.textContent = "Could not save the decision. Try again.";
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

/* ================= MEMORY MODAL ================= */

function showMemoryModal(r, citedLabel) {
  const t = MEMORY_TYPE[r.memory_type || r.type] || MEMORY_TYPE.vendor_profile;

  const type = document.getElementById("memory-type");
  type.textContent = t.label;
  type.className = `text-[10px] tracking-widest uppercase ${t.text}`;

  const caseEl = document.getElementById("memory-case");
  caseEl.textContent = r.case_id || "";
  caseEl.classList.toggle("hidden", !r.case_id);

  document.getElementById("memory-cited").textContent = citedLabel || "";
  document.getElementById("memory-text").textContent = r.text;
  document.getElementById("memory-modal").classList.remove("hidden");
}

function openMemory(idx) {
  const r = lastRecalled[idx];
  if (!r) return;
  const isCited = r.case_id && lastCited.has(r.case_id);
  showMemoryModal(r, isCited ? "cited in this decision" : "recalled, not cited");
}

function closeMemory() {
  document.getElementById("memory-modal").classList.add("hidden");
}

document.getElementById("why-panel").addEventListener("click", (e) => {
  const card = e.target.closest(".memory-card");
  if (card) openMemory(Number(card.dataset.idx));
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeReview();
    closeMemory();
    closeReset();
  }
});

/* ================= MEMORY TOGGLE ================= */

function setMemory(on) {
  memoryOn = on;
  const onBtn = document.getElementById("mem-on");
  const offBtn = document.getElementById("mem-off");
  const label = document.getElementById("mem-label");

  onBtn.className = on
    ? "px-2.5 py-1 bg-accent text-bg font-semibold"
    : "px-2.5 py-1 text-dim hover:text-ink";
  offBtn.className = on
    ? "px-2.5 py-1 text-dim hover:text-ink"
    : "px-2.5 py-1 bg-[#C3C4C1] text-ink font-semibold";
  label.className = on ? "px-2.5 py-1 text-faint" : "px-2.5 py-1 text-dim";

  // Clear the old decision so it can be re-run in the new mode
  if (selectedId) renderDecisionIdle();
}

document.getElementById("mem-on").onclick = () => setMemory(true);
document.getElementById("mem-off").onclick = () => setMemory(false);

/* ================= TABS ================= */

const VIEWS = {
  workspace: "view-workspace",
  compare: "view-compare",
  curve: "view-curve",
  ops: "view-ops",
  explorer: "view-explorer",
};

function showTab(name, navigate = true) {
  if (!Object.hasOwn(VIEWS, name)) return;
  if (navigate && window.location.hash !== `#/${name}`) {
    window.history.pushState({ tab: name }, "", `#/${name}`);
  }
  Object.entries(VIEWS).forEach(([key, id]) =>
    document.getElementById(id).classList.toggle("hidden", key !== name)
  );
  document.querySelectorAll(".tab-btn").forEach((b) => {
    const active = b.dataset.tab === name;
    b.className =
      "tab-btn py-3 " + (active ? "-mt-px border-t-2 border-accent text-ink" : "text-dim hover:text-ink");
  });
  if (name === "compare") initCompare();
  if (name === "curve") renderCurve();
  if (name === "ops") refreshOps();
  if (name === "explorer") initExplorer();
}

window.addEventListener("popstate", () => {
  const tab = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  if (Object.hasOwn(VIEWS, tab)) {
    document.getElementById("hero")?.remove();
    showTab(tab, false);
  }
});

/* ================= LIVE IMPACT + SAFE SANDBOX ================= */

async function refreshOps() {
  const error = document.getElementById("ops-error");
  error.textContent = "";
  try {
    const [impact, audit] = await Promise.all([api.getImpact(), api.getAudit()]);
    document.getElementById("impact-handled").textContent = impact.invoices_handled ?? 0;
    document.getElementById("impact-handsfree").textContent = impact.hands_free_decisions ?? 0;
    document.getElementById("impact-hours").textContent = `${Number(impact.estimated_hours_saved || 0).toFixed(2)} h`;
    document.getElementById("impact-risk").textContent = formatINR(impact.risk_routed_to_review_inr || 0);
    const records = audit.records || [];
    document.getElementById("audit-count").textContent = `${records.length} records`;
    const host = document.getElementById("ops-audit-list");
    host.replaceChildren();
    if (!records.length) {
      const empty = document.createElement("p"); empty.className = "text-[12px] text-faint"; empty.textContent = "No saved decisions in this bank yet."; host.appendChild(empty);
    }
    records.slice().reverse().slice(0, 12).forEach((record) => {
      const row = document.createElement("article"); row.className = "border-b border-line py-2.5";
      const head = document.createElement("div"); head.className = "flex justify-between gap-3 text-[11px]";
      const title = document.createElement("strong"); title.className = "font-mono text-ink"; title.textContent = record.invoice_id || record.case_id || "Session";
      const status = document.createElement("span"); status.className = "text-dim"; status.textContent = String(record.final_status || record.status || record.action || "Saved").replaceAll("_", " ");
      head.append(title, status); row.appendChild(head);
      const detail = document.createElement("p"); detail.className = "text-[10px] text-dim mt-1";
      const action = record.decision?.action || record.action || "Decision recorded";
      const approver = record.human_approver ? ` · ${record.human_approver}` : "";
      detail.textContent = `${String(action).replaceAll("_", " ")}${approver}`; row.appendChild(detail);
      const time = document.createElement("time"); time.className = "text-[9px] text-faint";
      time.textContent = record.recorded_at ? new Date(record.recorded_at).toLocaleString() : "Recorded this session"; row.appendChild(time);
      host.appendChild(row);
    });
  } catch (err) {
    error.textContent = `Backend data unavailable: ${err.message}`;
  }
}

let sandboxBase = 0;
async function loadSandboxInvoice(id) {
  const result = document.getElementById("sandbox-result");
  if (!id) return;
  result.textContent = "Loading invoice baseline…";
  try {
    const detail = await api.getInvoice(id);
    sandboxBase = Number(detail.po?.total || detail.invoice.total || 0);
    const variance = Number(document.getElementById("sandbox-variance").value);
    document.getElementById("sandbox-amount").value = Math.round(sandboxBase * (1 + variance / 100));
    document.getElementById("sandbox-variance-label").textContent = `${variance.toFixed(variance % 1 ? 1 : 0)}%`;
    result.textContent = "Adjust the amount or bank details, then run the isolated challenge.";
  } catch (err) { result.textContent = `Could not load invoice baseline: ${err.message}`; }
}

function initSandboxControls() {
  const select = document.getElementById("sandbox-invoice");
  select.innerHTML = invoicesCache.map((invoice) => `<option value="${esc(invoice.id)}">${esc(invoice.id)} · ${esc(invoice.vendor_name)}</option>`).join("");
  if (!invoicesCache.length) return;
  select.value = invoicesCache.some((i) => i.id === selectedId) ? selectedId : invoicesCache[0].id;
  select.onchange = () => loadSandboxInvoice(select.value);
  const slider = document.getElementById("sandbox-variance");
  slider.oninput = () => {
    const variance = Number(slider.value);
    document.getElementById("sandbox-variance-label").textContent = `${variance.toFixed(variance % 1 ? 1 : 0)}%`;
    if (sandboxBase) document.getElementById("sandbox-amount").value = Math.round(sandboxBase * (1 + variance / 100));
  };
  document.getElementById("sandbox-amount").oninput = () => {
    const amount = Number(document.getElementById("sandbox-amount").value);
    if (sandboxBase && amount > 0) {
      const variance = Math.max(0, Math.min(20, (amount / sandboxBase - 1) * 100));
      slider.value = variance; document.getElementById("sandbox-variance-label").textContent = `${variance.toFixed(1)}%`;
    }
  };
  select.onchange();
  document.getElementById("sandbox-run").onclick = runSandboxChallenge;
}

async function runSandboxChallenge() {
  const button = document.getElementById("sandbox-run"), result = document.getElementById("sandbox-result");
  const invoiceId = document.getElementById("sandbox-invoice").value;
  const amount = Number(document.getElementById("sandbox-amount").value);
  if (!invoiceId || !Number.isFinite(amount) || amount <= 0 || amount > 10000000) { result.textContent = "Enter a valid amount between ₹1 and ₹1,00,00,000."; return; }
  button.disabled = true; button.textContent = "Running decision pipeline…"; result.textContent = "Evaluating a temporary invoice copy…";
  try {
    const decision = await api.runSandbox({ invoice_id: invoiceId, invoice_total: amount, bank_change: document.getElementById("sandbox-bank-change").checked, memory_enabled: memoryOn });
    result.replaceChildren();
    const heading = document.createElement("strong"); heading.className = "text-ink"; heading.textContent = `${String(decision.final_status || "UNKNOWN").replaceAll("_", " ")} · ${decision.decision?.action || "No action"}`; result.appendChild(heading);
    const guardrails = decision.guardrails?.triggered || [];
    const checks = document.createElement("p"); checks.className = "mt-1"; checks.textContent = guardrails.length ? guardrails.join(" · ") : "No blocking guardrail triggered."; result.appendChild(checks);
    if (decision.decision?.rationale) { const why = document.createElement("p"); why.className = "mt-1"; why.textContent = decision.decision.rationale; result.appendChild(why); }
    const note = document.createElement("p"); note.className = "text-[10px] mt-2 text-dim"; note.textContent = "Sandbox run · source invoice, saved audit, and Hindsight memory unchanged."; result.appendChild(note);
  } catch (err) { result.textContent = `Challenge failed safely: ${err.message}`; }
  finally { button.disabled = false; button.textContent = "Run challenge →"; }
}

/* ================= COMPARE ================= */

function initCompare() {
  const sel = document.getElementById("compare-select");
  const prev = sel.value || selectedId;
  sel.innerHTML = invoicesCache
    .filter((i) => i.exception_types.length)
    .map((i) => `<option value="${i.id}">${i.id} · ${i.vendor_name} · ${formatINR(i.amount)}</option>`)
    .join("");
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

function compareWhyHTML(d) {
  if (!d.recalled.length) {
    return `<p class="text-sm text-faint">No memory found: acting generically.</p>`;
  }
  const cited = new Set(d.decision.cited_case_ids);
  return d.recalled
    .map((r) => {
      const t = MEMORY_TYPE[r.memory_type] || MEMORY_TYPE.vendor_profile;
      const isCited = r.case_id && cited.has(r.case_id);
      return `
      <div class="border-l-2 ${t.border} bg-raised pl-3 pr-3 py-2 mb-2 ${isCited ? "" : "opacity-50"}">
        <div class="flex items-center gap-2 mb-0.5">
          <span class="text-[10px] tracking-widest uppercase ${t.text}">${t.label}</span>
          ${r.case_id ? `<span class="font-mono text-[11px] px-1.5 rounded-sm border border-line">${r.case_id}</span>` : ""}
          ${isCited ? `<span class="text-[10px] uppercase tracking-widest text-accent ml-auto">cited</span>` : ""}
        </div>
        <p class="text-[12px] leading-relaxed text-dim line-clamp-2">${r.text}</p>
      </div>`;
    })
    .join("");
}

function compareColumn(title, titleColor, d) {
  return `
    <div class="border border-line rounded-md bg-panel">
      <div class="px-4 h-10 flex items-center border-b border-line">
        <span class="font-mono text-[12px] font-medium ${titleColor}">${title}</span>
      </div>
      <div class="p-4 border-b border-line">${decisionHTML(d)}</div>
      <div class="p-4">
        <div class="eyebrow mb-2">Why</div>
        ${compareWhyHTML(d)}
      </div>
    </div>`;
}

async function runCompare() {
  const id = document.getElementById("compare-select").value;
  const out = document.getElementById("compare-result");
  const btn = document.getElementById("compare-run");
  if (!id) return;

  btn.disabled = true;
  out.innerHTML = `<p class="font-mono text-[12px] text-dim">Running with memory off and on…</p>`;
  try {
    const [off, on] = await Promise.all([
      api.processInvoice(id, false),
      api.processInvoice(id, true),
    ]);
    const changed = off.decision.action !== on.decision.action;
    out.innerHTML = `
      <div class="mb-5 text-[13px] ${changed ? "text-accent" : "text-dim"}">
        ${
          changed
            ? `Memory changed the outcome: <span class="font-mono text-[12px]">${off.decision.action}</span> → <span class="font-mono text-[12px]">${on.decision.action}</span>`
            : "Same action with and without memory for this invoice."
        }
      </div>
      <div class="grid grid-cols-2 gap-6">
        ${compareColumn("Memory OFF", "text-dim", off)}
        ${compareColumn("Memory ON", "text-accent", on)}
      </div>`;
  } catch (err) {
    console.error(err);
    out.innerHTML = `<p class="text-sm text-danger">Could not run the comparison. Try again.</p>`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("compare-run").onclick = runCompare;

/* ================= LEARNING CURVE ================= */

let curveChart = null;

function statCell(label, value, sub, color) {
  return `
    <div class="px-5 py-4">
      <div class="eyebrow mb-2">${label}</div>
      <div class="font-display text-3xl font-semibold tabular-nums ${color || "text-ink"}">${value}</div>
      <div class="text-[12px] text-faint mt-1">${sub}</div>
    </div>`;
}

async function renderCurve() {
  const out = document.getElementById("curve-content");
  out.innerHTML = `<p class="font-mono text-[12px] text-dim">Loading results…</p>`;

  try {
    const [on, off] = await Promise.all([api.getMetrics(true), api.getMetrics(false)]);
    const s = on.summary;
    const touchesSaved = off.summary.human_touches - on.summary.human_touches;
    const hoursSaved = ((s.touchless_correct * 12) / 60).toFixed(1);
    const isSample = on.placeholder || off.placeholder;

    out.innerHTML = `
      <div class="flex items-center justify-between mb-5">
        <div>
          <div class="eyebrow mb-1.5">Learning curve</div>
          <h2 class="font-display text-2xl font-semibold tracking-tight">Same invoices, with and without memory</h2>
        </div>
        ${
          isSample
            ? `<span class="font-mono text-[11px] uppercase tracking-widest px-2 py-1 border border-warn/50 text-warn rounded-sm">Sample data</span>`
            : `<span class="font-mono text-[11px] text-faint">${s.invoices} invoices · real run</span>`
        }
      </div>

      <div class="grid grid-cols-4 divide-x divide-line border border-line rounded-md mb-6">
        ${statCell("Correct auto-resolution", Math.round(s.final_rate * 100) + "%", "memory on, final rolling rate", "text-accent")}
        ${statCell("Human touches saved", touchesSaved, `${off.summary.human_touches} without memory, ${on.summary.human_touches} with`)}
        ${statCell("Unsafe auto-resolutions", s.unsafe, "handled hands-free but wrong", s.unsafe === 0 ? "text-resolution" : "text-danger")}
        ${statCell("Est. hours saved", hoursSaved, "assumes 12 min of manual work per exception")}
      </div>

      <div class="border border-line rounded-md p-4 bg-panel">
        <div class="relative h-[340px]"><canvas id="curve-canvas"></canvas></div>
      </div>`;

    drawCurve(on, off);
  } catch (err) {
    console.error(err);
    out.innerHTML = `<p class="text-sm text-danger">Could not load the results.</p>`;
  }
}

function drawCurve(on, off) {
  if (curveChart) curveChart.destroy();

  const firstIdx = on.points.findIndex((p) => p.auto_correct);
  const radii = on.points.map((_, i) => (i === firstIdx ? 6 : 0));

  // Draws the "first memory-driven resolution" label next to that point
  const markerPlugin = {
    id: "firstMarker",
    afterDatasetsDraw(chart) {
      if (firstIdx < 0) return;
      const pt = chart.getDatasetMeta(0).data[firstIdx];
      if (!pt) return;
      const { ctx, width } = chart;
      const right = pt.x > width * 0.6;
      ctx.save();
      ctx.fillStyle = "#151615";
      ctx.font = "11px JetBrains Mono, monospace";
      ctx.textAlign = right ? "right" : "left";
      ctx.fillText("First memory-driven resolution", pt.x + (right ? -12 : 12), pt.y - 12);
      ctx.restore();
    },
  };

  Chart.defaults.font.family = "Geist, system-ui, sans-serif";
  Chart.defaults.color = "#696C64"

  curveChart = new Chart(document.getElementById("curve-canvas"), {
    type: "line",
    data: {
      labels: on.points.map((p) => p.n),
      datasets: [
        {
          label: "With Hindsight memory",
          data: on.points.map((p) => p.rolling_rate * 100),
          borderColor: "#FA7F62",
          backgroundColor: "#FA7F62",
          borderWidth: 2,
          tension: 0.3,
          pointRadius: radii,
          pointHoverRadius: 5,
        },
        {
          label: "Without memory",
          data: off.points.map((p) => p.rolling_rate * 100),
          borderColor: "#A3A59E",
          borderWidth: 2,
          borderDash: [6, 4],
          tension: 0.3,
          pointRadius: 0,
          pointHoverRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          callbacks: { label: (c) => `${c.dataset.label}: ${Math.round(c.parsed.y)}%` },
        },
      },
      scales: {
        y: {
          min: 0,
          max: 100,
          grid: { color: "#E6E2D8" },
          border: { display: false },
          title: { display: true, text: "Correct auto-resolution (%)" },
        },
        x: {
          grid: { display: false },
          title: { display: true, text: "Invoice number (processed in order)" },
        },
      },
    },
    plugins: [markerPlugin],
  });
}

/* ================= MEMORY EXPLORER ================= */

// The backend's /api/invoices has no vendor_id, so we map name -> id here.
// Source: playbook Section 11.4. Keep in sync with data/vendors.json.
const VENDORS = [
  { id: "V-1001", name: "Charminar Office Supplies" },
  { id: "V-1002", name: "Godavari IT Services" },
  { id: "V-1003", name: "Hitech City Facilities Pvt Ltd" },
  { id: "V-1004", name: "Deccan Freight Lines" },
  { id: "V-1005", name: "Sri Lakshmi Packaging" },
  { id: "V-1006", name: "Krishna Components" },
  { id: "V-1007", name: "Banjara Cloud Hosting" },
  { id: "V-1008", name: "Nizam Catering Co" },
  { id: "V-1009", name: "Telangana Steel Traders" },
  { id: "V-1010", name: "Pearl City Print Works" },
  { id: "V-1011", name: "Mehdipatnam Security Services" },
  { id: "V-1012", name: "Musi Chemicals" },
];

// Memory text can contain anything a human typed, so escape before inserting.
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let explorerItems = [];
let searchToken = 0;
let briefingToken = 0;
let briefingTouched = false; // true once the user picks a vendor by hand

function vendorForSelected() {
  const inv = invoicesCache.find((i) => i.id === selectedId);
  return inv ? VENDORS.find((v) => v.name === inv.vendor_name) : null;
}

function initExplorer() {
  const sel = document.getElementById("briefing-select");
  if (!sel.options.length) {
    sel.innerHTML = VENDORS.map(
      (v) => `<option value="${v.id}">${esc(v.name)} · ${v.id}</option>`
    ).join("");
  }
  const v = vendorForSelected();
  if (v && !briefingTouched) sel.value = v.id;
}

function explorerCardHTML(r, i) {
  const t = MEMORY_TYPE[r.type || r.memory_type] || MEMORY_TYPE.vendor_profile;
  return `
    <div data-idx="${i}"
         class="explorer-card border-l-2 ${t.border} bg-raised pl-3 pr-3 py-2.5 mb-2 cursor-pointer hover:bg-line/40">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-[10px] tracking-widest uppercase ${t.text}">${t.label}</span>
        ${r.case_id ? `<span class="font-mono text-[11px] px-1.5 rounded-sm border border-line text-ink">${esc(r.case_id)}</span>` : ""}
      </div>
      <p class="text-[12px] leading-relaxed text-dim line-clamp-3">${esc(r.text)}</p>
    </div>`;
}

async function runExplorerSearch(q) {
  const input = document.getElementById("explorer-q");
  const out = document.getElementById("explorer-results");
  const btn = document.getElementById("explorer-search");
  if (typeof q === "string") input.value = q;
  q = input.value.trim();

  if (!q) {
    out.innerHTML = `<p class="text-sm text-faint">Type something to search, for example a vendor name.</p>`;
    return;
  }

  const token = ++searchToken;
  btn.disabled = true;
  out.innerHTML = `<p class="font-mono text-[12px] text-dim">Searching memory…</p>`;
  try {
    const items = await api.searchMemory(q);
    if (token !== searchToken) return;
    explorerItems = items;
    if (!items.length) {
      out.innerHTML = `<p class="text-sm text-faint">No memories match “${esc(q)}”.</p>`;
      return;
    }
    out.innerHTML =
      `<div class="font-mono text-[11px] text-faint mb-3">${items.length} ${items.length === 1 ? "memory" : "memories"} for “${esc(q)}”</div>` +
      items.map(explorerCardHTML).join("");
  } catch (err) {
    if (token !== searchToken) return;
    console.error(err);
    out.innerHTML = `<p class="text-sm text-danger">Could not search memory. Try again.</p>`;
  } finally {
    if (token === searchToken) btn.disabled = false;
  }
}

async function runBriefing() {
  const sel = document.getElementById("briefing-select");
  const out = document.getElementById("briefing-result");
  const btn = document.getElementById("briefing-run");
  const v = VENDORS.find((x) => x.id === sel.value);
  if (!v) return;

  const token = ++briefingToken;
  btn.disabled = true;
  out.innerHTML = `<p class="font-mono text-[12px] text-dim">Reflecting on what we know about ${esc(v.name)}…</p>`;
  try {
    const res = await api.getBriefing(v.id);
    if (token !== briefingToken) return;
    const text = (res.briefing_text || "").trim();
    out.innerHTML = text
      ? `<div class="border-l-2 border-quirk bg-raised pl-3 pr-3 py-3">
           <div class="eyebrow mb-2">${esc(v.name)} · ${v.id}</div>
           <p class="text-[13px] leading-relaxed whitespace-pre-wrap">${esc(text)}</p>
         </div>`
      : `<p class="text-sm text-faint">Nothing remembered about ${esc(v.name)} yet.</p>`;
  } catch (err) {
    if (token !== briefingToken) return;
    console.error(err);
    out.innerHTML = `<p class="text-sm text-danger">Could not get the briefing. Try again.</p>`;
  } finally {
    if (token === briefingToken) btn.disabled = false;
  }
}

// Header "Vendor briefing" button
function openBriefing() {
  briefingTouched = false;
  showTab("explorer");
  if (vendorForSelected()) runBriefing();
}

document.getElementById("explorer-search").onclick = () => runExplorerSearch();
document.getElementById("explorer-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runExplorerSearch();
});
document.getElementById("explorer-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".explorer-chip");
  if (chip) runExplorerSearch(chip.dataset.q);
});
document.getElementById("explorer-results").addEventListener("click", (e) => {
  const card = e.target.closest(".explorer-card");
  if (!card) return;
  const r = explorerItems[Number(card.dataset.idx)];
  if (r) showMemoryModal(r, "");
});
document.getElementById("briefing-run").onclick = runBriefing;
document.getElementById("briefing-select").addEventListener("change", () => {
  briefingTouched = true;
});

/* ================= RESET + DEMO MODE ================= */

let demoSteps = [];
let demoIdx = -1; // -1 = not started
let demoBusy = false;

function updateDemoUI() {
  const btn = document.getElementById("demo-next");
  const bar = document.getElementById("demo-bar");
  const total = demoSteps.length;

  if (demoIdx < 0) {
    btn.textContent = "Start demo (N)";
    bar.classList.add("hidden");
    return;
  }
  const s = demoSteps[demoIdx];
  bar.classList.remove("hidden");
  document.getElementById("demo-step").textContent = `${s.step} · ${demoIdx + 1} of ${total}`;
  document.getElementById("demo-title").textContent = s.title;
  document.getElementById("demo-narration").textContent = s.narration;
  btn.textContent =
    demoIdx + 1 < total ? `Next step: ${demoSteps[demoIdx + 1].step} (N)` : "Story done";
}

async function demoNext() {
  if (demoBusy) return;
  if (!document.getElementById("review-modal").classList.contains("hidden")) return;

  demoBusy = true;
  const btn = document.getElementById("demo-next");
  btn.disabled = true;
  try {
    if (!demoSteps.length) demoSteps = await api.getStory();

    if (demoIdx + 1 >= demoSteps.length) {
      showToast("Story finished. Open Compare for the proof, or Reset to run it again.");
      return;
    }

    const s = demoSteps[demoIdx + 1];
    if (!invoicesCache.some((i) => i.id === s.invoice_id)) {
      showToast(`${s.step}: invoice ${s.invoice_id} is not in the inbox. Check demo_story.json.`);
      return;
    }

    demoIdx++;
    updateDemoUI();
    showTab("workspace");
    if (!memoryOn) setMemory(true);
    await selectInvoice(s.invoice_id);
    const d = await processSelected();

    // Escalated steps: open the review modal with the story's note prefilled
    if (d && d.final_status === "NEEDS_REVIEW" && s.note) {
      openReview();
      document.getElementById("review-note").value = s.note;
    }
  } catch (err) {
    console.error(err);
    showToast("Could not run this story step.");
  } finally {
    demoBusy = false;
    btn.disabled = false;
    updateDemoUI();
  }
}

function openReset() {
  document.getElementById("reset-error").textContent = "";
  document.getElementById("reset-modal").classList.remove("hidden");
}

function closeReset() {
  document.getElementById("reset-modal").classList.add("hidden");
}

async function doReset(clearMemory) {
  const err = document.getElementById("reset-error");
  const buttons = document.querySelectorAll(".reset-btn");
  err.textContent = "";
  buttons.forEach((b) => (b.disabled = true));
  try {
    await api.resetDemo(clearMemory);
    invoicesCache = await api.listInvoices();

    closeReset();
    closeReview();
    selectedId = null;
    demoIdx = -1;
    lastRecalled = [];
    lastCited = new Set();

    renderInbox(invoicesCache);
    document.getElementById("detail-panel").innerHTML =
      `<p class="text-sm text-faint">Select an invoice to see it here.</p>`;
    document.getElementById("decision-panel").innerHTML =
      `<p class="text-sm text-faint">The agent's decision will appear here.</p>`;
    document.getElementById("why-panel").innerHTML =
      `<p class="text-sm text-faint">Recalled memories will appear here.</p>`;

    showTab("workspace");
    updateDemoUI();
    showToast(clearMemory ? "Reset done. Memory cleared." : "Reset done. Memory kept.");
  } catch (e) {
    console.error(e);
    err.textContent = "Could not reset. Is the backend running?";
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

// N = next story step (ignored while typing)
document.addEventListener("keydown", (e) => {
  if (e.key !== "n" && e.key !== "N") return;
  if (document.getElementById("hero")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t.matches("input, textarea, select") || t.isContentEditable) return;
  demoNext();
});

/* ================= HERO INTRO ================= */

(function initHero() {
  const hero = document.getElementById("hero");
  if (!hero) return;
  const requestedTab = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  if (Object.hasOwn(VIEWS, requestedTab)) {
    hero.remove();
    showTab(requestedTab, false);
  }
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const video = document.getElementById("hero-video");

  if (reduced) {
    video.remove();
  } else {
    video.addEventListener("loadeddata", () => video.classList.add("ready"));
    video.play().catch(() => {});
  }

  const enterWorkspace = (tab = "workspace") => {
    const nextTab = Object.hasOwn(VIEWS, tab) ? tab : "workspace";
    hero.remove();
    showTab(nextTab);
    const view = document.getElementById(VIEWS[nextTab]);
    if (view) view.setAttribute("tabindex", "-1");
    if (view) view.focus({ preventScroll: true });
  };
  document.getElementById("hero-cta").addEventListener("click", () => enterWorkspace("workspace"));

  const menu = document.getElementById("hero-menu");
  const profile = document.getElementById("hero-profile");
  const closePops = () => { menu.hidden = true; profile.hidden = true; };

  document.getElementById("hero-menu-btn").onclick = () => {
    const open = menu.hidden;
    closePops();
    menu.hidden = !open;
  };
  document.getElementById("hero-profile-btn").onclick = () => {
    const open = profile.hidden;
    closePops();
    profile.hidden = !open;
  };

  hero.addEventListener("click", (e) => {
    const go = e.target.closest("[data-go]");
    if (go) {
      enterWorkspace(go.dataset.go);
      return;
    }
    if (!e.target.closest(".hero-pop-wrap")) closePops();
  });

    if (reduced || !hero.animate) return;

  const rise = (el, delay, dy = 24) =>
    el.animate(
      [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: "translateY(0)" }],
      { duration: 700, delay, easing: "cubic-bezier(.2,.7,.2,1)", fill: "backwards" }
    );

  rise(hero.querySelector(".hero-head"), 0, -12);
  hero.querySelectorAll(".hl").forEach((el, i) => rise(el, 250 + i * 180, 32));
  rise(hero.querySelector(".hero-lede"), 900);
  // CTA keeps its centring transform: animate opacity and top offset only
  hero.querySelector("#hero-cta").animate(
    [{ opacity: 0, top: "24px" }, { opacity: 1, top: "0px" }],
    { duration: 700, delay: 1100, easing: "ease-out", fill: "backwards" }
  );
  [".hcard-dark", ".hcard-glass", ".hcard-coral"].forEach((sel, i) =>
    rise(hero.querySelector(sel), 1400 + i * 300, 40)
  );
})();

/* ================= STARTUP ================= */

async function init() {
  const list = document.getElementById("inbox-list");
  list.innerHTML = `<p class="p-4 text-sm text-faint">Loading…</p>`;
  try {
    invoicesCache = await api.listInvoices();
    renderInbox(invoicesCache);
  } catch (err) {
    list.innerHTML = `<p class="p-4 text-sm text-danger">Could not load invoices.</p>`;
    console.error(err);
  }

  list.addEventListener("click", (e) => {
    const card = e.target.closest(".invoice-card");
    if (card) selectInvoice(card.dataset.id);
  });
  initSandboxControls();
  document.getElementById("ops-refresh").onclick = refreshOps;
  document.getElementById("ops-export").onclick = () => api.exportAuditCsv();
  refreshOps();
}

init();
