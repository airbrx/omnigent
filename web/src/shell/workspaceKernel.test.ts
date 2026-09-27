// The airbrx workspace kernel (`omnigent/airbrx/workspace/ui/`). It is plain
// browser scripts, not part of the web bundle, so each file is read from disk
// and run against jsdom in the order a page loads it, the same way
// evaWorkspaceApp.test.ts runs Eva's app.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KERNEL = join(dirname(fileURLToPath(import.meta.url)), "../../../omnigent/airbrx/workspace/ui");
const FILES = [
  "dom.js",
  "theme.js",
  "api.js",
  "markdown.js",
  "transcript.js",
  "stream.js",
  "dock.js",
  "tabs.js",
  "context.js",
];
const SOURCES = FILES.map((file) => readFileSync(join(KERNEL, file), "utf8"));

// A loose view of window.AirbrxWorkspace; the kernel is untyped browser script.
/* eslint-disable @typescript-eslint/no-explicit-any */
type AW = any;
let AW: AW;

const listeners: [string, EventListener][] = [];
const realAdd = window.addEventListener.bind(window);

function load(): AW {
  delete (window as any).AirbrxWorkspace;
  // Indirect eval: each file is an IIFE written for a browser <script>.
  for (const source of SOURCES) (0, eval)(source);
  return (window as any).AirbrxWorkspace;
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/v1/iris/sessions/s1/ui/");
  document.body.innerHTML = "";
  document.body.className = "";
  window.addEventListener = ((type: string, listener: EventListener, options?: unknown) => {
    listeners.push([type, listener]);
    realAdd(type, listener, options as never);
  }) as never;
  AW = load();
});

