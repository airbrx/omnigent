// Tally's workspace. Framed by Omnigent at /tally/:sessionId and served per
// session at /v1/tally/sessions/{id}/ui/. Everything shown comes from api/state,
// which is built from Tally's own recorded tool results; nothing here invents
// data, and a value that was not read or not reported shows as a dash, never 0.
//
// Rendering uses DOM nodes and textContent only. Portal data is data and must
// never be parsed as markup.
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
    if (event.data && typeof event.data.tallyHostTheme === "string")
      applyTheme(event.data.tallyHostTheme);
  });

  // The workspace's tabs, in order. `path` is the portal page framed for the
  // tab, through Omnigent's same-origin gateway proxy at /gateway/app/. Chat has
  // no path; it is Tally's own view, and her chat is docked beside every tab.
  const TABS = [
    { id: "chat", label: "Chat" },
    { id: "analytics", label: "Analytics", path: "/gateway/app/#overview" },
    { id: "agents", label: "Agent management", path: "/gateway/app/#agents" },
    { id: "board", label: "Sprint board", path: "/gateway/app/#board" },
  ];
  const VIEWS = ["overview", "policies", "decisions", "health"];
  const POLICY_AGENTS = ["iris", "eva"];
  const DOCK_KEY = "tally.dockCollapsed";
  // What a tile shows for a value Tally has not read. An en dash, never an em dash.
  const UNREAD = "–";

  const $ = (id) => document.getElementById(id);
  let state = null;
  let view = "overview";
  let tab = TABS[0];
  let includeContext = true;
  let dockCollapsed = false;
  try {
    dockCollapsed = localStorage.getItem(DOCK_KEY) === "1";
  } catch {
    dockCollapsed = false;
  }
  let busy = false;
  const history = [];
  const SESSION_ID = decodeURIComponent(
    (/\/tally\/sessions\/([^/]+)\/ui\//.exec(location.pathname) || [])[1] || "",
  );
  const POLL_MS = Number(document.body.dataset.pollMs) || 1500;
  // A refresh that finds her runner still starting (after a deploy or idle it
  // takes about two minutes to reconnect) waits 1, 2, 3... steps, at most 6,
  // for up to 36 steps in all: 5s, 10s, 15s... for about three minutes.
  const RETRY_MS = Number(document.body.dataset.retryMs) || 5000;
  let retryCancelled = false;

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

  /** A freshness value as the portal gave it: epoch seconds, ms or a date string. */
  function freshnessText(value) {
    if (typeof value === "number") {
      if (value > 1e11) return shortWhen(value / 1000);
      if (value > 1e9) return shortWhen(value);
      return String(value);
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : shortWhen(date.getTime() / 1000);
  }

  function notice(text, isError) {
    const box = $("notice");
    box.hidden = !text;
    box.className = isError ? "notice error" : "notice";
    box.textContent = text || "";
  }

  const humanize = (key) => String(key).replace(/_/g, " ");

  /** Whether a field name says it is measured or estimated. Nothing is assumed. */
  function basisBadge(key) {
    const k = String(key).toLowerCase();
    // api_equivalent_usd is a published-rate equivalent, not a charge;
    // actual_charges_usd is the only measured charge.
    if (/estimat|advertis|project|forecast|potential|api_equivalent/.test(k))
      return el("span", { class: "badge warn" }, "estimated");
    if (/measured|actual_charges|observed/.test(k))
      return el("span", { class: "badge ok" }, "measured");
    return null;
  }

  function unavailable() {
    return el("span", { class: "muted" }, "unavailable");
  }

  /** A short label for one list entry, from the fields a record usually has. */
  function entryLabel(entry) {
    if (entry === null || entry === undefined) return "unavailable";
    if (typeof entry !== "object") return String(entry);
    return (
      entry.title ||
      entry.name ||
      entry.label ||
      entry.summary ||
      entry.id ||
      "(untitled)"
    );
  }

  function entryNote(entry) {
    if (!entry || typeof entry !== "object") return "";
    return [entry.status || entry.state, entry.owner || entry.assignee]
      .filter((v) => typeof v === "string" && v)
      .join(" · ");
  }

  /** Portal data as a readable panel. Nulls read "unavailable", never zero. */
  function facts(data, depth = 0) {
    if (data === null || data === undefined) return unavailable();
    if (typeof data !== "object") return el("p", {}, String(data));
    if (Array.isArray(data)) {
      if (!data.length) return el("p", { class: "muted small" }, "None.");
      const shown = data.slice(0, 20);
      return el(
        "div",
        {},
        el(
          "ul",
          { class: "rules" },
          shown.map((entry) =>
            el(
              "li",
              {},
              el("span", {}, String(entryLabel(entry))),
              el("span", { class: "muted small" }, entryNote(entry)),
            ),
          ),
        ),
        data.length > shown.length
          ? el(
              "p",
              { class: "muted small" },
              `${shown.length} of ${data.length} shown.`,
            )
          : null,
      );
    }
    const rows = [];
    const nested = [];
    for (const [key, value] of Object.entries(data)) {
      if (value !== null && typeof value === "object")
        nested.push([key, value]);
      else rows.push([key, value]);
    }
    const out = [];
    if (rows.length)
      out.push(
        el(
          "dl",
          {},
          rows.map(([key, value]) => [
            el("dt", {}, humanize(key), " ", basisBadge(key)),
            el(
              "dd",
              {},
              value === null || value === undefined
                ? unavailable()
                : String(value),
            ),
          ]),
        ),
      );
    for (const [key, value] of nested) {
      out.push(
        el(
          "div",
          { class: "section-title", style: "margin-top:12px" },
          humanize(key),
          " ",
          basisBadge(key),
          Array.isArray(value) ? ` · ${value.length}` : "",
        ),
      );
      out.push(
        depth < 2
          ? facts(value, depth + 1)
          : el("p", { class: "muted small" }, "Ask Tally for the detail."),
      );
    }
    return out.length
      ? el("div", {}, out)
      : el("p", { class: "muted small" }, "Empty.");
  }

  function readCard(title, read, missing) {
    return el(
      "div",
      { class: "card detail", style: "margin-bottom:12px" },
      el("h2", {}, title),
      el(
        "div",
        { class: "sub" },
        read && read.at
          ? `Read ${shortWhen(read.at)}`
          : "Not read in this session",
      ),
      read ? facts(read.data) : el("p", { class: "muted small" }, missing),
    );
  }

  // ---------------------------------------------------------------- views

  function renderView() {
    const container = $("view");
    container.replaceChildren();
    for (const button of document.querySelectorAll("#views button"))
      button.classList.toggle("active", button.dataset.view === view);
    const unread = !state || state.empty;
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
            "Tally hasn't read the portal yet",
          ),
          el(
            "p",
            {},
            "This view shows only what Tally has read in this session. The Analytics, Agent management and Sprint board tabs show the portal itself and are always current.",
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
              "Have Tally read it now",
            ),
          ),
        ),
      );
      return;
    }
    if (view === "policies") {
      for (const agent of POLICY_AGENTS)
        container.append(
          readCard(
            `${agent.charAt(0).toUpperCase()}${agent.slice(1)} policy`,
            state.policies && state.policies[agent],
            `Tally has not read ${agent}'s policy in this session.`,
          ),
        );
      const others = Object.keys(state.policies || {}).filter(
        (a) => !POLICY_AGENTS.includes(a),
      );
      for (const agent of others)
        container.append(
          readCard(`${agent} policy`, state.policies[agent], ""),
        );
    } else if (view === "decisions") {
      const k = state.kpis || {};
      container.append(
        el(
          "div",
          { class: "card detail", style: "margin-bottom:12px" },
          el("h2", {}, "Decisions and blockers"),
          el(
            "dl",
            {},
            [
              ["Decisions waiting", k.decisions_waiting],
              ["Blockers", k.blockers],
            ].map(([label, kpi]) => [
              el("dt", {}, label),
              el(
                "dd",
                {},
                kpi && kpi.value !== null && kpi.value !== undefined
                  ? `${kpi.value} (${kpi.source})`
                  : el(
                      "span",
                      { class: "muted" },
                      `unavailable: ${(kpi && kpi.reason) || "not read yet"}`,
                    ),
              ),
            ]),
          ),
        ),
        itemList(
          "Decisions needed from Abram",
          state.decisions,
          k.decisions_waiting,
        ),
        itemList("Blockers", state.blockers, k.blockers),
      );
    } else if (view === "health") {
      container.append(
        readCard(
          "Portal health",
          state.health,
          "Tally has not read the portal's health in this session.",
        ),
      );
    } else {
      const agentsTable = agentsCard(state.analytics);
      if (agentsTable) container.append(agentsTable);
      container.append(
        readCard(
          "Analytics overview",
          state.analytics,
          "Tally has not read the analytics overview in this session.",
        ),
      );
    }
  }

  /** One board section as a list, or unavailable with the reason. Never "0" for missing. */
  function itemList(title, items, kpi) {
    return el(
      "div",
      { class: "card detail", style: "margin-bottom:12px" },
      el("h2", {}, title),
      el(
        "div",
        { class: "sub" },
        kpi && kpi.at ? `From the sprint board, read ${shortWhen(kpi.at)}` : "",
      ),
      Array.isArray(items)
        ? items.length
          ? el(
              "ul",
              { class: "rules" },
              items.map((item) =>
                el("li", {}, el("span", {}, item), el("span")),
              ),
            )
          : el("p", { class: "muted small" }, "None on the board.")
        : el(
            "p",
            { class: "muted small" },
            `Unavailable: ${(kpi && kpi.reason) || "not read yet"}.`,
          ),
    );
  }

  function money(value) {
    return typeof value === "number"
      ? `$${value.toFixed(value < 1 ? 4 : 2)}`
      : null;
  }

  /** Per-agent usage from the analytics overview. null reads unavailable. */
  function agentsCard(read) {
    const agents = read && read.data && read.data.agents;
    if (!Array.isArray(agents)) return null;
    const cell = (value) =>
      value === null || value === undefined ? unavailable() : String(value);
    return el(
      "div",
      { class: "card", style: "margin-bottom:12px" },
      el("div", { class: "section-title" }, `Agents · ${agents.length}`),
      el(
        "table",
        {},
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", {}, "Agent"),
            el("th", {}, "Runs"),
            el("th", {}, "Tokens in / out"),
            el("th", {}, "API-equivalent ", basisBadge("api_equivalent_usd")),
            el("th", {}, "Actual charges ", basisBadge("actual_charges_usd")),
          ),
        ),
        el(
          "tbody",
          {},
          agents.map((a) =>
            el(
              "tr",
              {},
              el(
                "td",
                {},
                el("strong", {}, String(a.label || a.agent_id || "unnamed")),
              ),
              el("td", { class: "mono" }, cell(a.run_count)),
              el(
                "td",
                { class: "mono" },
                a.input_tokens === null || a.input_tokens === undefined
                  ? unavailable()
                  : `${a.input_tokens} / ${a.output_tokens ?? "unavailable"}`,
              ),
              el(
                "td",
                { class: "mono" },
                money(a.api_equivalent_usd) || unavailable(),
              ),
              el(
                "td",
                { class: "mono" },
                money(a.actual_charges_usd) || unavailable(),
              ),
            ),
          ),
        ),
      ),
    );
  }

  function renderKpis() {
    const k = (state && state.kpis) || {};
    const tiles = [
      ["kpi-decisions", k.decisions_waiting, "on the sprint board"],
      ["kpi-blockers", k.blockers, "open on the board"],
      ["kpi-agents", k.agents_tracked, "in analytics"],
      ["kpi-freshness", k.data_freshness, "analytics updated_at"],
      [
        "kpi-spend",
        k.api_equivalent_usd,
        "estimated at published rates, not a charge",
      ],
    ];
    for (const [id, kpi, note] of tiles) {
      const known = kpi && kpi.value !== null && kpi.value !== undefined;
      $(id).textContent = known
        ? id === "kpi-freshness"
          ? freshnessText(kpi.value)
          : id === "kpi-spend"
            ? money(kpi.value)
            : String(kpi.value)
        : UNREAD;
      const tile = $(id).parentElement;
      tile.classList.toggle("unread", !known);
      tile.querySelector(".note").textContent = known
        ? note
        : (kpi && kpi.reason) || "not read yet";
    }
    const policies = Object.keys((state && state.policies) || {}).length;
    $("nav-agents").textContent = state ? String(policies) : UNREAD;
    const blockers = k.blockers;
    $("nav-board").textContent =
      blockers && blockers.value !== null && blockers.value !== undefined
        ? String(blockers.value)
        : UNREAD;
    $("freshness").textContent =
      state && state.refreshed_at
        ? `Tally read the portal ${shortWhen(state.refreshed_at)}${state.stale ? ", may be out of date" : ""}`
        : "Tally has not read the portal yet";
  }

  function render() {
    renderKpis();
    renderView();
    const errors = (state && state.errors) || [];
    if (errors.length)
      notice(
        `Tally's last failed read: ${errors[errors.length - 1].tool}: ${errors[errors.length - 1].message}`,
        true,
      );
  }

  // ---------------------------------------------------------------- chat

  // Tally writes a little markdown. Her answers render a safe subset of it:
  // bold, italics, inline code, bullet and numbered lists, and line breaks.
  // Everything is built as DOM nodes with text content, so any HTML in her
  // text shows as the characters it is and never becomes markup.
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

  /** Tally's text as nodes: paragraphs, lists and inline marks, never HTML. */
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

  /** What each of Tally's answer lines says, as she wrote it. */
  const rawText = new WeakMap();

  function setTallyText(line, text) {
    rawText.set(line, text);
    line.replaceChildren(...markdown(text));
  }

  function addMessage(kind, text, retry) {
    const list = $("messages");
    if (kind === "tally" && waitingLine) {
      waitingLine.remove();
      waitingLine = null;
    }
    const line = list.appendChild(
      el(
        "li",
        { class: kind },
        kind === "tally" ? null : text,
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
    if (kind === "tally") setTallyText(line, text);
    list.scrollTop = list.scrollHeight;
    return line;
  }

  // ------------------------------------------------ the session's own record
  //
  // The adapter's chat call returns only when Tally's turn is over. While it
  // runs, the dock reads the native session's items and shows her interim
  // messages and what she is doing. The same read rebuilds the chat on load.

  const handled = new Set();
  const answerLines = new Map();
  let progressLine = null;
  let waitingLine = null;

  /** What each of Tally's tools is doing, in plain words, never its name. */
  const DOING = {
    get_analytics_overview: "Reading the analytics overview",
    get_agent_policy: "Reading an agent's policy",
    get_sprint_board: "Reading the sprint board",
    get_health: "Checking the portal's health",
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

  /** A person's own message as they wrote it: without the page note the dock adds. */
  function userText(text) {
    return text.replace(/\n\n\(I am looking at [\s\S]* in the portal\.\)$/, "");
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
        if (rawText.get(line) !== text) setTallyText(line, text);
        if (shown) shown.add(text.trim());
        return;
      }
      if (handled.has(item.id)) return;
      handled.add(item.id);
      answerLines.set(item.id, addMessage("tally", text));
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
        addMessage("system", "You had Tally read the portal.");
      else if (text.trim()) addMessage("user", userText(text));
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
    showProgress("Tally is working…");
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

  /** An adapter failure in words a person can act on. */
  function plainError(error) {
    const raw = (error && error.message) || "";
    const status = error && error.status;
    if (
      /native session operation failed/i.test(raw) ||
      status === 502 ||
      status === 503
    )
      return "Tally could not be reached just now. Her runner may be offline or restarting.";
    if (status === 504 || /timed out/i.test(raw))
      return "Tally took too long, so the turn was stopped.";
    if (status === 401)
      return "Your Omnigent sign-in has expired. Reload the page to sign in again.";
    if (!status)
      return "The workspace could not reach Omnigent. Check your connection.";
    if (status >= 500) return "Something went wrong on the Omnigent side.";
    // The adapter's own 4xx details are written for people.
    return raw;
  }

  /**
   * Whether a failure is her runner not being up yet, which passes on its own.
   * A 504 or any 4xx is an answer, not an absence, and is never retried.
   */
  function runnerStarting(error) {
    const status = error && error.status;
    const raw = (error && error.message) || "";
    return (
      status === 502 ||
      status === 503 ||
      (status === 500 && /native session operation failed/i.test(raw))
    );
  }

  /**
   * Refresh, waiting out a runner that is still starting. Only refresh does
   * this: a failed chat turn may already have been posted, and resending it
   * would ask Tally twice, while a second refresh only reads the portal again.
   */
  async function refreshWhileStarting() {
    let waited = 0;
    for (let attempt = 1; ; attempt++) {
      try {
        return await api("refresh", {});
      } catch (error) {
        const delay = Math.min(attempt, 6) * RETRY_MS;
        if (
          !runnerStarting(error) ||
          retryCancelled ||
          waited + delay > 36 * RETRY_MS
        )
          throw error;
        showProgress("Tally is starting up… retrying");
        await new Promise((resolve) => {
          setTimeout(resolve, delay);
        });
        waited += delay;
        if (retryCancelled) throw error;
      }
    }
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
    const page = tab.path && includeContext && withContext ? tab : null;
    history.push({
      role: "user",
      content: page
        ? `${text}\n\n(I am looking at the ${page.label} page, ${framedPath()}, in the portal.)`
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
      if (!shown.has((reply.text || "").trim()))
        addMessage("tally", reply.text);
      // A turn can change what Tally has read; the answer does not carry state.
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

  function currentFrame() {
    return document.querySelector(`#frames iframe[data-tab="${tab.id}"]`);
  }

  /** The framed page's current path and hash, so navigation inside it is picked up. */
  function framedPath() {
    const frame = currentFrame();
    try {
      const where =
        frame && frame.contentWindow && frame.contentWindow.location;
      if (where && where.pathname && where.pathname !== "blank")
        return `${where.pathname}${where.hash}`;
    } catch {
      // A page that left this origin cannot be read; its src still helps.
    }
    const src = frame && frame.getAttribute("src");
    return src && src.startsWith("/") ? src : tab.path;
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
    // Freshness and refresh are about Tally's read, which only Chat shows.
    $("actions").hidden = docked;
    $("frames").hidden = !docked;
    $("chat").hidden = collapsed;
    $("dock-rail").hidden = !collapsed;
    $("collapse").hidden = !docked;
    $("context").hidden = !docked || !includeContext;
    $("context-label").textContent = docked ? `About ${tab.label}` : "";
    $("input").placeholder = docked
      ? `Ask Tally about ${tab.label}. Enter sends, Shift+Enter adds a line.`
      : "Ask Tally about agents, costs, coverage, decisions or blockers. Enter sends, Shift+Enter adds a line.";
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
          title: `Tally ${tab.label}`,
          src: tab.path,
          onload: () => renderDock(),
        }),
      );
    for (const frame of frames.querySelectorAll("iframe"))
      frame.hidden = frame.dataset.tab !== tab.id;
    if (!fromHash) replaceHash(`#${tab.id}`);
    renderTabs();
    renderDock();
  }

  /** Take an open tab's page back to where the tab starts. */
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
    addMessage("system", "Refreshing: Tally is reading the portal.");
    await markExisting();
    const stopWatching = watchTurn(new Set());
    retryCancelled = false;
    try {
      // Refresh answers with the state itself, as Iris's and Eva's do.
      state = await refreshWhileStarting();
      await stopWatching();
      addMessage("system", "Refreshed: Tally has read the portal.");
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
          ? `That refresh read nothing new. This is what Tally read ${shortWhen(state.refreshed_at)}.`
          : "That refresh read nothing, so there is nothing to show yet. Ask Tally in the chat what went wrong.",
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
  $("cancel").addEventListener("click", () => {
    retryCancelled = true;
    void api("cancel", {}).catch(() => {});
  });
  window.addEventListener("hashchange", () => {
    showTab(tabFromHash(), true);
    renderView();
  });
  for (const button of document.querySelectorAll("#views button"))
    button.addEventListener("click", () => {
      view = button.dataset.view;
      renderView();
    });

  $("portal-link").addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button)
      return;
    event.preventDefault();
    showTab(TABS.find((t) => t.id === "analytics"));
  });
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
          "Tally hasn't answered in this session yet. Ask her anything to start.",
        );
      if (readiness.last_task_failed)
        notice(
          "Tally's last turn in this session failed. Open native chat to see what happened.",
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
