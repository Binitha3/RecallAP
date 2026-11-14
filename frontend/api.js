// Flip this to false when Member 1's backend is ready. That's the only change needed.
const USE_MOCK = false;

// Mock mode only: which decision file each invoice returns.
const MOCK_DECISIONS = {
  "INV-2025-0731": "decision_cold.json",
  "INV-2025-0732": "decision_warm.json",
  "INV-2025-0736": "decision_guardrail.json",
};

let mockCaseCounter = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getJSON(mockFile, livePath, options = {}) {
  const url = USE_MOCK ? `mock/${mockFile}` : livePath;
  const res = await fetch(url, USE_MOCK ? {} : options);
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

const api = {
  listInvoices: () => getJSON("invoices.json", "/api/invoices"),

  getInvoice: (id) => getJSON("invoice_detail.json", `/api/invoices/${id}`),

  processInvoice: async (id, memoryOn = true) => {
    if (USE_MOCK) {
      await sleep(1800);
      if (!memoryOn) {
        // Mock: memory OFF always behaves like the cold start
        const cold = await getJSON("decision_cold.json", "");
        return { ...cold, invoice_id: id, memory_used: false };
      }
      return getJSON(MOCK_DECISIONS[id] || "decision_cold.json", "");
    }
    return getJSON(
      "",
      `/api/invoices/${id}/process?memory=${memoryOn ? "on" : "off"}`,
      { method: "POST" }
    );
  },

  // payload: { action, note, approver }  ->  { case_id, status, retained }
  resolveInvoice: async (id, payload) => {
    if (USE_MOCK) {
      await sleep(700);
      mockCaseCounter += 1;
      return {
        case_id: "CASE-" + String(mockCaseCounter).padStart(4, "0"),
        status: payload.action === "REJECT" ? "REJECTED" : "AUTO_RESOLVED",
        retained: true,
      };
    }
    const res = await fetch(`/api/invoices/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return res.json();
  },

    getMetrics: (memoryOn) =>
    getJSON(
      memoryOn ? "metrics_on.json" : "metrics_off.json",
      `/api/metrics?memory=${memoryOn ? "on" : "off"}`
    ),

  getImpact: () => getJSON("", "/api/impact"),
  getAudit: () => getJSON("", "/api/audit?limit=100"),

  runSandbox: async (payload) => {
    const res = await fetch("/api/sandbox/decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed: ${res.status}`);
    return data;
  },

  exportAuditCsv: () => { window.location.assign("/api/audit.csv"); },

  // -> [{ case_id?, type, text }]
  searchMemory: async (q) => {
    if (USE_MOCK) {
      await sleep(400);
      const all = await getJSON("memory_search.json", "");
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      return all.filter((m) => words.every((w) => m.text.toLowerCase().includes(w)));
    }
    return getJSON("", `/api/memory/search?q=${encodeURIComponent(q)}`);
  },

  // -> { vendor_id, briefing_text }
  getBriefing: async (vendorId) => {
    if (USE_MOCK) {
      await sleep(1500); // reflect is slow; lets you see the spinner
      if (vendorId === "V-1004") return getJSON("vendor_briefing.json", "");
      return { vendor_id: vendorId, briefing_text: "" };
    }
    return getJSON("", `/api/vendors/${vendorId}/briefing`);
  },

    // Story steps live in frontend/demo_story.json (same file in mock and live mode)
  getStory: async () => {
    const res = await fetch("demo_story.json");
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return res.json();
  },

  // -> { ok: true }
  resetDemo: async (clearMemory) => {
    if (USE_MOCK) {
      await sleep(500);
      mockCaseCounter = 0;
      return { ok: true };
    }
    const res = await fetch("/api/demo/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear_memory: clearMemory }),
    });
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return res.json();
  },
};