afterEach(() => {
  for (const [type, listener] of listeners.splice(0)) window.removeEventListener(type, listener);
  window.addEventListener = realAdd as never;
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

function holder(fragment: Node) {
  const div = document.createElement("div");
  div.append(fragment);
  return div;
}

/** Every element a render produced, and every attribute on them. */
function shape(root: Element) {
  const elements = [...root.querySelectorAll("*")];
  return {
    tags: [...new Set(elements.map((e) => e.tagName.toLowerCase()))],
    attrs: elements.flatMap((e) => [...e.attributes].map((a) => `${e.tagName.toLowerCase()}[${a.name}]`)),
  };
}

const SAFE_TAGS = ["p", "br", "strong", "em", "code", "ul", "ol", "li"];

// ------------------------------------------------------------ markdown safety

describe("markdown", () => {
  it("renders the subset: bold, italics, code, lists, headings as bold paragraphs", () => {
    const root = holder(
      AW.markdown("# Title\n**bold** and *em* and `code`\nsecond line\n\n- one\n- two\n\n3. three\n4. four"),
    );
    expect(root.querySelector("p > strong")?.textContent).toBe("Title");
    expect(root.querySelectorAll("strong")[1].textContent).toBe("bold");
    expect(root.querySelector("em")?.textContent).toBe("em");
    expect(root.querySelector("code")?.textContent).toBe("code");
    expect(root.querySelector("br")).not.toBeNull();
    expect([...root.querySelectorAll("ul li")].map((li) => li.textContent)).toEqual(["one", "two"]);
    expect(root.querySelector("ol")?.getAttribute("start")).toBe("3");
    expect(AW.markdown("x")).toBeInstanceOf(DocumentFragment);
  });

  it.each([
    ["a script tag", "<script>window.__pwned = 1</script>"],
    ["an image with a handler", '<img src="x" onerror="window.__pwned = 1">'],
    ["an iframe", '<iframe src="javascript:window.__pwned=1"></iframe>'],
    ["markup inside bold", '**<b onclick="window.__pwned=1">hi</b>**'],
    ["markup inside a list", "- <a href=\"javascript:window.__pwned=1\">x</a>"],
    ["markup inside a heading", "## <svg onload=\"window.__pwned=1\"></svg>"],
  ])("shows %s as the characters it is", (_name, text) => {
    const root = holder(AW.markdown(text));
    const { tags, attrs } = shape(root);
    for (const tag of tags) expect(SAFE_TAGS).toContain(tag);
    expect(attrs).toEqual([]);
    expect(root.textContent).toContain("<");
    expect((window as any).__pwned).toBeUndefined();
  });

  it.each([
    ["a markdown link", "[click me](javascript:alert(1))"],
    ["an autolink", "<javascript:alert(1)>"],
    ["an image", "![x](javascript:alert(1))"],
    ["a bare scheme", "javascript:alert(1)"],
    ["a data URL link", "[x](data:text/html,<script>alert(1)</script>)"],
  ])("never makes %s into a link or image", (_name, text) => {
    const root = holder(AW.markdown(text));
    expect(root.querySelector("a, img, [href], [src]")).toBeNull();
    expect(root.textContent).toContain(":");
  });

  it("cannot inject an attribute from inside code, bold or a list number", () => {
    const root = holder(
      AW.markdown('`" onmouseover="alert(1)`\n**" style="x**\n7. "><x onclick=1>\n12) "start="99'),
    );
    const { tags, attrs } = shape(root);
    for (const tag of tags) expect(SAFE_TAGS).toContain(tag);
    // The only attribute the subset ever sets is a list's numeric start.
    expect(attrs).toEqual(["ol[start]"]);
    expect(root.querySelector("ol")?.getAttribute("start")).toBe("7");
  });

  it("inline returns nodes only", () => {
    const nodes = AW.inline("a **b** <i>c</i>");
    for (const node of nodes) expect(node).toBeInstanceOf(Node);
    expect(holder(nodes.reduce((f: DocumentFragment, n: Node) => (f.append(n), f), document.createDocumentFragment())).querySelector("i")).toBeNull();
  });

  it("el drops script URLs and string handlers", () => {
    const a = AW.el("a", { href: " javascript:alert(1)", onclick: "alert(1)" }, "x");
    expect(a.hasAttribute("href")).toBe(false);
    expect(a.hasAttribute("onclick")).toBe(false);
    expect(AW.el("a", { href: "/v1/sessions/s1/resources/files" }).getAttribute("href")).toBe(
      "/v1/sessions/s1/resources/files",
    );
    expect(AW.el("p", {}, "<b>x</b>").querySelector("b")).toBeNull();
  });
});

// ------------------------------------------------------------ transcript

function transcriptFixture(agentName = "Iris") {
  const list = document.createElement("ol");
  list.id = "messages";
  document.body.append(list);
  return { list, transcript: AW.createTranscript({ list, agentName }) };
}

describe("transcript", () => {
  it("renders agent lines as markdown and every other kind as plain text", () => {
    const { list, transcript } = transcriptFixture();
    transcript.add("agent", "**yes** <script>x</script>");
    transcript.add("user", "**not bold** <b>x</b>");
    transcript.add("error", "<img src=x onerror=alert(1)>");
    const [agent, user, error] = [...list.children];
    expect(agent.className).toBe("agent");
    expect(agent.querySelector("strong")?.textContent).toBe("yes");
    expect(agent.querySelector("script")).toBeNull();
    expect(user.querySelector("strong, b")).toBeNull();
    expect(user.textContent).toBe("**not bold** <b>x</b>");
    expect(error.querySelector("img")).toBeNull();
  });

  it("keeps one progress line at the bottom and a retry chip calls back", () => {
    const { list, transcript } = transcriptFixture();
    transcript.progress("Reading…");
    transcript.add("system", "note");
    const retry = vi.fn();
    transcript.add("error", "failed", retry);
    expect(list.lastElementChild).toHaveAttribute("role", "status");
    expect(list.querySelectorAll("[role=status]")).toHaveLength(1);
    fireEvent.click(list.querySelector("button.retry")!);
    expect(retry).toHaveBeenCalledOnce();
    transcript.progress(null);
    expect(list.querySelector("[role=status]")).toBeNull();
  });

  it("the waiting line goes when the agent first answers", () => {
    const { list, transcript } = transcriptFixture();
    transcript.waiting("Iris hasn't answered yet.");
    transcript.add("agent", "Hello");
    expect(list.textContent).not.toContain("hasn't answered");
  });
});

// ------------------------------------------------------------ stream

type Item = Record<string, unknown>;
const assistant = (id: string, text: string): Item => ({
  id,
  type: "message",
  role: "assistant",
  content: [{ type: "output_text", text }],
});
const user = (id: string, text: string): Item => ({
  id,
  type: "message",
  role: "user",
  content: [{ type: "input_text", text }],
});

let items: Item[] = [];
let hasMore = false;
let itemsStatus = 200;
let fetchMock: ReturnType<typeof vi.fn>;

function serveItems() {
  fetchMock = vi.fn(async (input: string) => {
    if (!String(input).startsWith("/v1/sessions/s1/items")) throw new Error(`unexpected fetch ${input}`);
    if (itemsStatus !== 200) return Response.json({ detail: "no" }, { status: itemsStatus });
    // The API answers newest first (order=desc).
    return Response.json({ data: items.slice().reverse(), has_more: hasMore });
  });
  window.fetch = fetchMock as never;
}

const itemReads = () => fetchMock.mock.calls.filter(([url]) => String(url).includes("/items")).length;

const REFRESH = "Call iris_overview and iris_audit for the selected tenant.";

function streamFixture() {
  const { list, transcript } = transcriptFixture();
  const stream = AW.createStream({
    sessionId: "s1",
    pollMs: 10,
    doing: { iris_overview: "Reading the tenant's traffic", ToolSearch: "Getting her tools ready" },
    recognise: (text: string) => (text.startsWith(REFRESH) ? "You had Iris collect a fresh overview." : null),
    stripUser: (text: string) => text.replace(/\n\n\(context\)$/, ""),
    transcript,
  });
  return { list, stream, transcript };
}

const lines = (list: HTMLElement) => [...list.children].map((li) => [li.className, li.textContent]);

describe("stream", () => {
  beforeEach(() => {
    items = [];
    hasMore = false;
    itemsStatus = 200;
    serveItems();
  });

  it("reads the session's items same-origin, newest page, oldest first", async () => {
    items = [assistant("a1", "one"), assistant("a2", "two")];
    const { stream } = streamFixture();
    const page = await stream.sessionItems(50);
    expect(fetchMock).toHaveBeenCalledWith("/v1/sessions/s1/items?order=desc&limit=50", {
      credentials: "same-origin",
    });
    expect(page.items.map((i: Item) => i.id)).toEqual(["a1", "a2"]);
    expect(AW.sessionIdFromPath("/v1/iris/sessions/abc%2Fd/ui/")).toBe("abc/d");
  });

  it("loadHistory rebuilds the transcript: both sides, recognised lines, no tool traffic", async () => {
    hasMore = true;
    items = [
      user("u0", `${REFRESH} Then summarise.`),
      assistant("a0", "Collected."),
      user("u1", "How is the **cache**?\n\n(context)"),
      { id: "f1", type: "function_call", name: "iris__iris_overview", arguments: "{}" },
      { id: "o1", type: "function_call_output", output: "{}" },
      assistant("a1", "Hit rate is **80%**."),
    ];
    const { list, stream } = streamFixture();
    await expect(stream.loadHistory()).resolves.toBe(true);
    expect(lines(list)).toEqual([
      ["system", "Earlier messages are in native chat."],
      ["system", "You had Iris collect a fresh overview."],
      ["agent", "Collected."],
      ["user", "How is the **cache**?"],
      ["agent", "Hit rate is 80%."],
    ]);
    expect(list.querySelector("li.user strong")).toBeNull();
    expect(list.children[4].querySelector("strong")?.textContent).toBe("80%");
    // History never shows progress: that is for a live turn.
    expect(list.querySelector("[role=status]")).toBeNull();
  });

  it("loadHistory answers false with no answer in the record, and when it cannot be read", async () => {
    items = [user("u1", "hello")];
    await expect(streamFixture().stream.loadHistory()).resolves.toBe(false);
    itemsStatus = 500;
    document.body.innerHTML = "";
    const { list, stream } = streamFixture();
    await expect(stream.loadHistory()).resolves.toBe(false);
    expect(list.children).toHaveLength(0);
  });

  /** The app's turn, as Eva runs it: mark, watch, await the answer, stop. */
  async function turn(stream: AW, transcript: AW, chat: Promise<{ text: string }>) {
    const shown = new Set<string>();
    await stream.markExisting();
    const stop = stream.watchTurn(shown);
    try {
      const reply = await chat;
      await stop();
      if (!shown.has(reply.text.trim())) transcript.add("agent", reply.text);
    } catch {
      await stop();
      transcript.add("error", "Iris did not answer.");
    }
  }

  it("watchTurn streams the turn, then stops polling when the turn completes", async () => {
    items = [user("old", "earlier"), assistant("olda", "earlier answer")];
    const { list, stream, transcript } = streamFixture();
    let answer!: (value: { text: string }) => void;
    const done = turn(stream, transcript, new Promise((resolve) => (answer = resolve)));
    await waitFor(() => expect(list.querySelector("[role=status]")?.textContent).toBe("Iris is working…"));
    items.push(user("u1", "question"), { id: "f1", type: "function_call", name: "iris__iris_overview", arguments: "{}" });
    await waitFor(() =>
      expect(list.querySelector("[role=status]")?.textContent).toBe("Reading the tenant's traffic…"),
    );
    items.push(assistant("a1", "Half"));
    await waitFor(() => expect(list.querySelector("li.agent")?.textContent).toBe("Half"));
    items[items.length - 1] = assistant("a1", "Half and whole");
    answer({ text: "Half and whole" });
    await done;
    // Grown in place, not added twice; nothing from before the turn replayed.
    expect(lines(list)).toEqual([["agent", "Half and whole"]]);
    expect(list.querySelector("[role=status]")).toBeNull();
    const reads = itemReads();
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(itemReads()).toBe(reads);
  });

  it("watchTurn stops polling when the turn fails, and clears the progress line", async () => {
    const { list, stream, transcript } = streamFixture();
    let fail!: (error: Error) => void;
    const done = turn(stream, transcript, new Promise((_resolve, reject) => (fail = reject)));
    await waitFor(() => expect(itemReads()).toBeGreaterThan(2));
    // Polls that fail are harmless; the watch keeps going until stopped.
    itemsStatus = 502;
    const before = itemReads();
    await waitFor(() => expect(itemReads()).toBeGreaterThan(before + 1));
    fail(Object.assign(new Error("HTTP 504"), { status: 504 }));
    await done;
    expect(list.querySelector("[role=status]")).toBeNull();
    expect(list.querySelector("li.agent")).toBeNull();
    expect(lines(list)).toEqual([["error", "Iris did not answer."]]);
    const reads = itemReads();
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(itemReads()).toBe(reads);
  });

  it("stop() is idempotent", async () => {
    const { stream } = streamFixture();
    const stop = stream.watchTurn(new Set());
    const first = stop();
    expect(stop()).toBe(first);
    await first;
  });
});

// ------------------------------------------------------------ the whitelist

describe("what the stream may put in the chat", () => {
  const SENTINEL = "SENTINEL-7f3c-EVIDENCE";

  const toolTraffic = (suffix: string): Item[] => [
    {
      id: `f-${suffix}`,
      type: "function_call",
      name: `mcp__secret_tool_${suffix}`,
      call_id: `c-${suffix}`,
      arguments: JSON.stringify({ tenant: SENTINEL, sql: `select '${SENTINEL}'` }),
    },
    {
      id: `o-${suffix}`,
      type: "function_call_output",
      call_id: `c-${suffix}`,
      output: JSON.stringify({ evidence: [{ data: SENTINEL }] }),
      content: [{ type: "output_text", text: SENTINEL }],
    },
    {
      id: `r-${suffix}`,
      type: "reasoning",
      summary: [{ type: "summary_text", text: SENTINEL }],
      content: [{ type: "output_text", text: SENTINEL }],
    },
    { id: `x-${suffix}`, type: "mcp_list_tools", content: [{ type: "output_text", text: SENTINEL }] },
    { id: `t-${suffix}`, type: "message", role: "tool", content: [{ type: "output_text", text: SENTINEL }] },
    {
      id: `s-${suffix}`,
      type: "message",
      role: "system",
      content: [{ type: "input_text", text: SENTINEL }],
    },
  ];

  const inDom = () => document.body.outerHTML;

  beforeEach(() => {
    items = [];
    hasMore = false;
    itemsStatus = 200;
    serveItems();
  });

  it("history never shows tool calls, tool outputs, reasoning or other items", async () => {
    items = [user("u1", "go"), ...toolTraffic("h"), assistant("a1", "Done.")];
    const { list, stream } = streamFixture();
    await stream.loadHistory();
    expect(inDom()).not.toContain(SENTINEL);
    expect(inDom()).not.toContain("secret_tool");
    expect(lines(list)).toEqual([
      ["user", "go"],
      ["agent", "Done."],
    ]);
  });

  it("a live turn shows only a label from the doing map, never the tool name or arguments", async () => {
    const { list, stream } = streamFixture();
    const stop = stream.watchTurn(new Set());
    items = toolTraffic("l");
    await waitFor(() => expect(list.querySelector("[role=status]")?.textContent).toBe("Working…"));
    expect(inDom()).not.toContain(SENTINEL);
    expect(inDom()).not.toContain("secret_tool");
    items.push({ id: "f2", type: "function_call", name: "constructor", arguments: SENTINEL });
    items.push({ id: "f3", type: "function_call", name: "iris__ToolSearch", arguments: SENTINEL });
    await waitFor(() =>
      expect(list.querySelector("[role=status]")?.textContent).toBe("Getting her tools ready…"),
    );
    await stop();
    expect(inDom()).not.toContain(SENTINEL);
    expect(list.children).toHaveLength(0);
  });

  it("a live turn does not replay the user's own message", async () => {
    const { list, stream } = streamFixture();
    const stop = stream.watchTurn(new Set());
    items = [user("u1", SENTINEL)];
    await waitFor(() => expect(itemReads()).toBeGreaterThan(1));
    await stop();
    expect(list.textContent).not.toContain(SENTINEL);
  });
});

// ------------------------------------------------------------ dock

function dockFixture() {
  document.body.innerHTML = `
    <section id="chat"></section>
    <button id="dock-rail" hidden><span>Ask Iris</span></button>
    <button id="collapse" aria-label="Collapse chat">Hide</button>`;
  return AW.createDock({
    chat: AW.$("chat"),
    rail: AW.$("dock-rail"),
    collapseButton: AW.$("collapse"),
    storageKey: "iris.dockCollapsed",
  });
}

describe("dock", () => {
  it("collapses to the rail, remembers it, and comes back", () => {
    const dock = dockFixture();
    dock.render({ docked: true });
    expect(AW.$("chat")).toBeVisible();
    fireEvent.click(AW.$("collapse"));
    expect(AW.$("chat")).not.toBeVisible();
    expect(AW.$("dock-rail")).toBeVisible();
    expect(document.body).toHaveClass("collapsed");
    expect(localStorage.getItem("iris.dockCollapsed")).toBe("1");
    fireEvent.click(AW.$("dock-rail"));
    expect(AW.$("chat")).toBeVisible();
    expect(document.body).not.toHaveClass("collapsed");
    expect(localStorage.getItem("iris.dockCollapsed")).toBe("0");
  });

  it("a collapsed dock stays collapsed on the next visit, but never on the home tab", () => {
    localStorage.setItem("iris.dockCollapsed", "1");
    const dock = dockFixture();
    expect(dock.collapsed()).toBe(true);
    dock.render({ docked: false });
    expect(AW.$("chat")).toBeVisible();
    expect(AW.$("collapse")).not.toBeVisible();
    dock.render({ docked: true });
    expect(AW.$("chat")).not.toBeVisible();
    expect(document.body).toHaveClass("docked", "collapsed");
  });

  it("works when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const dock = dockFixture();
    dock.render({ docked: true });
    expect(dock.collapsed()).toBe(false);
    dock.setCollapsed(true);
    expect(AW.$("chat")).not.toBeVisible();
  });
});

// ------------------------------------------------------------ tabs

describe("tabs", () => {
  const TABS = [
    { id: "overview", label: "Overview", home: true },
    { id: "findings", label: "Findings" },
    { id: "rules", label: "Rules" },
  ];

  function tabsFixture() {
    document.body.innerHTML = `<nav id="tabs"></nav>`;
    const onShow = vi.fn();
    const onRestart = vi.fn();
    const tabs = AW.createTabs({ nav: AW.$("tabs"), tabs: TABS, onShow, onRestart });
    return { tabs, onShow, onRestart };
  }

  const selected = () =>
    [...document.querySelectorAll("[role=tab]")].filter((t) => t.getAttribute("aria-selected") === "true").map((t) => t.textContent);

  it("switches tabs, keeps the tab in the hash, and tells the app", () => {
    const { tabs, onShow } = tabsFixture();
    tabs.show(tabs.fromHash().id, { fromHash: true });
    expect([...document.querySelectorAll("[role=tab]")].map((t) => t.textContent)).toEqual([
      "Overview",
      "Findings",
      "Rules",
    ]);
    expect(selected()).toEqual(["Overview"]);
    fireEvent.click(document.querySelector('button[data-tab="findings"]')!);
    expect(selected()).toEqual(["Findings"]);
    expect(tabs.current().id).toBe("findings");
    expect(window.location.hash).toBe("#findings");
    expect(document.body.dataset.tab).toBe("findings");
    expect(onShow).toHaveBeenLastCalledWith(TABS[1], TABS[0]);
  });

  it("clicking the open tab restarts it", () => {
    const { tabs, onShow, onRestart } = tabsFixture();
    tabs.show("rules");
    onShow.mockClear();
    fireEvent.click(document.querySelector('button[data-tab="rules"]')!);
    expect(onRestart).toHaveBeenCalledWith(TABS[2]);
    expect(onShow).not.toHaveBeenCalled();
  });

  it("opens on the tab the hash names, follows the hash, and an unknown id opens home", () => {
    window.history.replaceState(null, "", "#rules");
    const { tabs } = tabsFixture();
    expect(tabs.fromHash().id).toBe("rules");
    window.history.replaceState(null, "", "#findings");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(selected()).toEqual(["Findings"]);
    expect(tabs.show("nope").id).toBe("overview");
  });
});

// ------------------------------------------------------------ api and errors

describe("api", () => {
  it("GETs without a body and POSTs JSON with one, same-origin, relative to base", async () => {
    const calls: [string, RequestInit][] = [];
    window.fetch = vi.fn(async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return Response.json({ ok: true });
    }) as never;
    const api = AW.createApi({ base: "api/" });
    await api("state");
    await api("chat", { history: [], deadline: 300 });
    expect(calls[0][0]).toBe("api/state");
    expect(calls[0][1].method).toBe("GET");
    expect(calls[1][1].method).toBe("POST");
    expect(calls[1][1].body).toBe('{"history":[],"deadline":300}');
    expect(calls[1][1].credentials).toBe("same-origin");
  });

  it("gives up after 330 s by default and says it took too long", async () => {
    vi.useFakeTimers();
    window.fetch = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))),
        ),
    ) as never;
    const pending = AW.createApi()("chat", {}).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(329_999);
    let settled = false;
    void pending.then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await pending;
    expect(error.status).toBe(504);
    expect(error.timedOut).toBe(true);
    expect(AW.plainError(error, { agent: "Iris" })).toBe("Iris took too long, so the turn was stopped.");
  });

  it("throws status and detail; plainError never shows host text on a 5xx", async () => {
    const SENTINEL = "Traceback SENTINEL token=abc";
    window.fetch = vi.fn(async (url: string) =>
      url.endsWith("boom")
        ? Response.json({ detail: SENTINEL }, { status: 500 })
        : Response.json({ detail: "Iris has not collected an overview here yet" }, { status: 409 }),
    ) as never;
    const api = AW.createApi();
    const boom = await api("boom").catch((e: unknown) => e);
    expect(boom.status).toBe(500);
    expect(boom.detail).toBe(SENTINEL);
    expect(AW.plainError(boom, { agent: "Iris" })).not.toContain("SENTINEL");
    const refused = await api("state").catch((e: unknown) => e);
    expect(AW.plainError(refused)).toBe("Iris has not collected an overview here yet.");
    for (const status of [502, 503]) expect(AW.plainError({ status, detail: SENTINEL }, { agent: "Iris" })).toContain("Iris could not be reached");
    expect(AW.plainError({ status: 401 })).toMatch(/sign-in has expired/);
    expect(AW.plainError({ status: 504, detail: SENTINEL })).not.toContain("SENTINEL");
  });

  it("a network failure is status 0", async () => {
    window.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as never;
    const error = await AW.createApi()("state").catch((e: unknown) => e);
    expect(error.status).toBe(0);
    expect(AW.plainError(error)).toBe("The workspace could not reach Omnigent. Check your connection.");
  });
});

