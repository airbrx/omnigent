// Eva's workspace. Framed by Omnigent at /eva/:sessionId and served per session
// at /v1/eva/sessions/{id}/ui/. Everything shown comes from api/state, which is
// built from Eva's own recorded tool results; nothing here invents data.
//
// Rendering uses DOM nodes and textContent only. Lead and draft text is data
// from a CRM and must never be parsed as markup.
(() => {
  "use strict";

  // Theme: the host's appearance on the URL at mount, then by message.
  const params = new URLSearchParams(location.search);
  function applyTheme(mode) {
    if (mode === "dark" || mode === "light")
      document.documentElement.dataset.theme = mode;
  }
  applyTheme(
    params.get("theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    if (event.data && typeof event.data.evaHostTheme === "string")
      applyTheme(event.data.evaHostTheme);
  });

  // The workspace's tabs, in order. Edit this list to add, remove or reorder
  // tabs. `path` is the outreach app page framed for the tab: Omnigent proxies
  // /eva/app to the outreach app, which drops its own nav there. Chat has no
  // path; it is Eva's own view, and her chat is docked beside every other tab.
  const TABS = [
    { id: "chat", label: "Chat" },
    { id: "leads", label: "Leads", path: "/eva/app/leads" },
    { id: "pool", label: "Pool", path: "/eva/app/leads/pool" },
    { id: "accounts", label: "Accounts", path: "/eva/app/accounts" },
    { id: "analytics", label: "Analytics", path: "/eva/app/analytics" },
    { id: "scoreboard", label: "Scoreboard", path: "/eva/app/scoreboard" },
    { id: "linkedin", label: "LinkedIn", path: "/eva/app/linkedin" },
    { id: "plan", label: "GTM plan", path: "/eva/app/plan" },
    { id: "guardrails", label: "Guardrails", path: "/eva/app/guardrails" },
    { id: "sync", label: "Sync", path: "/eva/app/sync" },
    { id: "settings", label: "Settings", path: "/eva/app/settings/token" },
  ];
  const VIEWS = ["pipeline", "mine", "drafts"];
  const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const LEAD_PATH =
    /^\/eva\/app\/leads\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i;
  // A scoreboard month as the page sends it: a real year and month, nothing else.
  const MONTH = /^20\d{2}-(0[1-9]|1[0-2])$/;
  const DOCK_KEY = "eva.dockCollapsed";
  // What a tile shows for a count Eva has not read. An en dash, never an em dash.
  const UNREAD = "\u2013";

  const $ = (id) => document.getElementById(id);
  let state = null;
  let view = "pipeline";
  let tab = TABS[0];
  let includeContext = true;
  let dockCollapsed = false;
  try {
    dockCollapsed = localStorage.getItem(DOCK_KEY) === "1";
  } catch {
    dockCollapsed = false;
  }
  let selectedLead = null;
  let busy = false;
  const history = [];
  const SESSION_ID = decodeURIComponent(
    (/\/eva\/sessions\/([^/]+)\/ui\//.exec(location.pathname) || [])[1] || "",
  );
  const POLL_MS = Number(document.body.dataset.pollMs) || 1500;

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
    }
    return node;
  }

  async function api(path, body) {
    const response = await fetch(`api/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const detail =
        payload && typeof payload.detail === "string"
          ? payload.detail
          : `HTTP ${response.status}`;
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  /** The adapter's state, or null while it has nothing to show (409). */
  async function loadState() {
    try {
      return await api("state");
    } catch (error) {
      if (error.status === 409) return null;
      throw error;
    }
  }

  // ---------------------------------------------------------------- helpers

  const leadId = (row) => row && (row.lead_id || row.id);
  const allLeadRows = () => [
    ...((state && state.pool && state.pool.leads) || []),
    ...((state && state.mine && state.mine.leads) || []),
  ];
  const outreach = (path) =>
    state && state.outreach_url ? `${state.outreach_url}${path}` : null;

  function statusBadge(status) {
    const kind =
      {
        approved: "ok",
        sent: "ok",
        in_review: "accent",
        draft: "info",
        rejected: "urgent",
      }[status] || "";
    return el(
      "span",
      { class: `badge ${kind}` },
      (status || "unknown").replace("_", " "),
    );
  }

  function when(value) {
    if (!value) return "";
    const date =
      typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function shortWhen(value) {
    const date = new Date(value * 1000);
    return Number.isNaN(date.getTime())
      ? String(value)
      : date.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        });
  }

  function notice(text, isError) {
    const box = $("notice");
    box.hidden = !text;
    box.className = isError ? "notice error" : "notice";
    box.textContent = text || "";
  }

  // ---------------------------------------------------------------- views

  function leadTable(rows, withClaim) {
    if (!rows.length) return el("p", { class: "empty" }, "No leads here yet.");
    return el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", {}, "Lead"),
          el("th", {}, "Company"),
          el("th", {}, "Warehouse"),
          el("th", {}, "Status"),
          el("th", {}, withClaim ? "Claim" : "Priority"),
        ),
      ),
      el(
        "tbody",
        {},
        rows.map((row) =>
          el(
            "tr",
            {
              class: `row ${leadId(row) === selectedLead ? "selected" : ""}`,
              onclick: () => selectLead(leadId(row)),
            },
            el(
              "td",
              {},
              el("strong", {}, row.name || "Unnamed"),
              el("div", { class: "muted small" }, row.title || ""),
            ),
            el("td", {}, row.company || ""),
            el(
              "td",
              {},
              row.warehouse || el("span", { class: "muted" }, "unknown"),
            ),
            el(
              "td",
              {},
              el("span", { class: "badge" }, row.lead_status || "New"),
            ),
            el(
              "td",
              { class: "mono" },
              withClaim
                ? row.claim && row.claim.days_remaining !== undefined
                  ? `${row.claim.days_remaining} days`
                  : row.relationship || ""
                : row.priority !== undefined && row.priority !== null
                  ? `P${row.priority}`
                  : "",
            ),
          ),
        ),
      ),
    );
  }

  function draftsView() {
    const drafts = (state && state.drafts) || [];
    if (!drafts.length)
      return el(
        "p",
        { class: "empty" },
        "No drafts yet. Pick a lead and ask Eva to draft a first touch.",
      );
    return el(
      "div",
      { class: "drafts" },
      drafts.map((d) => {
        const rules = (d.rule_results || []).filter((r) => r.status === "fail");
        const link = outreach(`/drafts/${encodeURIComponent(d.draft_id)}`);
        return el(
          "div",
          { class: "card", style: "margin-bottom:12px" },
          el(
            "div",
            {
              style:
                "display:flex;justify-content:space-between;gap:12px;align-items:start",
            },
            el(
              "div",
              {},
              el("strong", {}, d.subject || "(no subject)"),
              el(
                "div",
                { class: "muted small" },
                [
                  d.company,
                  d.name,
                  d.channel,
                  d.version_no ? `v${d.version_no}` : null,
                ]
                  .filter(Boolean)
                  .join(" · "),
              ),
            ),
            el(
              "div",
              {},
              d.blocked
                ? el("span", { class: "badge urgent" }, "blocked")
                : statusBadge(d.status),
            ),
          ),
          rules.length
            ? el(
                "ul",
                { class: "rules" },
                rules.map((r) =>
                  el(
                    "li",
                    { class: r.severity === "block" ? "block" : "warn" },
                    el(
                      "span",
                      {},
                      el("strong", {}, r.key),
                      " ",
                      r.message || "",
                    ),
                    el(
                      "span",
                      {
                        class: `badge ${r.severity === "block" ? "urgent" : "warn"}`,
                      },
                      r.severity,
                    ),
                  ),
                ),
              )
            : el(
                "p",
                { class: "muted small", style: "margin-top:8px" },
                d.rule_results
                  ? "All guardrails passed."
                  : "Guardrail results appear once Eva submits this draft.",
              ),
          d.status === "in_review"
            ? el(
                "p",
                { class: "small", style: "margin-top:8px" },
                `Waiting on ${d.approval_requested_from || "the lead owner"} to approve. Approval happens in the outreach app, never here.`,
              )
            : null,
          el(
            "div",
            { class: "asks" },
            d.status === "draft" && !d.blocked
              ? el(
                  "button",
                  {
                    class: "chip",
                    type: "button",
                    onclick: () =>
                      ask(
                        `Request approval for draft ${d.draft_id} with request_approval, then tell me who it went to.`,
                      ),
                  },
                  "Request approval",
                )
              : null,
            d.blocked
              ? el(
                  "button",
                  {
                    class: "chip",
                    type: "button",
                    onclick: () =>
                      ask(
                        `Draft ${d.draft_id} was blocked by the guardrails. Rewrite it to pass them and submit_draft again with the same draft_id.`,
                      ),
                  },
                  "Fix and resubmit",
                )
              : null,
            link
              ? el(
                  "a",
                  {
                    class: "chip",
                    href: link,
                    onclick: (event) => openInWorkspace(event, link),
                  },
                  "Open in outreach app",
                )
              : null,
          ),
        );
      }),
    );
  }

  function renderView() {
    const container = $("view");
    container.replaceChildren();
    for (const button of document.querySelectorAll("#views button"))
      button.classList.toggle("active", button.dataset.view === view);
    const unread = !state || state.empty;
    // No row of zeros before Eva has read anything: zeros beside a Pool tab
    // full of leads reads as broken.
    $("kpis").hidden = unread;
    $("views").hidden = unread;
    if (unread) {
      container.append(
        el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "section-title" },
            "Eva hasn't read the pipeline yet",
          ),
          el(
            "p",
            {},
            "This view shows only what Eva has read in this session. The Pool and Leads tabs show the outreach app itself and are always current.",
          ),
          el(
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
              "Have Eva read it now",
            ),
          ),
        ),
      );
      return;
    }
    if (view === "drafts") {
      container.append(
        el(
          "div",
          { class: "card" },
          el("div", { class: "section-title" }, "Drafts and approvals"),
          draftsView(),
        ),
      );
    } else if (view === "mine") {
      container.append(
        el(
          "div",
          { class: "card" },
          el("div", { class: "section-title" }, "My leads"),
          leadTable((state.mine && state.mine.leads) || [], true),
        ),
      );
    } else {
      const total = state.pool && state.pool.total;
      const rows = (state.pool && state.pool.leads) || [];
      container.append(
        el(
          "div",
          { class: "card" },
          el(
            "div",
            { class: "section-title" },
            total !== undefined && total !== null
              ? `Pool · ${rows.length} of ${total} shown`
              : "Pool",
          ),
          leadTable(rows, false),
        ),
      );
    }
  }

  function renderDetail() {
    const box = $("detail");
    box.replaceChildren();
    if (!selectedLead || view === "drafts") {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const full = state && state.leads && state.leads[selectedLead];
    const row = allLeadRows().find((r) => leadId(r) === selectedLead) || {};
    const profile = (full && full.profile) || row;
    const link = outreach(`/leads/${encodeURIComponent(selectedLead)}`);
    box.append(
      el("h2", {}, profile.name || "Lead"),
      el(
        "div",
        { class: "sub" },
        [profile.title, profile.company, profile.city]
          .filter(Boolean)
          .join(" · "),
      ),
      el(
        "div",
        { class: "asks" },
        el(
          "button",
          {
            class: "chip",
            type: "button",
            onclick: () =>
              ask(
                `Get lead ${selectedLead} with get_lead, include everything, and summarize who this is and what we know.`,
              ),
          },
          full ? "Reload detail" : "Load detail",
        ),
        profile.claim
          ? null
          : el(
              "button",
              {
                class: "chip",
                type: "button",
                onclick: () =>
                  ask(
                    `Claim lead ${selectedLead} (${profile.name || ""}, ${profile.company || ""}) with claim_lead, then get_lead it.`,
                  ),
              },
              "Claim",
            ),
        el(
          "button",
          {
            class: "chip",
            type: "button",
            onclick: () =>
              ask(
                `Qualify lead ${selectedLead}: read it with get_lead, then save_qualification with your assessment and rationale. Open and close a record_agent_run.`,
              ),
          },
          "Qualify",
        ),
        el(
          "button",
          {
            class: "chip",
            type: "button",
            onclick: () =>
              ask(
                `Draft a first-touch email for lead ${selectedLead}. Call list_guardrails with this lead_id first, then submit_draft. Report the rule results.`,
              ),
          },
          "Draft first touch",
        ),
        link
          ? el(
              "a",
              {
                class: "chip",
                href: link,
                onclick: (event) => openInWorkspace(event, link),
              },
              "Open in outreach app",
            )
          : null,
      ),
    );
    if (!full) {
      box.append(
        el(
          "p",
          { class: "muted small" },
          "Only the list row is loaded. Load detail asks Eva to read the full lead.",
        ),
      );
      return;
    }
    const q = full.qualification;
    const facts = [
      ["Status", profile.lead_status],
      ["Warehouse", profile.warehouse],
      ["Pillar", profile.lead_pillar],
      ["Role", profile.role_type],
      ["Source", profile.lead_source],
      ["Owner", profile.account_owner],
      ["Last contact", profile.last_contact_date],
      [
        "Claim",
        profile.claim
          ? `${profile.claim.rep || "claimed"} until ${when(profile.claim.expires_at)}`
          : "unclaimed",
      ],
      [
        "Qualification",
        q
          ? `score ${q.score}, ${q.warehouse_fit || "fit unknown"}`
          : "not qualified yet",
      ],
    ].filter(([, v]) => v !== undefined && v !== null && v !== "");
    box.append(
      el(
        "dl",
        {},
        facts.map(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]),
      ),
    );
    if (q && q.rationale)
      box.append(
        el("div", { class: "section-title" }, "Why"),
        el("p", { class: "small" }, q.rationale),
      );
    const touches = full.touches || [];
    box.append(
      el(
        "div",
        { class: "section-title", style: "margin-top:12px" },
        `Touches · ${touches.length}`,
      ),
      touches.length
        ? el(
            "ul",
            { class: "rules" },
            touches.map((t) =>
              el(
                "li",
                {},
                el(
                  "span",
                  {},
                  `${t.channel || ""} ${t.direction || ""}, ${t.outcome || ""}`,
                  t.notes ? `: ${t.notes}` : "",
                ),
                el("span", { class: "muted small" }, when(t.occurred_at)),
              ),
            ),
          )
        : el("p", { class: "muted small" }, "No touches recorded."),
    );
  }

  function renderKpis() {
    const pool = state && state.pool;
    const mine = state && state.mine;
    const drafts = (state && state.drafts) || [];
    // A count Eva has not read is a dash, not a zero: "0 claimable leads"
    // beside a Pool tab full of leads reads as broken.
    const poolCount = pool ? (pool.total ?? pool.leads.length) : null;
    const mineCount = mine ? (mine.total ?? mine.leads.length) : null;
    const review = drafts.filter((d) => d.status === "in_review").length;
    for (const [id, value] of [
      ["kpi-pool", poolCount],
      ["nav-pool", poolCount],
      ["kpi-mine", mineCount],
      ["nav-mine", mineCount],
      ["kpi-drafts", drafts.length],
      ["nav-drafts", drafts.length],
      ["kpi-review", review],
    ])
      $(id).textContent = value === null ? UNREAD : String(value);
    for (const [id, value, note] of [
      ["kpi-pool", poolCount, "claimable leads"],
      ["kpi-mine", mineCount, "owned or claimed"],
    ]) {
      const tile = $(id).parentElement;
      tile.classList.toggle("unread", value === null);
      tile.querySelector(".note").textContent =
        value === null ? "not read yet" : note;
    }
    $("freshness").textContent =
      state && state.refreshed_at
        ? `Eva read the pipeline ${shortWhen(state.refreshed_at)}${state.stale ? ", may be out of date" : ""}`
        : "Eva has not read the pipeline yet";
  }

  function render() {
    renderKpis();
    renderView();
    renderDetail();
    const errors = (state && state.errors) || [];
    if (errors.length)
      notice(
        `Eva's last tool error: ${errors[errors.length - 1].tool}: ${errors[errors.length - 1].message}`,
        true,
      );
  }

  function selectLead(id) {
    selectedLead = id;
    renderView();
    renderDetail();
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------------------------------------------------------- chat

  // Eva writes a little markdown. Her answers render a safe subset of it:
  // bold, italics, inline code, bullet and numbered lists, and line breaks.
  // Everything is built as DOM nodes with text content, so any HTML in her
  // text (which can quote CRM data) shows as the characters it is and never
  // becomes markup. Anything outside the subset shows as written.
  const INLINE = new RegExp(
    [
      /`([^`\n]+)`/.source,
      /\*\*(?=\S)([^\n]*?\S)\*\*/.source,
      /(?<!\w)__(?=\S)([^\n]*?\S)__(?!\w)/.source,
      /\*([^\s*](?:[^*\n]*?[^\s*])?)\*/.source,
      /(?<!\w)_([^\s_](?:[^_\n]*?[^\s_])?)_(?!\w)/.source,
    ].join("|"),
  );

  function inline(text) {
    const out = [];
    let rest = text;
    for (let match = INLINE.exec(rest); match; match = INLINE.exec(rest)) {
      if (match.index) out.push(rest.slice(0, match.index));
      const [, code, bold, bold2, em, em2] = match;
      if (code !== undefined) out.push(el("code", {}, code));
      else if (bold !== undefined || bold2 !== undefined)
        out.push(el("strong", {}, inline(bold ?? bold2)));
      else out.push(el("em", {}, inline(em ?? em2)));
      rest = rest.slice(match.index + match[0].length);
    }
    if (rest) out.push(rest);
    return out;
  }

  const BULLET = /^\s*[-*+]\s+(.*)$/;
  const NUMBERED = /^\s*(\d{1,9})[.)]\s+(.*)$/;
  const HEADING = /^\s*#{1,6}\s+(.*)$/;

  /** Eva's text as nodes: paragraphs, lists and inline marks, never HTML. */
  function markdown(text) {
    const blocks = [];
    let paragraph = null;
    let list = null;
    for (const line of String(text).replace(/\r\n?/g, "\n").split("\n")) {
      const bullet = BULLET.exec(line);
      const numbered = !bullet && NUMBERED.exec(line);
      if (bullet || numbered) {
        paragraph = null;
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = el(
            tag,
            numbered && numbered[1] !== "1"
              ? { start: Number(numbered[1]) }
              : {},
          );
          blocks.push(list);
        }
        list.append(el("li", {}, inline(bullet ? bullet[1] : numbered[2])));
        continue;
      }
      list = null;
      if (!line.trim()) {
        paragraph = null;
        continue;
      }
      const heading = HEADING.exec(line);
      if (heading) {
        paragraph = null;
        blocks.push(el("p", {}, el("strong", {}, inline(heading[1]))));
        continue;
      }
      if (paragraph) paragraph.append(el("br"), ...inline(line));
      else blocks.push((paragraph = el("p", {}, inline(line))));
    }
    return blocks;
  }

  /** What each of Eva's answer lines says, as she wrote it. */
  const rawText = new WeakMap();

  function setEvaText(line, text) {
    rawText.set(line, text);
    line.replaceChildren(...markdown(text));
  }

  function addMessage(kind, text, retry) {
    const list = $("messages");
    if (kind === "eva" && waitingLine) {
      waitingLine.remove();
      waitingLine = null;
    }
    const line = list.appendChild(
      el(
        "li",
        { class: kind },
        kind === "eva" ? null : text,
        retry
          ? el(
              "button",
              {
                type: "button",
                class: "chip retry",
                disabled: busy,
                onclick: retry,
              },
              "Try again",
            )
          : null,
      ),
    );
    if (kind === "eva") setEvaText(line, text);
    list.scrollTop = list.scrollHeight;
    return line;
  }

  // ------------------------------------------------ the session's own record
  //
  // The adapter's chat call returns only when Eva's turn is over, which can be
  // minutes. So while it runs, the dock reads the native session's items, the
  // same record native chat shows, and puts her interim messages and what she
  // is doing into the chat as they arrive. The same read rebuilds the chat on
  // load, so a reload does not lose the conversation.

  const handled = new Set();
  const answerLines = new Map();
  let progressLine = null;
  let waitingLine = null;

  /** What each of Eva's tools is doing, in a rep's words, never its name. */
  const DOING = {
    list_pool: "Reading the pool",
    list_my_leads: "Reading your leads",
    get_lead: "Reading a lead",
    claim_lead: "Claiming a lead",
    release_lead: "Releasing a lead",
    add_lead: "Adding a lead",
    update_lead_fields: "Updating a lead",
    save_qualification: "Saving a qualification",
    submit_draft: "Checking a draft against the guardrails",
    add_comment: "Adding a comment",
    request_approval: "Asking for approval",
    log_touch: "Logging a touch",
    list_guardrails: "Reading the guardrails",
    query_analytics: "Reading the analytics",
    record_agent_run: "Recording her work",
    list_linkedin_posts: "Reading the LinkedIn posts",
    record_linkedin_metrics: "Recording LinkedIn post stats",
    set_linkedin_post_url: "Recording a LinkedIn post's address",
    list_plan_items: "Reading the GTM plan",
    record_plan_actual: "Recording a plan actual",
    ToolSearch: "Getting her tools ready",
  };

  async function sessionItems(limit) {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(SESSION_ID)}/items?order=desc&limit=${limit}`,
      { credentials: "same-origin" },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json();
    return {
      items: (page.data || []).slice().reverse(),
      hasMore: Boolean(page.has_more),
    };
  }

  function itemText(item) {
    return (Array.isArray(item.content) ? item.content : [])
      .filter((c) => c.type === "output_text" || c.type === "input_text")
      .map((c) => c.text || "")
      .join("\n");
  }

  /** A rep's own message as they wrote it: without the page note the dock adds. */
  function repText(text) {
    return text.replace(
      /\n\n\(I am looking at [\s\S]* in the outreach app\.\)$/,
      "",
    );
  }

  function showProgress(text) {
    if (!progressLine) {
      progressLine = addMessage("system", text);
      progressLine.classList.add("progress");
      progressLine.setAttribute("role", "status");
    } else progressLine.textContent = text;
    $("messages").append(progressLine);
  }

  function clearProgress() {
    if (progressLine) progressLine.remove();
    progressLine = null;
  }

  /**
   * Put one session item in the chat. `live` is a turn in progress: her
   * messages and what she is doing. Otherwise it is history: both sides.
   */
  function applyItem(item, live, shown) {
    if (!item || !item.id) return;
    if (item.type === "message" && item.role === "assistant") {
      const text = itemText(item);
      if (!text.trim()) return;
      const line = answerLines.get(item.id);
      if (line) {
        if (rawText.get(line) !== text) setEvaText(line, text);
        // The grown text is what the reply will carry, so it is shown too.
        if (shown) shown.add(text.trim());
        return;
      }
      if (handled.has(item.id)) return;
      handled.add(item.id);
      answerLines.set(item.id, addMessage("eva", text));
      if (shown) shown.add(text.trim());
      if (progressLine) $("messages").append(progressLine);
      return;
    }
    if (handled.has(item.id)) return;
    handled.add(item.id);
    if (item.type === "function_call" && live) {
      const name = String(item.name || "")
        .split("__")
        .pop();
      showProgress(`${DOING[name] || "Working"}…`);
    } else if (item.type === "message" && item.role === "user" && !live) {
      const text = itemText(item);
      if (text.startsWith("Workspace refresh."))
        addMessage("system", "You had Eva read the pipeline.");
      else if (text.trim()) addMessage("user", repText(text));
    }
  }

  /** Mark everything already in the session as shown, before a new turn. */
  async function markExisting() {
    try {
      const { items } = await sessionItems(200);
      for (const item of items) handled.add(item.id);
    } catch {
      // Without the record the turn still answers; it just cannot stream.
    }
  }

  /** Stream a running turn into the chat until the returned stop is called. */
  function watchTurn(shown) {
    let stopped = false;
    let timer = null;
    showProgress("Eva is working…");
    const tick = async () => {
      if (stopped) return;
      try {
        const { items } = await sessionItems(50);
        if (!stopped) for (const item of items) applyItem(item, true, shown);
      } catch {
        // A missed poll is harmless: the next one, or the answer, catches up.
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return async () => {
      stopped = true;
      clearTimeout(timer);
      try {
        const { items } = await sessionItems(50);
        for (const item of items) applyItem(item, true, shown);
      } catch {
        // The adapter's answer below still shows.
      }
      clearProgress();
    };
  }

  /** Rebuild the chat from the session, so a reload keeps the conversation. */
  async function loadHistory() {
    try {
      const { items, hasMore } = await sessionItems(200);
      if (hasMore) addMessage("system", "Earlier messages are in native chat.");
      for (const item of items) applyItem(item, false);
      return answerLines.size > 0;
    } catch {
      return false;
    }
  }

  /** An adapter failure in words a rep can act on. */
  function plainError(error) {
    const raw = (error && error.message) || "";
    const status = error && error.status;
    if (
      /native session operation failed/i.test(raw) ||
      status === 502 ||
      status === 503
    )
      return "Eva could not be reached just now. Her host may be offline or restarting.";
    if (status === 504 || /timed out/i.test(raw))
      return "Eva took too long, so the turn was stopped.";
    if (status === 401)
      return "Your Omnigent sign-in has expired. Reload the page to sign in again.";
    if (!status)
      return "The workspace could not reach Omnigent. Check your connection.";
    if (status >= 500) return "Something went wrong on the Omnigent side.";
    // The adapter's own 4xx details are written for people.
    return raw;
  }

  function setBusy(value) {
    busy = value;
    $("send").disabled = value;
    $("refresh").disabled = value;
    $("cancel").hidden = !value;
    for (const button of document.querySelectorAll(
      "button.chip, button[data-busy-off]",
    ))
      button.disabled = value;
  }

  async function ask(text, withContext = true) {
    if (busy || !text.trim()) return;
    const page =
      tab.path && includeContext && withContext ? pageContext() : null;
    history.push({
      role: "user",
      content: page
        ? `${text}\n\n(I am looking at ${page.sentence} in the outreach app.)`
        : text,
    });
    while (history.length > 24) history.shift();
    addMessage("user", page ? `${text}\n\nAbout ${page.label}` : text);
    setBusy(true);
    notice("");
    const shown = new Set();
    await markExisting();
    const stopWatching = watchTurn(shown);
    try {
      const reply = await api("chat", { history });
      await stopWatching();
      history.push({ role: "assistant", content: reply.text });
      if (!shown.has((reply.text || "").trim())) addMessage("eva", reply.text);
      // A turn can change what Eva has read; the answer does not carry state.
      loadState()
        .then((next) => {
          if (next) {
            state = next;
            render();
          }
        })
        .catch(() => {});
    } catch (error) {
      await stopWatching();
      // Drop the unanswered question so the next turn does not resend it.
      history.pop();
      addMessage("error", plainError(error), () => void ask(text, withContext));
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------------------- tabs

  function tabFromHash() {
    const id = (location.hash || "").slice(1);
    if (VIEWS.includes(id)) view = id;
    return TABS.find((t) => t.id === id) || TABS[0];
  }

  /** The framed page's current path, so navigation inside it is picked up. */
  function currentFrame() {
    return document.querySelector(`#frames iframe[data-tab="${tab.id}"]`);
  }

  function framedPath() {
    const frame = currentFrame();
    try {
      const where =
        frame && frame.contentWindow && frame.contentWindow.location;
      if (where && where.pathname && where.pathname !== "blank")
        return `${where.pathname}${where.search}`;
    } catch {
      // A page that left this origin cannot be read; its src still helps.
    }
    const src = frame && frame.getAttribute("src");
    return src && src.startsWith("/") ? src : tab.path;
  }

  /** CRM text headed for Eva's prompt: one line, bounded, never markup. */
  function cleanName(raw) {
    if (typeof raw !== "string") return "";
    return raw
      .replace(/[\u0000-\u001f\u007f]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 120);
  }

  /**
   * What the rep is looking at in the open tab. On a lead page that is the
   * lead: its id from the path, its name from the page's `data-eva-lead-name`
   * or, failing that, the page title.
   */
  function pageContext() {
    const path = framedPath();
    const match = LEAD_PATH.exec(path.split("?")[0]);
    if (!match) return { label: tab.label, sentence: `${tab.label}, ${path}` };
    const id = match[1].toLowerCase();
    let name = "";
    try {
      const doc = currentFrame().contentDocument;
      const marked = doc && doc.querySelector(`[data-eva-lead-id="${id}"]`);
      name = cleanName(marked && marked.getAttribute("data-eva-lead-name"));
      if (!name && doc) {
        const title = cleanName(doc.title.split(" | ")[0]);
        if (title && title !== "Airbrx Outreach") name = title;
      }
    } catch {
      // Unreadable page: the id alone still tells Eva which lead.
    }
    return {
      label: name || "this lead",
      sentence: name
        ? `the lead ${name} (id ${id}) at ${path}`
        : `the lead with id ${id} at ${path}`,
    };
  }

  function renderTabs() {
    const nav = $("tabs");
    if (!nav.children.length)
      nav.append(
        ...TABS.map((t) =>
          el(
            "button",
            {
              type: "button",
              role: "tab",
              "data-tab": t.id,
              onclick: () => (t.id === tab.id ? restartTab(t) : showTab(t)),
            },
            t.label,
          ),
        ),
      );
    for (const button of nav.children) {
      const selected = button.dataset.tab === tab.id;
      button.setAttribute("aria-selected", String(selected));
      button.classList.toggle("active", selected);
    }
  }

  function renderDock() {
    const docked = Boolean(tab.path);
    const collapsed = docked && dockCollapsed;
    document.body.dataset.tab = tab.id;
    document.body.classList.toggle("docked", docked);
    document.body.classList.toggle("collapsed", collapsed);
    $("home").hidden = docked;
    // Freshness and refresh are about the pipeline, which only the Chat tab shows.
    $("actions").hidden = docked;
    $("frames").hidden = !docked;
    $("chat").hidden = collapsed;
    $("dock-rail").hidden = !collapsed;
    $("collapse").hidden = !docked;
    $("context").hidden = !docked || !includeContext;
    const about = docked ? pageContext().label : "";
    $("context-label").textContent = docked ? `About ${about}` : "";
    $("input").placeholder = docked
      ? `Ask Eva about ${about}. Enter sends, Shift+Enter adds a line.`
      : "Ask Eva to find, qualify or draft. Enter sends, Shift+Enter adds a line.";
  }

  function showTab(next, fromHash) {
    if (next.id !== tab.id) includeContext = true;
    tab = next;
    // Framed pages mount on first visit and stay, so each keeps its place.
    const frames = $("frames");
    if (tab.path && !frames.querySelector(`iframe[data-tab="${tab.id}"]`))
      frames.append(
        el("iframe", {
          "data-tab": tab.id,
          title: `Eva ${tab.label}`,
          src: tab.path,
          // Navigation inside the page changes what the dock is "About".
          onload: () => renderDock(),
        }),
      );
    for (const frame of frames.querySelectorAll("iframe"))
      frame.hidden = frame.dataset.tab !== tab.id;
    if (!fromHash) replaceHash(`#${tab.id}`);
    announceTab();
    renderTabs();
    renderDock();
  }

  // Deep links (docs/eva/WORKSPACE.md, "Deep links to a tab"). Omnigent frames
  // this page at /eva/:sessionId and forwards its own #<tab> here as our hash.
  // Telling it which tab is open keeps its address bar on the same tab, so a
  // copied link reopens it. Only the tab id goes up, never a path.
  function announceTab() {
    if (window.parent === window) return;
    try {
      window.parent.postMessage(
        { type: "eva.tab", tab: tab.id },
        location.origin,
      );
    } catch {
      // A parent on another origin gets nothing, which is the point.
    }
  }

  /** Open an outreach app link in its own tab here, with Eva docked beside it. */
  function openInWorkspace(event, href) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button)
      return;
    event.preventDefault();
    const target =
      TABS.filter((t) => t.path && href.startsWith(t.path)).sort(
        (a, b) => b.path.length - a.path.length,
      )[0] || TABS.find((t) => t.id === "leads");
    showTab(target);
    const frame = $("frames").querySelector(`iframe[data-tab="${target.id}"]`);
    if (frame) frame.src = href;
  }

  /** Take an open tab's page back to where the tab starts, e.g. a lead to the list. */
  function restartTab(t) {
    const frame = t.path && currentFrame();
    if (frame) frame.src = t.path;
  }

  function replaceHash(hash) {
    try {
      window.history.replaceState(null, "", hash);
    } catch {
      location.hash = hash;
    }
  }

  function setDockCollapsed(value) {
    dockCollapsed = value;
    try {
      localStorage.setItem(DOCK_KEY, value ? "1" : "0");
    } catch {
      // Storage can be unavailable; the dock still works for this visit.
    }
    renderDock();
  }

  async function refresh() {
    if (busy) return;
    setBusy(true);
    notice("");
    addMessage("system", "Refreshing: Eva is reading the pool and your leads.");
    await markExisting();
    const stopWatching = watchTurn(new Set());
    try {
      // Refresh answers with the state itself, as Iris's does.
      state = await api("refresh", {});
      await stopWatching();
      addMessage("system", "Refreshed: Eva has read the pool and your leads.");
      render();
    } catch (error) {
      await stopWatching();
      if (error.status !== 409) {
        addMessage("error", plainError(error), () => void refresh());
        return;
      }
      // Her turn ran but read nothing. Say so, and keep showing what she read
      // before, labelled with when, rather than an empty or broken panel.
      addMessage("system", error.message);
      state = (await loadState().catch(() => null)) || state;
      render();
      notice(
        state && state.refreshed_at
          ? `That refresh read nothing new. This is what Eva read ${shortWhen(state.refreshed_at)}.`
          : "That refresh read nothing, so there is nothing to show yet. Ask Eva in the chat what went wrong.",
        false,
      );
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------------------- wiring

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
  $("cancel").addEventListener(
    "click",
    () => void api("cancel", {}).catch(() => {}),
  );
  window.addEventListener("hashchange", () => {
    showTab(tabFromHash(), true);
    renderView();
    renderDetail();
  });
  for (const button of document.querySelectorAll("#views button"))
    button.addEventListener("click", () => {
      view = button.dataset.view;
      renderView();
      renderDetail();
    });
  // A management page asks Eva for something, e.g. its "Draft with Eva" button:
  //   window.parent.postMessage({type: "eva.ask", intent: "draft", lead_id,
  //     lead_name}, location.origin)
  // A message listener is an injection surface, so this accepts a message only
  // from this origin AND from one of this workspace's own management frames,
  // and only an intent listed below with well-formed fields. Each intent turns
  // into a fixed sentence; nothing the page sends reaches Eva except a lead id
  // checked as a UUID, a lead name cleaned to one bounded line, and a month
  // checked as YYYY-MM.
  const LEAD_ASK = (verb) => (data) => {
    if (typeof data.lead_id !== "string" || !UUID.test(data.lead_id))
      return null;
    const id = data.lead_id.toLowerCase();
    const name = cleanName(data.lead_name) || "this lead";
    return {
      text: `${verb(name)} (id ${id}).`,
      later: verb(name).replace(/^./, (c) => c.toLowerCase()),
    };
  };
  const INTENTS = {
    // "Draft with Eva" on a lead page.
    draft: LEAD_ASK((name) => `Draft a first touch for ${name}`),
    // "Qualify with Eva" on a lead page.
    qualify: LEAD_ASK((name) => `Qualify ${name}`),
    // The LinkedIn page's refresh button.
    refresh_linkedin: () => ({
      text: "Refresh the LinkedIn post stats: read the latest analytics for our recent posts and record them.",
      later: "refresh the LinkedIn post stats",
    }),
    // The scoreboard's "Ask Eva for a read" for the month on screen.
    read_scoreboard: (data) => {
      if (typeof data.month !== "string" || !MONTH.test(data.month))
        return null;
      return {
        text: `Give me a read on the outreach scoreboard for ${data.month}: what changed, what's working, what needs attention.`,
        later: `give you a read on the scoreboard for ${data.month}`,
      };
    },
  };
  // Omnigent's address bar changed to name a tab (back, forward, an edited
  // hash). Accepted only from the page framing this one, on this origin, and
  // only as a tab id from TABS: the frame opens that tab's own fixed path.
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "eva.host.tab") return;
    if (event.origin !== location.origin) return;
    if (window.parent === window || event.source !== window.parent) return;
    const next = TABS.find((t) => t.id === data.tab);
    if (next && next.id !== tab.id) showTab(next);
  });
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "eva.ask") return;
    if (event.origin !== location.origin || !event.source) return;
    const frames = [...document.querySelectorAll("#frames iframe")];
    if (!frames.some((frame) => frame.contentWindow === event.source)) return;
    if (!Object.hasOwn(INTENTS, data.intent)) return;
    const asked = INTENTS[data.intent](data);
    if (!asked) return;
    setDockCollapsed(false);
    if (busy) {
      addMessage(
        "system",
        `Eva is busy with another turn. Ask her to ${asked.later} when she finishes.`,
      );
      return;
    }
    // The ask says what it is about, so no page note is added to it.
    void ask(asked.text, false);
  });

  $("outreach-link").addEventListener("click", (event) =>
    openInWorkspace(event, "/eva/app/leads"),
  );
  $("collapse").addEventListener("click", () => setDockCollapsed(true));
  $("dock-rail").addEventListener("click", () => setDockCollapsed(false));
  $("context-remove").addEventListener("click", () => {
    includeContext = false;
    renderDock();
  });
  showTab(tabFromHash(), true);

  (async () => {
    try {
      const answered = await loadHistory();
      const readiness = await api("readiness");
      if (!answered && !readiness.turn_completed_here)
        waitingLine = addMessage(
          "system",
          "Eva hasn't answered in this session yet. Ask her anything to start.",
        );
      if (readiness.last_task_failed)
        notice(
          "Eva's last turn in this session failed. Open native chat to see what happened.",
          true,
        );
    } catch (error) {
      notice(error.message, true);
    }
    try {
      state = await loadState();
    } catch (error) {
      notice(error.message, true);
    }
    render();
  })();
})();
