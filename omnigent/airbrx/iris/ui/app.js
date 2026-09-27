// Iris's workspace. Framed by Omnigent at /iris/:sessionId and served per
// session at /v1/iris/sessions/{id}/ui/ (the only workspace since W5). It runs on
// the shared workspace kernel (window.AirbrxWorkspace, loaded from kernel/
// before this file) and declares only what is Iris's own: her tabs, her words
// and her honesty rules.
//
// Every number on screen comes from api/state, which the adapter builds from
// Iris's own recorded tool results. Nothing here computes an answer in her
// voice: a question goes to her through api/chat, and when the host refuses
// it the chat says she did not answer.
//
// Rendering uses DOM nodes and textContent only. Tenant evidence, rule ids and
// Iris's own text are data and must never be parsed as markup.
(() => {
  "use strict";

  const AW = window.AirbrxWorkspace;
  const { el, $ } = AW;
  const AGENT = "Iris";
  const SESSION_ID = decodeURIComponent(
    (/\/iris\/sessions\/([^/]+)\/ui\//.exec(location.pathname) || [])[1] || "",
  );
  const POLL_MS = Number(document.body.dataset.pollMs) || 1500;
  // D2. "show" draws an out-of-date capture with its label and refreshes it in
  // the background; "collect" draws nothing until one fresh collection returns.
  const ON_STALE =
    document.body.dataset.onStale === "collect" ? "collect" : "show";
  // The first sentence of the adapter's refresh prompt, byte for byte. The
  // adapter keeps it stable so a reloaded chat can say what that turn was.
  const REFRESH_SENTENCE =
    "Call iris_overview and iris_audit for the selected tenant.";
  const CONTEXT_NOTE =
    /\n\n\(I am looking at the [^\n]* tab of the Iris workspace\.\)$/;
  // Iris's tools in plain words. Never the tool's name, never its arguments.
  const DOING = {
    iris_overview: "Reading the tenant's traffic",
    iris_audit: "Checking the rule configuration",
    iris_investigate: "Comparing periods",
    iris_propose: "Drafting and validating a rule change",
    ToolSearch: "Getting her tools ready",
  };
  const DOWNLOADS = ["report.json", "report.md", "proposal.json"];
  const ONCE_PER_SESSION =
    "The page collects on its own only once per session, because each collection reads the warehouse. Collect again when you want a new one.";
  // Refresh 409s (docs/iris/WORKSPACE_V2.md section 2). Only these two details
  // mean a collection ran and read no overview; the page's own sentences are
  // for them. Any other 409 is shown as the adapter words it.
  const RAN_AND_MISSED =
    /^The collection (produced no overview|read no current-period overview)/;
  // These mean no turn was started: BUSY_DETAIL and REPEAT_COLLECT_DETAIL.
  const NOT_STARTED = /^(Iris is busy|Iris did not start another collection)/;
  // This tab's record of the automatic collection, for when the session's
  // record cannot be read or the turn never reached it.
  const AUTO_KEY = `iris.autoCollected.${SESSION_ID}`;

  // ------------------------------------------------------------- state --

  let state = null;
  let readiness = null;
  let readinessError = "";
  // "" when idle, else "first" (nothing collected here yet) or "stale" (an
  // out-of-date capture is being replaced). Different sentences on purpose.
  let collecting = "";
  // One automatic collection per page load, however often state is re-read.
  let autoCollected = false;
  // "first" or "stale" when this load did not collect on its own because this
  // session already had its automatic collection (QA N1): each one is a
  // warehouse read, so after it the page offers "Collect now" instead.
  let autoSkipped = "";
  // The newest collect turn in the session's own record, epoch seconds:
  // -Infinity when there is none, Infinity when one carries no time.
  let lastCollectTurn = -Infinity;
  let refused = ""; // why the adapter's state was not drawn, in plain words
  // collect mode only: an out-of-date capture exists but is not drawn.
  let heldStale = false;
  let busy = false;
  // False until this load's first read of state has answered: until then the
  // page says it is reading, never "not collected yet" over a capture.
  let loaded = false;
  let stopWatching = null;
  let currentTab = null;
  let accounts = { status: "idle", tenants: [], account: null, error: "" };
  const history = [];
  const notes = new Map();

  // ----------------------------------------------------------- helpers --

  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  // A measured zero is "0"; only a missing measurement says so.
  const num = (v) =>
    finite(v)
      ? v.toLocaleString("en-US", { maximumFractionDigits: 1 })
      : "Not measured";
  const percent = (v) =>
    finite(v) ? `${(v * 100).toFixed(1)}%` : "Not measured";
  const title = (v) =>
    String(v || "Finding")
      .replace(/_/g, " ")
      .replace(/^./, (c) => c.toUpperCase());
  const overviewOf = (s) => (s && s.overview) || {};
  const metricsOf = (s) => overviewOf(s).metrics || {};
  // The tenant this session is bound to, as readiness reports it; "" unknown.
  const sessionTenant = () => (readiness && readiness.tenant_id) || "";

  function when(epochSeconds) {
    if (!finite(epochSeconds)) return "";
    const date = new Date(epochSeconds * 1000);
    return Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        });
  }

  function age(seconds) {
    if (!finite(seconds)) return "";
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    return `${Math.floor(seconds / 86400)} d ago`;
  }

  function period(m) {
    return `${m.start_date || "unknown start"} to ${m.end_date || "unknown end"} (UTC, end exclusive)`;
  }

  /** A plainError sentence, ready to sit inside another sentence. */
  function reason(error) {
    return AW.plainError(error, { agent: AGENT }).replace(/[.\s]+$/, "");
  }

  function notice(text, isError) {
    const box = $("notice");
    box.hidden = !text;
    box.className = isError ? "notice error" : "notice";
    box.textContent = text || "";
  }

  function isSynthetic() {
    return Boolean(
      (readiness && readiness.fixture) ||
      overviewOf(state).mode === "synthetic fixture",
    );
  }

  /** All findings, overview's then audit's, one per id. */
  function findings(s) {
    const all = [
      ...(overviewOf(s).findings || []),
      ...((s && s.audit && s.audit.findings) || []),
    ].filter((f) => f && typeof f === "object");
    const seen = new Set();
    return all.filter((f) => {
      if (seen.has(f.id)) return false;
      seen.add(f.id);
      return true;
    });
  }

  /** Evidence rows across every report in state, one per id, with where each came from. */
  function evidenceRows(s) {
    const rows = [];
    const seen = new Set();
    for (const [source, report] of [
      ["Overview", s && s.overview],
      ["Audit", s && s.audit],
      ["Investigation", s && s.investigation],
      ["Proposal", s && s.proposal],
    ])
      for (const row of (report && report.evidence) || []) {
        if (!row || typeof row !== "object" || seen.has(row.id)) continue;
        seen.add(row.id);
        rows.push({ source, row });
      }
    return rows;
  }

  function asOf(tool) {
    const at = state && state.report_times && state.report_times[tool];
    return el(
      "p",
      { class: "as-of muted small" },
      finite(at)
        ? `As of ${when(at)}`
        : "Iris has not produced this in the session yet.",
    );
  }

  function heading(name, sub, tool) {
    return el(
      "div",
      { class: "page-head" },
      el("h1", {}, name),
      sub ? el("p", { class: "muted small" }, sub) : null,
      tool ? asOf(tool) : null,
    );
  }

  function askButton(label, text) {
    return el(
      "button",
      {
        type: "button",
        class: "chip",
        "data-busy-off": true,
        disabled: busy,
        onclick: () => void ask(text),
      },
      label,
    );
  }

  function badge(text, kind) {
    return el("span", { class: `badge ${kind || ""}` }, text);
  }

  // ------------------------------------------------- is the capture coherent --
  //
  // Ported from the pinned workspace's normalize() (airbrx/iris ui/app.js),
  // which ran on every state. A capture whose own numbers disagree is not
  // drawn: it once rendered "80.0%" captioned "120 hits over 700 requests"
  // beside "Requests 1,000", a confident headline over a set the product
  // already knew was incoherent (Q19). The refusal names each mismatch.
  //
  // Deliberately NOT a rule: requests > 0. A week with no traffic is a
  // measured zero, and a measured zero is shown as zero, never refused.

  /** Why one metrics block cannot be drawn, as plain sentences; [] when it can. */
  function metricsProblems(m, where) {
    if (!m || typeof m !== "object") return [`${where} has no metrics.`];
    const out = [];
    const counts = [
      "requests",
      "cache_hits",
      "cache_misses",
      "hit_rate_denominator",
      "covered_days",
      "requested_days",
    ].filter(
      (key) =>
        m[key] !== null &&
        m[key] !== undefined &&
        (!finite(m[key]) || m[key] < 0),
    );
    if (counts.length)
      return [
        `${where}: ${counts.join(", ")} ${counts.length === 1 ? "is not a count" : "are not counts"}.`,
      ];
    const has = (...keys) => keys.every((key) => finite(m[key]));
    if (
      m.hit_rate !== null &&
      m.hit_rate !== undefined &&
      (!finite(m.hit_rate) || m.hit_rate < 0 || m.hit_rate > 1)
    )
      out.push(`${where}: the hit rate is not between 0 and 1.`);
    if (has("cache_hits", "requests") && m.cache_hits > m.requests)
      out.push(`${where}: there are more hits than requests.`);
    if (
      has("hit_rate_denominator", "requests") &&
      m.hit_rate_denominator !== m.requests
    )
      out.push(
        `${where}: the hit rate's denominator is not the request count.`,
      );
    if (
      has("cache_hits", "cache_misses", "hit_rate_denominator") &&
      m.cache_hits + m.cache_misses !== m.hit_rate_denominator
    )
      out.push(
        `${where}: hits and misses do not add up to the hit rate's denominator.`,
      );
    if (
      has("hit_rate", "cache_hits", "hit_rate_denominator") &&
      m.hit_rate_denominator > 0 &&
      Math.abs(m.hit_rate - m.cache_hits / m.hit_rate_denominator) > 0.0005
    )
      out.push(
        `${where}: the stated hit rate is not its own hits over its own denominator.`,
      );
    if (
      has("covered_days", "requested_days") &&
      m.covered_days > m.requested_days
    )
      out.push(`${where}: more days are covered than were requested.`);
    if (
      m.period_complete === true &&
      has("covered_days", "requested_days") &&
      m.covered_days !== m.requested_days
    )
      out.push(
        `${where}: the period is marked complete, but not every requested day is covered.`,
      );
    const latency = m.latency && m.latency.response_time_ms;
    if (
      latency !== null &&
      latency !== undefined &&
      (!finite(latency) || latency < 0)
    )
      out.push(`${where}: the response time is not a duration.`);
    return out;
  }

  /** Why a state cannot be drawn, as plain sentences; [] when it can. */
  function captureProblems(next, boundTenant) {
    const o = next && next.overview;
    if (!o || typeof o !== "object") return ["The capture has no overview."];
    const tenant = o.tenant_id;
    if (typeof tenant !== "string" || !tenant)
      return ["The capture does not name its tenant."];
    const out = [];
    if (tenant !== boundTenant)
      out.push("The capture is for a different tenant than this session.");
    for (const [label, report] of [
      ["The audit", next.audit],
      ["The investigation", next.investigation],
      ["The proposal", next.proposal],
    ])
      if (
        report &&
        typeof report === "object" &&
        report.tenant_id !== undefined &&
        report.tenant_id !== tenant
      )
        out.push(`${label} is for a different tenant than the overview.`);
    if (!Array.isArray(o.evidence) || !Array.isArray(o.findings))
      out.push("The overview's evidence or findings are not lists.");
    if (next.audit && !Array.isArray(next.audit.findings))
      out.push("The audit's findings are not a list.");
    if (
      next.rules !== undefined &&
      next.rules !== null &&
      !Array.isArray(next.rules)
    )
      out.push("The rule summaries are not a list.");
    for (const report of [o, next.audit].filter(
      (r) => r && typeof r === "object",
    )) {
      for (const f of Array.isArray(report.findings) ? report.findings : [])
        if (
          !f ||
          typeof f !== "object" ||
          typeof f.id !== "string" ||
          (f.evidence_ids !== undefined &&
            f.evidence_ids !== null &&
            !Array.isArray(f.evidence_ids))
        )
          out.push("A finding is malformed.");
        else if (f.tenant_id !== undefined && f.tenant_id !== tenant)
          out.push(
            `Finding ${f.id} is for a different tenant than the overview.`,
          );
      for (const e of Array.isArray(report.evidence) ? report.evidence : [])
        if (!e || typeof e !== "object")
          out.push("An evidence row is malformed.");
    }
    if (
      (Array.isArray(next.rules) ? next.rules : []).some(
        (r) => !r || typeof r !== "object",
      )
    )
      out.push("A rule summary is malformed.");
    out.push(...metricsProblems(o.metrics, "The overview"));
    return [...new Set(out)];
  }

  // ------------------------------------------------------------- views --

  /** What every tab but Accounts shows while there is no capture to draw. */
  function nothingYet(container) {
    let head;
    let body;
    if (refused) {
      head = "This capture is not shown";
      body = refused;
    } else if (collecting === "first") {
      head = "Collecting this tenant's first overview";
      body =
        "Iris is running her tools against the warehouse. Nothing is shown until she has read the tenant.";
    } else if (collecting === "stale") {
      head = "Refreshing an out-of-date capture";
      body =
        "Iris is collecting a fresh overview. The old capture is not shown until she has.";
    } else if (!loaded) {
      head = "Reading this session";
      body = "Loading the chat and Iris's last capture of this tenant.";
    } else if (heldStale) {
      head = "The last capture is out of date";
      body =
        "This workspace is set to show only a fresh capture, and the last collection did not produce one.";
      if (autoSkipped) body += ` ${ONCE_PER_SESSION}`;
    } else if (autoSkipped) {
      head = "No current overview yet";
      body = `Iris has already collected in this session, and it left no overview of this tenant's current period. ${ONCE_PER_SESSION}`;
    } else {
      head = "Iris has not collected an overview here yet";
      body =
        "She reads this session's tenant directly. Collecting runs her tools against the warehouse and usually takes about a minute.";
    }
    container.append(
      el(
        "div",
        { class: "card empty-state" },
        el("div", { class: "section-title" }, head),
        el("p", {}, body),
        collecting || refused || !loaded
          ? null
          : el(
              "div",
              { class: "asks" },
              el(
                "button",
                {
                  type: "button",
                  class: "primary",
                  "data-busy-off": true,
                  disabled: busy,
                  onclick: () => void refresh(),
                },
                autoSkipped
                  ? "Collect now"
                  : heldStale
                    ? "Collect a fresh overview"
                    : autoCollected
                      ? "Collect now"
                      : "Collect the first overview",
              ),
            ),
      ),
    );
  }

  function kpi(label, value, note) {
    return el(
      "div",
      { class: "kpi" },
      el("div", { class: "label" }, label),
      el("div", { class: "value" }, value),
      el("div", { class: "note" }, note),
    );
  }

  function kpis(m) {
    const latency = m.latency || {};
    return el(
      "section",
      { class: "kpis", "aria-label": "Summary" },
      kpi(
        "Cache hit rate",
        percent(m.hit_rate),
        finite(m.cache_hits) || finite(m.hit_rate_denominator)
          ? `${num(m.cache_hits)} hits over ${num(m.hit_rate_denominator)} requests`
          : "not measured in this capture",
      ),
      kpi("Requests", num(m.requests), "observed in the period"),
      kpi(
        "Cache misses",
        num(m.cache_misses),
        "requests the cache did not serve",
      ),
      kpi(
        "Response-time proxy",
        finite(latency.response_time_ms)
          ? `${(latency.response_time_ms / 1000).toFixed(2)} s`
          : "Not measured",
        latency.label || "not measured in this capture",
      ),
    );
  }

  function coverage(o, m) {
    // One chip per day of the period. A long session reads the same days
    // again; the newest reading of a day wins (the report lists its evidence
    // in the order Iris read it, so the last row for a day is the newest), and
    // days outside the period (end exclusive) are not this capture's coverage.
    const byDay = new Map();
    for (const e of o.evidence || []) {
      if (!e || e.source_tool !== "get_summary") continue;
      const day = String(e.start_date || "");
      if (m.start_date && day < String(m.start_date)) continue;
      if (m.end_date && day >= String(m.end_date)) continue;
      byDay.set(day, e);
    }
    const days = [...byDay.values()]
      .sort((a, b) => String(a.start_date).localeCompare(String(b.start_date)))
      .slice(0, 31);
    return el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "card-head" },
        el("div", { class: "section-title" }, "Evidence coverage"),
        badge(
          finite(m.covered_days) || finite(m.requested_days)
            ? `${num(m.covered_days)} / ${num(m.requested_days)} days`
            : "Coverage not measured",
          m.period_complete ? "ok" : "warn",
        ),
      ),
      days.length
        ? el(
            "div",
            { class: "coverage", "aria-label": "Daily summaries" },
            days.map((e) =>
              el(
                "span",
                {
                  class: `day ${e.status === "complete" ? "" : "missing"}`,
                  title: `${e.start_date || ""}: ${e.status || "unknown"}`,
                },
                String(e.start_date || "").slice(5),
              ),
            ),
          )
        : el(
            "p",
            { class: "muted small" },
            "No daily summaries in this capture.",
          ),
      el(
        "p",
        { class: "muted small" },
        "Coverage, not traffic volume: each day is one daily summary Iris could read.",
      ),
      m.period_complete
        ? null
        : el(
            "p",
            { class: "small" },
            "Partial capture. The numbers above describe the covered days only.",
          ),
    );
  }

  // A Python list of strings as Iris's audit writes it into a finding's text
  // (`{tables!r}`): ['a', "b's"]. The two alternatives in each string are
  // disjoint, so matching stays linear.
  const PY_STRING = String.raw`'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"`;
  const PY_LIST = new RegExp(
    String.raw`\[(?:${PY_STRING})(?:, (?:${PY_STRING}))*\]`,
    "g",
  );

  /**
   * A finding's sentence with each Python list in it shown as its items, each
   * as code, joined in words: "caches ['a', 'b']" reads "caches a and b". The
   * items are text nodes, never markup.
   */
  function readable(text) {
    const out = [];
    let at = 0;
    const source = String(text);
    for (const match of source.matchAll(PY_LIST)) {
      if (match.index > at) out.push(source.slice(at, match.index));
      const items = [...match[0].matchAll(new RegExp(PY_STRING, "g"))].map(
        ([quoted]) => quoted.slice(1, -1).replace(/\\(.)/g, "$1"),
      );
      items.forEach((item, i) => {
        if (i) out.push(i === items.length - 1 ? " and " : ", ");
        out.push(el("code", {}, item));
      });
      at = match.index + match[0].length;
    }
    if (at < source.length) out.push(source.slice(at));
    return out;
  }

  function findingCard(f, full) {
    const ids = Array.isArray(f.evidence_ids) ? f.evidence_ids : [];
    const note = notes.get(f.id) || "";
    return el(
      "article",
      { class: "finding", "data-finding-id": String(f.id) },
      el(
        "div",
        { class: "finding-head" },
        el("strong", {}, title(f.kind)),
        badge(f.severity || "unrated", f.severity === "warning" ? "warn" : ""),
        badge(`${f.confidence || "unknown"} confidence`),
      ),
      el(
        "div",
        { class: "muted small" },
        f.rule_id ? `Rule ${f.rule_id}` : "Tenant-wide",
      ),
      f.explanation ? el("p", {}, readable(f.explanation)) : null,
      f.next_step
        ? el("p", { class: "small" }, "Next: ", readable(f.next_step))
        : null,
      ids.length
        ? el(
            "div",
            { class: "evidence-links small" },
            "Evidence: ",
            ids.map((id) =>
              el(
                "button",
                {
                  type: "button",
                  class: "link mono",
                  onclick: () => showEvidence(id),
                },
                String(id).slice(0, 8),
              ),
            ),
          )
        : null,
      el(
        "div",
        { class: "asks" },
        askButton("Investigate this finding", `Investigate finding ${f.id}.`),
        full
          ? el(
              "button",
              {
                type: "button",
                class: "chip",
                onclick: () => exportHandoff(f),
              },
              "Export handoff",
            )
          : null,
      ),
      full
        ? el(
            "details",
            { class: "review", open: note ? true : undefined },
            el("summary", {}, note ? "Your review note" : "Add a review note"),
            noteBox(f, note),
            el(
              "p",
              { class: "muted small" },
              "Notes stay in this page until you reload. Export a handoff to keep them.",
            ),
          )
        : null,
    );
  }

  /** A finding's note box, holding what was typed, so a redraw keeps it. */
  function noteBox(f, note) {
    const box = el("textarea", {
      rows: "3",
      maxlength: "4000",
      "aria-label": `Review note for ${f.id}`,
      placeholder:
        "What should change? What freshness or table-scope limits must hold?",
      oninput: (event) => notes.set(f.id, event.target.value),
    });
    box.value = note;
    return box;
  }

  function exportHandoff(f) {
    const data = {
      kind: "iris-investigation-handoff",
      status: "unvalidated-intent",
      tenant_id: sessionTenant(),
      finding: f,
      notes: notes.get(f.id) || "",
      period: metricsOf(state),
      next_step:
        "Ask Iris to investigate and propose in the Iris session; a proposal still needs external approval.",
    };
    try {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
      );
      const link = el("a", {
        href: url,
        download: "iris-investigation-handoff.json",
      });
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice(
        "Download offered: iris-investigation-handoff.json. Check your browser's downloads.",
      );
    } catch {
      notice("This browser would not offer the download.", true);
    }
  }

  function textFindings(s) {
    const container = el("div", { class: "findings" });
    const list = findings(s);
    if (!list.length)
      container.append(
        el("p", { class: "muted" }, "No findings in this capture."),
      );
    return { container, list };
  }

  const views = {
    overview(container, s) {
      if (!s) return nothingYet(container);
      const o = overviewOf(s);
      const m = metricsOf(s);
      const top = findings(s).slice(0, 2);
      container.append(
        heading(
          "Overview",
          `${period(m)} · tenant ${o.tenant_id || "unknown"}`,
          "iris_overview",
        ),
        // Element.append turns null into the text "null": spread, never null.
        ...(earlierWindow(s)
          ? [
              el(
                "p",
                { class: "notice small" },
                el("strong", {}, "An earlier window. "),
                `Iris's newest overview in this session is for ${period(m)}, which she read for a comparison. It is not this tenant's current period. Collect a fresh overview to see the current period.`,
              ),
            ]
          : []),
        kpis(m),
        coverage(o, m),
        el(
          "div",
          { class: "card" },
          el("div", { class: "section-title" }, "Where to look first"),
          top.length
            ? top.map((f) => findingCard(f, false))
            : el("p", { class: "muted" }, "No findings in this capture."),
        ),
        el(
          "p",
          { class: "note small" },
          el("strong", {}, "Monitoring is unavailable. "),
          "Iris reads the tenant when asked; nothing watches it between captures.",
        ),
        el(
          "div",
          { class: "asks" },
          askButton(
            "Explain this capture's performance",
            "Explain this capture's performance.",
          ),
        ),
      );
    },

    findings(container, s) {
      if (!s) return nothingYet(container);
      const { container: box, list } = textFindings(s);
      box.append(...list.map((f) => findingCard(f, true)));
      container.append(
        heading(
          "Findings",
          "Things Iris thinks need a closer look. None of them is an approved change.",
          "iris_audit",
        ),
        el("div", { class: "card" }, box),
      );
    },

    rules(container, s) {
      if (!s) return nothingYet(container);
      const rows = Array.isArray(s.rules) ? s.rules : [];
      const meta = s.rule_effectiveness_meta || {};
      const byRule = new Map();
      for (const f of (s.audit && s.audit.findings) || [])
        if (f && f.rule_id) {
          if (!byRule.has(f.rule_id)) byRule.set(f.rule_id, []);
          byRule.get(f.rule_id).push(f);
        }
      const ruleIds = [
        ...new Set([...rows.map((r) => r.ruleId), ...byRule.keys()]),
      ].filter(Boolean);
      const windowLabel =
        meta.year !== null && meta.year !== undefined
          ? `Annual window, ${meta.year}`
          : "Annual window (year not reported)";
      container.append(
        heading(
          "Rules",
          "How each cache rule has done, and what Iris found in its configuration.",
          "iris_overview",
        ),
        el(
          "div",
          { class: "card table-wrap" },
          el(
            "div",
            { class: "card-head" },
            el("div", { class: "section-title" }, "Rule effectiveness"),
            badge(windowLabel, "info"),
          ),
          el(
            "p",
            { class: "muted small" },
            "This is a separate window from the overview period, so its counts do not add up to the overview's.",
            finite(meta.totalQueries)
              ? ` ${num(meta.totalQueries)} queries in the window.`
              : "",
          ),
          rows.length
            ? el(
                "table",
                { "aria-label": "Rule effectiveness" },
                el(
                  "thead",
                  {},
                  el(
                    "tr",
                    {},
                    ["Rule", "Executions", "Hits", "Misses", "Hit rate"].map(
                      (h) => el("th", {}, h),
                    ),
                  ),
                ),
                el(
                  "tbody",
                  {},
                  rows.map((r) =>
                    el(
                      "tr",
                      {},
                      el(
                        "td",
                        {},
                        el(
                          "strong",
                          {},
                          r.ruleName || r.ruleId || "Unnamed rule",
                        ),
                        r.ruleName
                          ? el("div", { class: "muted small mono" }, r.ruleId)
                          : null,
                      ),
                      el("td", { class: "mono" }, num(r.totalExecutions)),
                      el("td", { class: "mono" }, num(r.cacheHits)),
                      el("td", { class: "mono" }, num(r.cacheMisses)),
                      el(
                        "td",
                        { class: "mono" },
                        finite(r.hitRate)
                          ? `${percent(r.hitRate)} of ${num(r.totalExecutions)}`
                          : "Not reported",
                      ),
                    ),
                  ),
                ),
              )
            : el(
                "p",
                { class: "muted" },
                "No rule-effectiveness summary in this capture.",
              ),
        ),
        el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "section-title" },
            "Configuration findings by rule",
          ),
          ruleIds.length
            ? ruleIds.map((id) =>
                el(
                  "section",
                  { class: "rule-block", "data-rule-id": String(id) },
                  el("h3", { class: "mono" }, id),
                  (byRule.get(id) || []).length
                    ? byRule.get(id).map((f) => findingCard(f, false))
                    : el(
                        "p",
                        { class: "muted small" },
                        "No configuration findings for this rule.",
                      ),
                  el(
                    "div",
                    { class: "asks" },
                    askButton(
                      "Propose a change to this rule",
                      `Propose a change to rule ${id}.`,
                    ),
                  ),
                ),
              )
            : el("p", { class: "muted" }, "No rules in this capture."),
        ),
      );
    },

    proposals(container, s) {
      // Said on every visit, with or without a proposal, and there is no
      // control that could apply one: Iris proposes, people approve elsewhere.
      const banner = el(
        "p",
        { class: "notice not-applied", role: "note" },
        "Not applied. Needs external approval.",
      );
      if (!s) {
        container.append(heading("Proposals"), banner);
        return nothingYet(container);
      }
      const p = s.proposal;
      container.append(
        heading(
          "Proposals",
          "The newest rule change Iris drafted and validated in this session.",
          "iris_propose",
        ),
        banner,
      );
      if (!p || typeof p !== "object") {
        container.append(
          el(
            "div",
            { class: "card" },
            el("p", {}, "Iris has not drafted a proposal in this session."),
            el(
              "p",
              { class: "muted small" },
              "Ask her to propose a change to a rule from the Rules tab.",
            ),
          ),
        );
        return;
      }
      const view = p.proposal_view || {};
      const validation = view.validation || {};
      const errors = Array.isArray(validation.errors) ? validation.errors : [];
      const warnings = Array.isArray(validation.warnings)
        ? validation.warnings
        : [];
      const validity =
        validation.valid === true
          ? "Passed validation"
          : validation.valid === false
            ? "Failed validation"
            : "Validation not reported";
      container.append(
        el(
          "div",
          { class: "card proposal" },
          el(
            "dl",
            {},
            el("dt", {}, "Rule"),
            el("dd", { class: "mono" }, view.rule_id || "not named"),
            el("dt", {}, "Status"),
            el("dd", {}, p.proposal_status || "not reported"),
            el("dt", {}, "Validation"),
            el("dd", {}, validity),
            el("dt", {}, "Baseline hash"),
            el("dd", { class: "mono" }, view.baseline_hash || "not reported"),
          ),
          errors.length
            ? el(
                "ul",
                { class: "problems error" },
                errors.map((e) =>
                  el(
                    "li",
                    {},
                    `Error: ${typeof e === "string" ? e : JSON.stringify(e)}`,
                  ),
                ),
              )
            : null,
          warnings.length
            ? el(
                "ul",
                { class: "problems" },
                warnings.map((w) =>
                  el(
                    "li",
                    {},
                    `Warning: ${typeof w === "string" ? w : JSON.stringify(w)}`,
                  ),
                ),
              )
            : null,
          p.message ? el("p", { class: "small" }, p.message) : null,
          el("div", { class: "section-title" }, "Change"),
          view.diff
            ? el("pre", { class: "diff" }, String(view.diff))
            : el("p", { class: "muted small" }, "No diff in this proposal."),
          view.preview && typeof view.preview === "object"
            ? el(
                "details",
                {},
                el("summary", {}, "Iris's dry-run preview"),
                el(
                  "pre",
                  { class: "json" },
                  JSON.stringify(view.preview, null, 2),
                ),
              )
            : null,
          el(
            "div",
            { class: "asks" },
            el(
              "button",
              {
                type: "button",
                class: "chip",
                "data-busy-off": true,
                disabled: busy,
                onclick: () => draft("Revise the proposal: "),
              },
              "Revise the proposal",
            ),
          ),
        ),
      );
    },

    evidence(container, s) {
      if (!s) return nothingYet(container);
      const rows = evidenceRows(s);
      const daily = rows.filter((r) => r.row.source_tool === "get_summary");
      const other = rows.filter((r) => r.row.source_tool !== "get_summary");
      container.append(
        heading(
          "Evidence",
          "What Iris read, by id. Every finding's evidence links land here.",
          "iris_overview",
        ),
        investigationCard(s.investigation),
        el(
          "div",
          { class: "card" },
          el("div", { class: "section-title" }, "Evidence rows"),
          other.length
            ? other.map(({ source, row }) => evidenceCard(source, row))
            : el("p", { class: "muted" }, "No evidence rows in this capture."),
          daily.length
            ? el(
                "details",
                { class: "daily" },
                el("summary", {}, `Daily summaries (${daily.length})`),
                daily.map(({ source, row }) => evidenceCard(source, row)),
              )
            : null,
        ),
        downloadsCard(),
      );
    },

    results(container, s) {
      if (!s) return nothingYet(container);
      const tenant = overviewOf(s).tenant_id || "";
      const m = metricsOf(s);
      const key = `iris.baseline.${tenant}`;
      let baseline = null;
      try {
        baseline = JSON.parse(localStorage.getItem(key) || "null");
      } catch {
        baseline = null;
      }
      const pin = el(
        "button",
        {
          type: "button",
          class: "secondary",
          onclick: () => {
            try {
              localStorage.setItem(
                key,
                JSON.stringify({
                  tenant_id: tenant,
                  captured_at: s.captured_at,
                  metrics: m,
                }),
              );
            } catch {
              notice("This browser would not keep the baseline.", true);
            }
            render();
          },
        },
        baseline
          ? "Replace the baseline with this capture"
          : "Pin this capture as the baseline",
      );
      let body;
      if (!baseline) {
        body = el(
          "p",
          {},
          "Pin this capture as a baseline, make a change outside this workspace, then collect a later overview here to compare.",
        );
      } else if (
        !baseline ||
        typeof baseline !== "object" ||
        baseline.tenant_id !== tenant
      ) {
        body = el(
          "p",
          { class: "refused" },
          "This baseline belongs to a different tenant, so it is not compared.",
        );
      } else if (baseline.captured_at === s.captured_at) {
        body = el(
          "p",
          {},
          `The baseline is this capture (${when(baseline.captured_at) || "time not recorded"}). Collect a later overview to compare.`,
        );
      } else {
        body = comparison(
          `Baseline, ${when(baseline.captured_at) || "time not recorded"}`,
          baseline.metrics || {},
          "This capture",
          m,
        );
      }
      const inv = s.investigation;
      container.append(
        heading(
          "Results",
          "Did the change help? Same tenant, complete periods only. A difference is not proof of cause.",
          "iris_investigate",
        ),
        el(
          "div",
          { class: "card" },
          el("div", { class: "section-title" }, "Baseline and now"),
          body,
          el("div", { class: "asks" }, pin),
        ),
        inv && inv.current && inv.previous
          ? el(
              "div",
              { class: "card" },
              el(
                "div",
                { class: "section-title" },
                "Iris's own period comparison",
              ),
              comparison(
                "Previous period",
                inv.previous,
                "Current period",
                inv.current,
              ),
            )
          : null,
        el(
          "div",
          { class: "asks" },
          askButton("What changed?", "What changed?"),
        ),
      );
    },

    accounts(container) {
      const here = sessionTenant();
      container.append(
        heading(
          "Accounts",
          "Every tenant you can open. Opening one goes back to your last session on that tenant, or starts one if there is none; this one stays on its tenant.",
        ),
      );
      if (accounts.status === "idle") void loadAccounts();
      if (accounts.status === "idle" || accounts.status === "loading") {
        container.append(
          el("p", { class: "muted", role: "status" }, "Reading the account."),
        );
        return;
      }
      if (!here)
        container.append(
          el(
            "p",
            { class: "notice" },
            "This session's tenant could not be confirmed, so no tenant can be opened from here. Use the Iris landing page.",
          ),
        );
      if (accounts.error)
        container.append(
          el("p", { class: "notice error", role: "alert" }, accounts.error),
        );
      const rows = orderedRows(accounts.tenants, accounts.account);
      const generated = accounts.account && accounts.account.generated_at;
      const headers = [
        "Tenant",
        "Hit rate",
        "Cache misses",
        "Requests",
        "Coverage",
        "Captured",
        "Open",
      ];
      container.append(
        el(
          "div",
          { class: "card table-wrap" },
          el(
            "table",
            { class: "accounts", "aria-label": "Tenants in this account" },
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                headers.map((h) => el("th", {}, h)),
              ),
            ),
            el(
              "tbody",
              {},
              rows.map((entry) =>
                labelled(
                  headers,
                  el(
                    "tr",
                    { "data-tenant-id": entry.tenant_id },
                    el(
                      "td",
                      {},
                      el("strong", {}, entry.name || entry.tenant_id),
                      el(
                        "div",
                        { class: "muted small mono" },
                        entry.tenant_id.length > 20
                          ? entry.tenant_id.slice(0, 8)
                          : entry.tenant_id,
                      ),
                    ),
                    standing(entry, generated),
                    el(
                      "td",
                      {},
                      entry.tenant_id === here
                        ? el("span", { class: "badge accent" }, "This session")
                        : !here
                          ? el(
                              "span",
                              { class: "muted small" },
                              "Not available",
                            )
                          : entry.host_online === false
                            ? el(
                                "span",
                                { class: "muted small" },
                                "Host offline",
                              )
                            : el(
                                "button",
                                {
                                  type: "button",
                                  class: "chip",
                                  onclick: () => openTenant(entry.tenant_id),
                                },
                                "Open",
                              ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    },
  };

  /**
   * Name each cell of a row by its column, for the stacked layout on a phone
   * (style.css): a cell that spans columns says what it is by itself, and so
   * do the tenant and Open cells. The label is an attribute, shown by CSS.
   */
  function labelled(headers, row) {
    let column = 0;
    for (const cell of row.children) {
      const span = Number(cell.getAttribute("colspan")) || 1;
      const name = headers[column];
      if (span === 1 && column > 0 && column < headers.length - 1)
        cell.dataset.label = name;
      column += span;
    }
    return row;
  }

  function comparisonProblem(x, y) {
    if (!x || !y) return "A capture is missing its numbers.";
    if (x.period_complete !== true || y.period_complete !== true)
      return "Both periods need complete daily coverage before they are compared.";
    const duration = (m) => Date.parse(m.end_date) - Date.parse(m.start_date);
    if (
      !finite(duration(x)) ||
      duration(x) <= 0 ||
      duration(x) !== duration(y) ||
      x.requested_days !== y.requested_days
    )
      return "The two periods are not the same length.";
    if (Date.parse(y.start_date) < Date.parse(x.end_date))
      return "The later period must start after the earlier one ends, without overlap.";
    for (const m of [x, y])
      if (
        m.covered_days !== m.requested_days ||
        !finite(m.cache_hits) ||
        !finite(m.requests) ||
        m.requests <= 0 ||
        m.cache_hits > m.requests ||
        m.hit_rate_denominator !== m.requests ||
        !finite(m.hit_rate)
      )
        return "Both periods need measured hits over a positive number of requests.";
    return "";
  }

  function comparison(beforeLabel, x, afterLabel, y) {
    const problem = comparisonProblem(x, y);
    if (problem)
      return el("p", { class: "refused" }, `Not compared: ${problem}`);
    const delta = (y.hit_rate - x.hit_rate) * 100;
    return el(
      "table",
      { class: "comparison", "aria-label": `${beforeLabel} and ${afterLabel}` },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, ""),
          el("th", {}, beforeLabel),
          el("th", {}, afterLabel),
        ),
      ),
      el(
        "tbody",
        {},
        el(
          "tr",
          {},
          el("td", {}, "Period"),
          el("td", {}, period(x)),
          el("td", {}, period(y)),
        ),
        el(
          "tr",
          {},
          el("td", {}, "Cache hit rate"),
          el(
            "td",
            { class: "mono" },
            `${percent(x.hit_rate)} (${num(x.cache_hits)} over ${num(x.hit_rate_denominator)})`,
          ),
          el(
            "td",
            { class: "mono" },
            `${percent(y.hit_rate)} (${num(y.cache_hits)} over ${num(y.hit_rate_denominator)})`,
          ),
        ),
        el(
          "tr",
          {},
          el("td", {}, "Requests"),
          el("td", { class: "mono" }, num(x.requests)),
          el("td", { class: "mono" }, num(y.requests)),
        ),
        el(
          "tr",
          {},
          el("td", {}, "Change"),
          el(
            "td",
            { colspan: "2" },
            `${delta > 0 ? "+" : ""}${delta.toFixed(1)} percentage points. Check for workload changes before crediting a rule.`,
          ),
        ),
      ),
    );
  }

  function investigationCard(inv) {
    if (!inv || typeof inv !== "object")
      return el(
        "div",
        { class: "card" },
        el("div", { class: "section-title" }, "Period comparison"),
        el(
          "p",
          { class: "muted small" },
          "Iris has not compared periods in this session. Ask her to investigate a finding.",
        ),
      );
    const rows = [
      ["Period", (m) => period(m)],
      [
        "Cache hit rate",
        (m) =>
          `${percent(m.hit_rate)} (${num(m.cache_hits)} over ${num(m.hit_rate_denominator)})`,
      ],
      ["Requests", (m) => num(m.requests)],
      ["Cache misses", (m) => num(m.cache_misses)],
      [
        "Coverage",
        (m) => `${num(m.covered_days)} / ${num(m.requested_days)} days`,
      ],
    ];
    const cur = inv.current || {};
    const prev = inv.previous || {};
    const problems = [
      ...metricsProblems(prev, "The previous period"),
      ...metricsProblems(cur, "The current period"),
    ];
    if (problems.length)
      return el(
        "div",
        { class: "card" },
        el("div", { class: "section-title" }, "Period comparison"),
        el(
          "p",
          { class: "refused" },
          `Iris's period comparison is not shown. ${problems.join(" ")}`,
        ),
      );
    return el(
      "div",
      { class: "card" },
      el("div", { class: "section-title" }, "Period comparison"),
      asOf("iris_investigate"),
      el(
        "table",
        { "aria-label": "Previous and current period" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, ""),
            el("th", {}, "Previous"),
            el("th", {}, "Current"),
          ),
        ),
        el(
          "tbody",
          {},
          rows.map(([label, cell]) =>
            el(
              "tr",
              {},
              el("td", {}, label),
              el("td", { class: "mono" }, cell(prev)),
              el("td", { class: "mono" }, cell(cur)),
            ),
          ),
        ),
      ),
      inv.message ? el("p", { class: "small" }, inv.message) : null,
    );
  }

  function evidenceCard(source, row) {
    const limits = Array.isArray(row.limitations) ? row.limitations : [];
    return el(
      "article",
      { class: "evidence", "data-evidence-id": String(row.id) },
      el(
        "div",
        { class: "finding-head" },
        el("strong", { class: "mono" }, String(row.id)),
        badge(source),
        badge(
          row.status || "status unknown",
          row.status === "complete" ? "ok" : "warn",
        ),
      ),
      el(
        "div",
        { class: "muted small" },
        `${row.source_tool || "unknown source"} · ${row.start_date || "?"} to ${row.end_date || "?"}`,
      ),
      limits.length
        ? el(
            "ul",
            { class: "small" },
            limits.map((l) =>
              el("li", {}, typeof l === "string" ? l : JSON.stringify(l)),
            ),
          )
        : null,
      row.data !== undefined && row.data !== null
        ? el(
            "details",
            {},
            el("summary", {}, "Data"),
            el("pre", { class: "json" }, JSON.stringify(row.data, null, 2)),
          )
        : null,
      el(
        "div",
        { class: "asks" },
        askButton("Dig into this evidence", `Dig into evidence ${row.id}.`),
      ),
    );
  }

  function downloadsCard() {
    const list = el("div", { class: "downloads", role: "status" });
    return el(
      "div",
      { class: "card" },
      el("div", { class: "section-title" }, "Session downloads"),
      el(
        "p",
        { class: "muted small" },
        "Files Iris's tools wrote in this session: report.json, report.md and proposal.json.",
      ),
      el(
        "div",
        { class: "asks" },
        el(
          "button",
          {
            type: "button",
            class: "chip",
            onclick: () => void listDownloads(list),
          },
          "List session downloads",
        ),
      ),
      list,
    );
  }

  async function listDownloads(list) {
    const session = encodeURIComponent(SESSION_ID);
    list.replaceChildren("Reading the session's files.");
    try {
      const response = await fetch(
        `/v1/sessions/${session}/resources/files?limit=100`,
        { credentials: "same-origin" },
      );
      if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      const page = await response.json();
      const files = ((page && page.data) || []).filter((f) =>
        DOWNLOADS.includes(f.filename),
      );
      list.replaceChildren(
        ...(files.length
          ? files.map((f) =>
              el(
                "a",
                {
                  class: "chip",
                  href: `/v1/sessions/${session}/resources/files/${encodeURIComponent(f.id)}/content`,
                  download: f.filename,
                },
                f.filename,
              ),
            )
          : ["No files in this session yet."]),
      );
    } catch (error) {
      list.replaceChildren(`Downloads unavailable: ${reason(error)}.`);
    }
  }

  function showEvidence(id) {
    tabs.show("evidence");
    const target = [...document.querySelectorAll("[data-evidence-id]")].find(
      (node) => node.dataset.evidenceId === String(id),
    );
    if (!target) {
      notice(`Evidence ${id} is not in this capture.`, true);
      return;
    }
    const details = target.closest("details");
    if (details) details.open = true;
    target.classList.add("highlight");
    if (target.scrollIntoView) target.scrollIntoView({ block: "center" });
  }

  // ----------------------------------------------------------- accounts --

  async function getJson(path) {
    const response = await fetch(path, { credentials: "same-origin" });
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function loadAccounts() {
    accounts = { status: "loading", tenants: [], account: null, error: "" };
    const [catalog, account] = await Promise.allSettled([
      getJson("/v1/iris"),
      getJson("/v1/iris/account"),
    ]);
    accounts = {
      status: "done",
      tenants:
        catalog.status === "fulfilled" && Array.isArray(catalog.value.bindings)
          ? catalog.value.bindings.filter(
              (b) => b && typeof b.tenant_id === "string",
            )
          : [],
      account: account.status === "fulfilled" ? account.value : null,
      error:
        catalog.status === "rejected"
          ? `The tenant list could not be read: ${reason(catalog.reason)}.`
          : account.status === "rejected"
            ? `The account ranking could not be read: ${reason(account.reason)}.`
            : "",
    };
    if (currentTab && currentTab.id === "accounts") render();
  }

  /** The account decides the order; the catalog decides the set. */
  function orderedRows(tenants, account) {
    const byId = new Map(tenants.map((t) => [t.tenant_id, t]));
    const out = [];
    const placed = new Set();
    for (const [status, list] of [
      ["ranked", (account && account.ranked) || []],
      ["quarantined", (account && account.quarantined) || []],
    ])
      for (const row of list) {
        const tenant = row && byId.get(row.tenant_id);
        if (tenant && !placed.has(row.tenant_id)) {
          out.push({ ...tenant, status, row });
          placed.add(row.tenant_id);
        }
      }
    for (const tenant of tenants)
      if (!placed.has(tenant.tenant_id))
        out.push({ ...tenant, status: "missing" });
    return out;
  }

  const REASON_LABEL = {
    tenant_mismatch: "Capture names another tenant",
    unreadable_config: "Could not be read",
    metrics_disagree: "Metrics disagree",
    incomplete_period: "Incomplete period",
    never_collected: "Never collected",
  };

  function standing(entry, generated) {
    const coverageText = (r) =>
      r.covered_days === null ||
      r.covered_days === undefined ||
      r.requested_days === null ||
      r.requested_days === undefined
        ? ""
        : `${r.covered_days} / ${r.requested_days} days`;
    if (entry.status === "ranked") {
      const r = entry.row;
      return [
        el(
          "td",
          { class: "mono" },
          r.hit_rate === null ? "no traffic" : percent(r.hit_rate),
          el(
            "div",
            { class: "muted small" },
            `of ${num(r.hit_rate_denominator)} requests`,
          ),
        ),
        el("td", { class: "mono" }, num(r.cache_misses)),
        el("td", { class: "mono" }, num(r.requests)),
        el("td", {}, coverageText(r)),
        el("td", {}, age(r.age_seconds)),
      ];
    }
    if (entry.status === "quarantined") {
      const r = entry.row;
      const label = REASON_LABEL[r.reason] || r.reason;
      return [
        el("td", { colspan: "3" }, r.detail ? `${label}: ${r.detail}` : label),
        el("td", {}, coverageText(r)),
        el(
          "td",
          {},
          finite(generated) && finite(r.captured_at)
            ? age(Math.max(0, generated - r.captured_at))
            : "",
        ),
      ];
    }
    return [
      el(
        "td",
        { colspan: "5" },
        accounts.account
          ? "Not ranked: not in the account response"
          : "Not ranked: account unavailable",
      ),
    ];
  }

  /**
   * Ask the shell to open this tenant: it goes back to the caller's last
   * session on that tenant, or creates one when there is none. The frame never
   * creates a session and never switches this one's tenant: a session never
   * accumulates tenants. The shell checks origin, source and binding.
   */
  function openTenant(tenantId) {
    if (typeof tenantId !== "string" || tenantId === sessionTenant()) return;
    window.parent.postMessage(
      { type: "iris.openTenant", tenant_id: tenantId },
      location.origin,
    );
  }

  // -------------------------------------------------------------- tabs --

  // The workspace's tabs, in order: the one place tabs are listed. Overview is
  // home, where the chat is always shown; beside every other tab the chat is
  // docked and collapses to a rail.
  const TABS = [
    { id: "overview", label: "Overview", render: views.overview, home: true },
    { id: "findings", label: "Findings", render: views.findings },
    { id: "rules", label: "Rules", render: views.rules },
    { id: "proposals", label: "Proposals", render: views.proposals },
    { id: "evidence", label: "Evidence", render: views.evidence },
    { id: "results", label: "Results", render: views.results },
    { id: "accounts", label: "Accounts", render: views.accounts },
  ];

  // ------------------------------------------------------------ render --

  function renderHeader() {
    $("synthetic").hidden = !isSynthetic();
    let text;
    if (!state && refused) text = "Iris's capture is not shown";
    else if (!state && !loaded) text = "Reading this session…";
    else if (!state) text = "Iris has not read this tenant yet";
    else if (finite(state.captured_at))
      text = `Iris read this tenant ${when(state.captured_at)}`;
    else if (finite(state.cache_age_seconds))
      text = `Iris read this tenant ${age(state.cache_age_seconds)}`;
    else text = "Iris read this tenant at a time the host did not report";
    if (state && state.stale) text += ", may be out of date";
    $("freshness").textContent = text;
  }

  function renderHost() {
    let status;
    if (readinessError)
      status = `This host will not run turns in this session: ${readinessError}.`;
    else if (!readiness)
      status = "Checking what this host can confirm about this session.";
    else if (readiness.turn_completed_here)
      status = "Iris has completed a turn in this session, so turns work here.";
    else
      status =
        "Iris has not completed a turn in this session yet, so it is not yet shown that this host can reach her model. Ask her something, or collect a fresh overview: either one runs a turn.";
    $("host-status").textContent = status;
    $("host-status").dataset.state = readinessError
      ? "refused"
      : readiness && readiness.turn_completed_here
        ? "ok"
        : "unverified";
    const lines = [];
    if (
      readiness &&
      (!readiness.turn_completed_here || readiness.last_task_failed)
    ) {
      for (const item of readiness.unverified || [])
        lines.push(`Not verified: ${item}`);
      if (readiness.last_task_failed)
        lines.push(
          "Iris's last task in this session failed. Open native chat to see what happened.",
        );
    }
    const details = $("host-details");
    details.hidden = !lines.length;
    details.replaceChildren(...lines.map((line) => el("li", {}, line)));
    const box = $("collecting");
    box.hidden = !collecting;
    box.textContent =
      collecting === "first"
        ? "Collecting this tenant's first overview. Iris is running her tools against the warehouse, which usually takes about a minute."
        : collecting === "stale"
          ? `Refreshing an out-of-date capture. Iris is collecting a fresh overview, which usually takes about a minute.${ON_STALE === "show" && state ? " The last capture stays on screen until then." : ""}`
          : "";
  }

  function render() {
    renderHeader();
    renderHost();
    const container = $("view");
    container.replaceChildren();
    const tab = currentTab || TABS[0];
    tab.render(container, state, { ask, readiness });
  }

  function renderDock() {
    const tab = currentTab || TABS[0];
    const docked = !tab.home;
    document.body.dataset.tab = tab.id;
    dock.render({ docked });
    $("collapse").hidden = !docked;
    const about = docked && context.included();
    context.render(docked ? tab.label : "");
    $("context").hidden = !about;
    $("context-label").textContent = about ? `About ${tab.label}` : "";
  }

  // --------------------------------------------------------- the kernel --

  AW.theme.init({ messageKey: "irisHostTheme" });
  const api = AW.createApi({ base: "api/", timeoutMs: 330000 });
  const transcript = AW.createTranscript({
    list: $("messages"),
    agentName: AGENT,
  });
  const stream = AW.createStream({
    sessionId: SESSION_ID,
    pollMs: POLL_MS,
    doing: DOING,
    recognise: (text, item) => {
      if (!String(text).startsWith(REFRESH_SENTENCE)) return null;
      const at = item && finite(item.created_at) ? item.created_at : Infinity;
      lastCollectTurn = Math.max(lastCollectTurn, at);
      return "You had Iris collect a fresh overview.";
    },
    stripUser: (text) => String(text).replace(CONTEXT_NOTE, ""),
    transcript,
  });
  const dock = AW.createDock({
    chat: $("chat"),
    rail: $("dock-rail"),
    collapseButton: $("collapse"),
    storageKey: "iris.dockCollapsed",
  });
  const context = AW.createContextToggle({
    badge: $("context"),
    label: $("context-label"),
    button: $("context-remove"),
  });
  const tabs = AW.createTabs({
    nav: $("tabs"),
    tabs: TABS,
    onShow: (tab, prev) => {
      if (!prev || prev.id !== tab.id) context.reset();
      currentTab = tab;
      renderDock();
      render();
    },
  });

  // ------------------------------------------------------------- turns --

  function setBusy(value) {
    busy = value;
    $("send").disabled = value;
    $("refresh").disabled = value;
    $("cancel").hidden = !value;
    for (const button of document.querySelectorAll("button[data-busy-off]"))
      button.disabled = value;
  }

  async function stopTurn() {
    const stop = stopWatching;
    stopWatching = null;
    if (stop) await stop();
  }

  async function watch() {
    await stream.markExisting();
    stopWatching = stream.watchTurn(new Set());
  }

  const DAY_MS = 86400000;
  const dayOf = (date) => {
    const at = Date.parse(`${date}T00:00:00Z`);
    return /^\d{4}-\d{2}-\d{2}$/.test(String(date)) && Number.isFinite(at)
      ? at
      : null;
  };

  /**
   * An overview Iris read for a window that ended before the tenant's current
   * period: for a comparison, not the current period. The current period is
   * the `requested_days` completed days before the capture day, or the day
   * before that when the host dates it by a local calendar behind UTC.
   *
   * Decided from where the window starts and ends, never from how much of it
   * was covered: iris_overview narrows a partial capture's dates to the days
   * it covered (09-20..22 of 09-20..27), and that is still the current
   * period (QA N2). A window that starts in the current period, or lies
   * within it, is current. Only one that starts before the current period and
   * ends by its start is earlier.
   */
  function earlierWindow(s) {
    const m = metricsOf(s);
    const start = dayOf(m.start_date);
    const end = dayOf(m.end_date);
    if (start === null || end === null || !s || !finite(s.captured_at))
      return false;
    const captured = Math.floor((s.captured_at * 1000) / DAY_MS) * DAY_MS;
    const days =
      finite(m.requested_days) && m.requested_days > 0
        ? m.requested_days
        : Math.max(1, Math.round((end - start) / DAY_MS));
    // The earliest start of the current period: a day's lag allowed.
    const periodStart = captured - (days + 1) * DAY_MS;
    return start < periodStart && end <= periodStart + DAY_MS;
  }

  /**
   * The Overview stays on the tenant's current period. When a turn leaves an
   * overview for an earlier window (a "compare with last week"), the one on
   * screen is kept with its own time, and the rest of the new state is taken.
   * A current-period capture always replaces it, partial or not, even when
   * its narrowed dates end before the shown capture's (QA N2).
   */
  function keepCurrentPeriod(next) {
    if (!state || !next || !next.overview) return next;
    if (!earlierWindow(next) || earlierWindow(state)) return next;
    return {
      ...next,
      overview: state.overview,
      rules: state.rules,
      rule_effectiveness_meta: state.rule_effectiveness_meta,
      captured_at: state.captured_at,
      cache_age_seconds: state.cache_age_seconds,
      stale: state.stale,
      report_times: {
        ...next.report_times,
        iris_overview: state.report_times && state.report_times.iris_overview,
      },
    };
  }

  /**
   * Draw a state from the adapter only when it is this session's tenant and
   * its numbers agree with each other. The adapter refuses another tenant's
   * report too; this is the second check, and it does not depend on readiness
   * answering: without readiness the session's tenant is unknown, so nothing
   * is drawn.
   */
  function accept(incoming) {
    const next = keepCurrentPeriod(incoming);
    const bound = readiness && readiness.tenant_id;
    const problems = bound
      ? captureProblems(next, bound)
      : [
          "This session's tenant could not be confirmed, so no capture is drawn.",
        ];
    if (problems.length) {
      state = null;
      refused = `Iris's capture is not shown. ${problems.join(" ")}`;
      return false;
    }
    refused = "";
    state = next;
    return true;
  }

  /** Re-read state after a turn. Never collects: that happens once, on load. */
  async function reread() {
    try {
      const next = await api("state");
      accept(next);
    } catch (error) {
      if (error.status !== 409)
        notice(
          `The workspace could not re-read Iris's capture: ${reason(error)}.`,
          true,
        );
    }
    render();
  }

  async function ask(text, withContext = true) {
    if (busy || !String(text).trim()) return;
    const tab = currentTab || TABS[0];
    const note =
      !tab.home && withContext && context.included()
        ? `\n\n(I am looking at the ${tab.label} tab of the Iris workspace.)`
        : "";
    history.push({ role: "user", content: `${text}${note}` });
    while (history.length > 24) history.shift();
    transcript.add("user", text);
    setBusy(true);
    notice("");
    const shown = new Set();
    await stream.markExisting();
    stopWatching = stream.watchTurn(shown);
    try {
      const reply = await api("chat", { history, deadline: 300 });
      await stopTurn();
      if (
        !reply ||
        reply.failed ||
        typeof reply.text !== "string" ||
        !reply.text.trim()
      ) {
        const error = new Error("incomplete turn");
        error.incomplete = true;
        throw error;
      }
      history.push({ role: "assistant", content: reply.text });
      if (!shown.has(reply.text.trim())) transcript.add("agent", reply.text);
      // A turn can change what Iris has read; the answer does not carry state.
      void reread();
      void loadReadiness();
    } catch (error) {
      await stopTurn();
      // Drop the unanswered question so the next turn does not resend it.
      if (history.length && history[history.length - 1].role === "user")
        history.pop();
      const why =
        error && error.incomplete
          ? "Her turn ended without an answer"
          : reason(error);
      transcript.add(
        "error",
        `Iris did not answer. ${why}. Nothing has been computed in her place.`,
        () => void ask(text, withContext),
      );
    } finally {
      setBusy(false);
      render();
    }
  }

  /** Put text in the composer for the person to finish, and send nothing. */
  function draft(text) {
    dock.setCollapsed(false);
    const input = $("input");
    input.value = text;
    input.focus();
  }

  /**
   * One collection turn. It answers with the state itself, or 409 when the
   * turn produced no overview or was not started. The chat is kept either way.
   * `pressed` is the transcript line that recorded a manual press; it is taken
   * back when the adapter refused to start the collection.
   */
  async function collect(pressed) {
    setBusy(true);
    await watch();
    try {
      const next = await api("refresh", {});
      await stopTurn();
      if (accept(next)) heldStale = false;
      return true;
    } catch (error) {
      await stopTurn();
      const said = typeof error.detail === "string" ? error.detail : "";
      if (error.status === 409 && RAN_AND_MISSED.test(said))
        transcript.add(
          "system",
          state
            ? "That collection produced no new overview. The capture shown is the earlier one, with its time."
            : heldStale
              ? "That collection produced no new overview. The last capture is out of date and is not shown here. Ask Iris in the chat what went wrong."
              : "That collection produced no overview, so there is nothing to show yet. Ask Iris in the chat what went wrong.",
        );
      else if (error.status === 409) {
        if (pressed && NOT_STARTED.test(said)) pressed.remove();
        transcript.add("system", `${reason(error)}.`);
      } else
        transcript.add(
          "error",
          `Iris could not collect an overview. ${reason(error)}. Nothing has been computed in her place.`,
        );
      return false;
    } finally {
      setBusy(false);
      // A collection is a turn: what readiness can confirm may have changed.
      void loadReadiness();
    }
  }

  async function refresh() {
    if (busy) return;
    notice("");
    await collect(
      transcript.add("system", "You had Iris collect a fresh overview."),
    );
    render();
  }

  /**
   * Whether this load may collect on its own. At most once per session: not
   * when the session's record already holds a collect turn newer than the
   * capture it would replace (any collect turn, when there is no capture),
   * and not when this tab already started one for it. The record is on the
   * server, so another tab or device sees the same marker.
   */
  function mayAutoCollect(capturedAt) {
    const since = finite(capturedAt) ? capturedAt : -Infinity;
    if (lastCollectTurn > since) return false;
    try {
      if (sessionStorage.getItem(AUTO_KEY) === String(since)) return false;
    } catch {
      // Without storage the session's record still decides.
    }
    return true;
  }

  async function autoCollect(kind, capture) {
    if (autoCollected) return;
    const capturedAt = capture && capture.captured_at;
    if (!mayAutoCollect(capturedAt)) {
      autoSkipped = kind;
      if (state && kind === "stale")
        notice(
          `This capture may be out of date. Iris has already collected in this session since it was taken. ${ONCE_PER_SESSION}`,
        );
      render();
      return;
    }
    autoCollected = true;
    try {
      sessionStorage.setItem(
        AUTO_KEY,
        String(finite(capturedAt) ? capturedAt : -Infinity),
      );
    } catch {
      // The session's record still holds the collect turn.
    }
    collecting = kind;
    render();
    await collect();
    collecting = "";
    render();
  }

  async function loadReadiness() {
    try {
      readiness = await api("readiness");
      readinessError = "";
    } catch (error) {
      readiness = null;
      readinessError = reason(error);
    }
    renderHost();
  }

  /**
   * The page loaded while a turn was running (a reload mid-chat). Draw what
   * capture there is without collecting over the turn, follow the turn until
   * it ends so its answer shows, then read state as a load would.
   */
  async function resumeRunningTurn() {
    setBusy(true);
    let result = { resumed: false, status: "" };
    try {
      await firstRead({ mayCollect: false });
      // Seen running before the history was read: follow it even if it has
      // ended since, so its answer gets one last read (review N1).
      result = await stream.resumeTurn({ running: true });
    } finally {
      setBusy(false);
    }
    if (result.status === "failed")
      transcript.add(
        "error",
        "Iris's turn that was running when this page loaded failed. Open native chat to see what happened. Nothing has been computed in her place.",
      );
    else if (result.status === "timeout")
      transcript.add(
        "system",
        "Iris is still on the turn that was running when this page loaded. Open native chat to follow it.",
      );
    await loadReadiness();
    // After the give-up the turn may still be running; collecting over it
    // would only be refused as busy (review N2). Draw what there is.
    await firstRead({ mayCollect: result.status !== "timeout" });
  }

  /**
   * The first read of state on this load, and the only one that may collect
   * (not while a turn from before the load is still running: `mayCollect`).
   */
  async function firstRead({ mayCollect = true } = {}) {
    let first = null;
    try {
      first = await api("state");
      loaded = true;
    } catch (error) {
      loaded = true;
      if (error.status !== 409) {
        notice(`Iris's capture could not be read: ${reason(error)}.`, true);
        render();
        return;
      }
    }
    if (first === null) return mayCollect ? autoCollect("first") : render();
    if (!first.stale) {
      accept(first);
      render();
      return;
    }
    if (ON_STALE === "show") {
      // Shown with its label while one fresh collection runs behind it.
      accept(first);
      render();
      return mayCollect ? autoCollect("stale", first) : undefined;
    }
    heldStale = true;
    return mayCollect ? autoCollect("stale", first) : render();
  }

  // ------------------------------------------------------------ wiring --

  $("native-chat").setAttribute("href", `/c/${encodeURIComponent(SESSION_ID)}`);
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("input");
    const text = input.value;
    input.value = "";
    void ask(text);
  });
  $("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("refresh").addEventListener("click", () => void refresh());
  $("cancel").addEventListener("click", async () => {
    try {
      await api("cancel", {});
    } catch (error) {
      notice(`The stop was not accepted: ${reason(error)}.`, true);
    }
    await stopTurn();
    transcript.progress(null);
  });
  $("collapse").addEventListener("click", () => {
    dock.setCollapsed(true);
    renderDock();
  });
  $("dock-rail").addEventListener("click", () => {
    dock.setCollapsed(false);
    renderDock();
  });
  $("context-remove").addEventListener("click", () => renderDock());

  const fromHash = (location.hash || "").slice(1);
  tabs.show(TABS.some((t) => t.id === fromHash) ? fromHash : TABS[0].id, {
    fromHash: true,
  });

  (async () => {
    // The status is read before the history: a turn that ends after this read
    // is in the history, and one still running is followed (review N1).
    const status = await stream.sessionStatus();
    await stream.loadHistory();
    await loadReadiness();
    if (status === "running" || status === "waiting") await resumeRunningTurn();
    else await firstRead();
  })();
})();