// ------------------------------------------------------------ theme and context

describe("theme", () => {
  it("applies ?theme= at mount, then follows same-origin host messages only", async () => {
    window.history.replaceState(null, "", "/v1/iris/sessions/s1/ui/?theme=dark");
    AW.theme.init({ messageKey: "irisHostTheme" });
    expect(document.documentElement.dataset.theme).toBe("dark");
    window.dispatchEvent(new MessageEvent("message", { origin: "https://evil.example", data: { irisHostTheme: "light" } }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    window.dispatchEvent(new MessageEvent("message", { origin: location.origin, data: { otherKey: "light" } }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    window.dispatchEvent(new MessageEvent("message", { origin: location.origin, data: { irisHostTheme: "light" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
    window.dispatchEvent(new MessageEvent("message", { origin: location.origin, data: { irisHostTheme: "red" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("context", () => {
  it("cleanName gives one bounded line without control characters", () => {
    expect(AW.cleanName("a\nb\u0000c d   e")).toBe("a b c d e");
    expect(AW.cleanName("x".repeat(200))).toHaveLength(120);
    expect(AW.cleanName("x".repeat(200), 10)).toHaveLength(10);
    expect(AW.cleanName(42)).toBe("");
  });

  it("the toggle drops the page until reset", () => {
    document.body.innerHTML = `<div id="context"><span id="context-label"></span><button id="context-remove">Don't include this page</button></div>`;
    const toggle = AW.createContextToggle({
      badge: AW.$("context"),
      label: AW.$("context-label"),
      button: AW.$("context-remove"),
    });
    toggle.render("About finding f1");
    expect(AW.$("context")).toBeVisible();
    expect(AW.$("context-label").textContent).toBe("About finding f1");
    fireEvent.click(AW.$("context-remove"));
    expect(toggle.included()).toBe(false);
    expect(AW.$("context")).not.toBeVisible();
    toggle.reset();
    expect(toggle.included()).toBe(true);
    expect(AW.$("context")).toBeVisible();
  });
});
