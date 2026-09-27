// Iris's framed workspace v2 (`omnigent/airbrx/iris/ui/`), on the shared
// workspace kernel (`omnigent/airbrx/workspace/ui/`). Both are plain browser
// scripts, not part of the web bundle, so they are read from disk and run
// against jsdom, as evaWorkspaceApp.test.ts runs Eva's app.
//
// The honesty rules in docs/iris/WORKSPACE_V2.md section 5 are each one test
// here, named "rule N: ...". They were paid for by incidents in the pinned
// workspace and its host.js adapter, and they are what this app must keep.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const UI = join(HERE, "../../../omnigent/airbrx/iris/ui");
const KERNEL = join(HERE, "../../../omnigent/airbrx/workspace/ui");
const html = readFileSync(join(UI, "index.html"), "utf8");
const script = readFileSync(join(UI, "app.js"), "utf8");
const BODY_TAG = /<body([^>]*)>/.exec(html)?.[1] ?? "";
const BODY = /<body[^>]*>([\s\S]*?)<script /.exec(html)?.[1] ?? "";
const DEFAULT_ON_STALE = /data-on-stale="([^"]*)"/.exec(BODY_TAG)?.[1];

// The kernel files, in the order index.html loads them.
const KERNEL_FILES = [...html.matchAll(/<script src="kernel\/([^"]+)"><\/script>/g)].map(
  (m) => m[1],
);

// ------------------------------------------------------------ the kernel --
//
// W2's shared kernel, loaded from disk in the order index.html names it. The
// test fails loudly if index.html names a file the kernel does not have.
const kernelSources = KERNEL_FILES.map((f) => readFileSync(join(KERNEL, f), "utf8"));

// ------------------------------------------------------------ the adapter --
//
// What the adapter's real handlers answer, captured by W1's
// tests/airbrx/test_iris_workspace_fixtures.py, which fails when they drift.
// Hand-written bodies are kept only for what the fixture cannot hold: the
// native session record the dock streams from, the session's files, the
// account surface (`/v1/iris`, `/v1/iris/account`), a baseline this browser
// pinned, and failures the adapter has no captured case for.

interface Captured {
  status: number;
  body: unknown;
}
const ADAPTER = JSON.parse(
  readFileSync(join(HERE, "__fixtures__/irisAdapter.json"), "utf8"),
) as Record<string, Record<string, Captured>>;
type Scenario = keyof typeof ADAPTER;
const replay = ({ status, body }: Captured) => Response.json(body, { status });

interface Metrics {
  requests: number;
  cache_hits: number;
  cache_misses: number;
  hit_rate: number | null;
  hit_rate_denominator: number;
  start_date: string;
  end_date: string;
  [key: string]: unknown;
}
interface Finding {
  id: string;
  evidence_ids: string[];
  [key: string]: unknown;
}
interface Report {
  tenant_id: string;
  mode: string;
  metrics: Metrics;
  evidence: { id: string; source_tool: string }[];
  findings: Finding[];
  [key: string]: unknown;
}
interface State {
  overview: Report;
  audit: Report;
  investigation: { current: Metrics; previous: Metrics } & Record<string, unknown>;
  proposal: Record<string, unknown> | null;
  captured_at: number;
  [key: string]: unknown;
}

// The fullest capture: overview, audit, an investigation and a proposal.
const FULL = ADAPTER.investigated_and_proposed.state.body as State;
const TENANT = FULL.overview.tenant_id;
const CAPTURED = FULL.captured_at;
const METRICS = FULL.overview.metrics;
const OPPORTUNITY = FULL.overview.evidence.find(
  (e) => e.source_tool === "get_cache_opportunities",
)!.id;
const FINDING = FULL.audit.findings[0].id;
const RULES_EVIDENCE = FULL.audit.findings[0].evidence_ids[0];
const PROPOSAL_HASH = String(
  (FULL.proposal!.proposal_view as { baseline_hash: string }).baseline_hash,
);
const READINESS = ADAPTER.captured.readiness.body as Record<string, unknown>;
const ANSWER = (ADAPTER.captured.chat.body as { text: string }).text;
// A baseline this browser pinned a week earlier. Pins live in localStorage,
// not in the adapter, so this one is written here: the previous period's
// dates from the fixture, with fewer hits, so the comparison has a change.
const PREVIOUS: Metrics = {
  ...structuredClone(FULL.investigation.previous),
  requests: 600,
  cache_hits: 420,
  cache_misses: 180,
  hit_rate: 0.7,
  hit_rate_denominator: 600,
};

/** The fullest captured state, with top-level keys replaced. */
function makeState(overrides: Record<string, unknown> = {}): State {
  return { ...structuredClone(FULL), ...overrides } as State;
}

type Route = (init?: RequestInit) => Response | Promise<Response>;
const json = (body: unknown, status = 200) => Response.json(body, { status });
const detail = (text: string, status: number) => json({ detail: text }, status);
const captured =
  (scenario: Scenario, route: string): Route =>
  () =>
    replay(ADAPTER[scenario][route]);

let fetchMock: ReturnType<typeof vi.fn>;
let items: unknown[] = [];
// The native session's status, as GET /v1/sessions/s1 reports it.
let sessionStatus = "idle";
const listeners: [string, EventListener][] = [];
const realAdd = window.addEventListener.bind(window);
let apiOptions: unknown[] = [];

function pathOf(input: unknown) {
  const raw = input instanceof Request ? input.url : String(input);
  return raw.replace(/^https?:\/\/[^/]+/, "").replace(/^\/v1\/iris\/sessions\/s1\/ui\//, "");
}

/**
 * Answer the app's calls from one captured scenario. A route the scenario did
 * not capture falls back to `investigated_and_proposed`, then `captured`; the
 * `overrides` win over both.
 */
function serve(
  overrides: Record<string, Route> = {},
  scenario: Scenario = "investigated_and_proposed",
) {
  fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const raw = pathOf(input);
    const path = raw.startsWith("/v1/sessions/s1/items")
      ? "items"
      : raw.startsWith("/v1/sessions/s1/resources/files")
        ? "files"
        : raw.replace(/^api\//, "");
    if (path in overrides) return overrides[path](init);
    const recorded =
      ADAPTER[scenario][path] ?? ADAPTER.investigated_and_proposed[path] ?? ADAPTER.captured[path];
    if (recorded) return replay(recorded);
    switch (path) {
      case "/v1/sessions/s1":
        return json({
          id: "s1",
          status: sessionStatus,
          active_response_id: sessionStatus === "running" ? "resp_1" : null,
        });
      case "items":
        return json({ data: items.slice().reverse(), has_more: false });
      case "cancel":
        return json({});
      case "files":
        return json({ data: [] });
      case "/v1/iris":
        return json({
          agent_id: "agent",
          bindings: [
            { tenant_id: TENANT, name: "Iris fixture tenant", fixture: true, host_online: true },
            {
              tenant_id: "f65d9135-0000-4000-8000-000000000000",
              name: "Production",
              fixture: false,
              host_online: true,
            },
          ],
        });
      case "/v1/iris/account":
        return json({
          generated_at: CAPTURED + 60,
          tenants: 2,
          ranked: [
            {
              tenant_id: "f65d9135-0000-4000-8000-000000000000",
              name: "Production",
              hit_rate: 0.5,
              hit_rate_denominator: 2000,
              requests: 2000,
              cache_misses: 1000,
              covered_days: 7,
              requested_days: 7,
              captured_at: CAPTURED,
              age_seconds: 120,
            },
          ],
          quarantined: [],
        });
    }
    throw new Error(`unexpected fetch ${String(input)}`);
  });
  window.fetch = fetchMock as never;
}

function calls(path: string) {
  return fetchMock.mock.calls.filter(([input]) => pathOf(input) === `api/${path}`);
}
function chatBodies() {
  return calls("chat").map(([, init]) => JSON.parse(String((init as RequestInit)?.body)));
}
function requested(pattern: RegExp) {
  return fetchMock.mock.calls.some(([input]) => pattern.test(pathOf(input)));
}

/** Run the kernel and the app as index.html would, on a fresh body. */
function start(hash = "", onStale = DEFAULT_ON_STALE) {
  window.history.replaceState(null, "", `/v1/iris/sessions/s1/ui/${hash}`);
  document.body.innerHTML = BODY;
  document.body.className = "";
  document.body.dataset.pollMs = "10";
  if (onStale) document.body.dataset.onStale = onStale;
  else delete document.body.dataset.onStale;
  for (const source of kernelSources) (0, eval)(source);
  const AW = (window as unknown as { AirbrxWorkspace: Record<string, unknown> }).AirbrxWorkspace;
  const createApi = AW.createApi as (options: unknown) => unknown;
  AW.createApi = (options: unknown) => {
    apiOptions.push(options);
    return createApi(options);
  };
  (0, eval)(script);
}

/** Start and wait for the capture to be drawn. */
async function mount(hash = "", onStale = DEFAULT_ON_STALE) {
  start(hash, onStale);
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).toMatch(/Iris read this tenant /),
  );
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
}

const messages = () => document.getElementById("messages")!;
const view = () => document.getElementById("view")!;

function ask(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Message Iris" }), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

function tab(label: string) {
  fireEvent.click(screen.getByRole("tab", { name: label }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  items = [];
  sessionStatus = "idle";
  apiOptions = [];
  window.addEventListener = ((type: string, listener: EventListener, options?: unknown) => {
    listeners.push([type, listener]);
    realAdd(type, listener, options as never);
  }) as never;
  serve();
});

afterEach(() => {
  for (const [type, listener] of listeners.splice(0)) window.removeEventListener(type, listener);
  window.addEventListener = realAdd as never;
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------- the tabs --

const TABS: [string, string, RegExp][] = [
  ["Overview", "overview", /Cache hit rate/],
  ["Findings", "findings", /Unknown sensitivity/],
  ["Rules", "rules", /Rule effectiveness/],
  ["Proposals", "proposals", new RegExp(PROPOSAL_HASH)],
  ["Evidence", "evidence", new RegExp(OPPORTUNITY)],
  ["Results", "results", /Baseline and now/],
  ["Accounts", "accounts", /Tenants|Every tenant you can open/],
];

it("renders the tabs from the one tab list, Overview first and selected", async () => {
  await mount();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(
    TABS.map(([label]) => label),
  );
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
});

it.each(TABS)(
  "the %s tab renders the fixture state and names itself in the hash",
  async (label, id, marker) => {
    // Start elsewhere, so the click is a switch and not a restart of the open tab.
    await mount(id === "overview" ? "#rules" : "");
    tab(label);
    expect(screen.getByRole("tab", { name: label })).toHaveAttribute("aria-selected", "true");
    expect(window.location.hash).toBe(`#${id}`);
    await waitFor(() => expect(view().textContent).toMatch(marker));
  },
);

it("opens on the tab the URL names", async () => {
  await mount("#rules");
  expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
  expect(view().textContent).toContain("report-cache");
});

it("Overview shows every KPI from state with its denominator, coverage, focus and monitoring", async () => {
  await mount();
  const text = view().textContent ?? "";
  expect(text).toContain("80.0%");
  expect(text).toContain("560 hits over 700 requests");
  expect(text).toContain("140");
  expect(text).toContain(String((METRICS.latency as { label: string }).label));
  expect(text).toContain("7 / 7 days");
  expect(text).toContain("Monitoring is unavailable");
  expect(text).toContain("2026-09-19 to 2026-09-26");
  expect(text).toContain(`tenant ${TENANT}`);
  expect(view().querySelectorAll(".coverage .day")).toHaveLength(7);
  // The overview's finding and the audit's: the top two.
  expect(view().querySelectorAll("[data-finding-id]")).toHaveLength(2);
});

it("a measured zero is shown as 0, never as missing", async () => {
  serve({
    state: () =>
      json(
        makeState({
          overview: {
            ...makeState().overview,
            metrics: {
              ...METRICS,
              requests: 0,
              cache_hits: 0,
              cache_misses: 0,
              hit_rate: null,
              hit_rate_denominator: 0,
            },
          },
        }),
      ),
  });
  await mount();
  const tiles = [...view().querySelectorAll(".kpi")];
  const tile = (label: string) =>
    tiles.find((t) => t.querySelector(".label")?.textContent === label)!;
  expect(tile("Cache misses").querySelector(".value")?.textContent).toBe("0");
  expect(tile("Requests").querySelector(".value")?.textContent).toBe("0");
  expect(tile("Cache hit rate").querySelector(".note")?.textContent).toBe("0 hits over 0 requests");
  // No traffic means no rate: that one is not measured, and says so.
  expect(tile("Cache hit rate").querySelector(".value")?.textContent).toBe("Not measured");
});

it("Findings lists every finding with severity, confidence, rule and next step", async () => {
  await mount("#findings");
  const cards = view().querySelectorAll("[data-finding-id]");
  expect(cards).toHaveLength(FULL.overview.findings.length + FULL.audit.findings.length);
  const text = view().textContent ?? "";
  for (const f of [...FULL.overview.findings, ...FULL.audit.findings]) {
    expect(text).toContain(String(f.severity));
    expect(text).toContain(`${String(f.confidence)} confidence`);
    expect(text).toContain(`Rule ${String(f.rule_id)}`);
    expect(text).toContain(`Next: ${String(f.next_step)}`);
  }
});

it("a finding in both the overview and the audit is listed once", async () => {
  const state = makeState();
  state.audit.findings.push(structuredClone(state.overview.findings[0]));
  serve({ state: () => json(state) });
  await mount("#findings");
  expect(view().querySelectorAll("[data-finding-id]")).toHaveLength(2);
});

it("a finding with no rule is tenant-wide", async () => {
  const state = makeState();
  state.audit.findings[0].rule_id = null;
  serve({ state: () => json(state) });
  await mount("#findings");
  expect(view().querySelector(`[data-finding-id="${FINDING}"]`)?.textContent).toContain(
    "Tenant-wide",
  );
});

it("Investigate asks Iris in the chat, naming the finding", async () => {
  await mount("#findings");
  const card = view().querySelector(`[data-finding-id="${FINDING}"]`) as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: "Investigate this finding" }));
  await waitFor(() => expect(chatBodies()).toHaveLength(1));
  expect(chatBodies()[0].history.at(-1).content).toMatch(
    new RegExp(`^Investigate finding ${FINDING}\\.`),
  );
});

it("an evidence link opens the Evidence tab at that row", async () => {
  await mount("#findings");
  const card = view().querySelector(`[data-finding-id="${FINDING}"]`) as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: RULES_EVIDENCE.slice(0, 8) }));
  expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
  expect(view().querySelector(`[data-evidence-id="${RULES_EVIDENCE}"]`)).toHaveClass("highlight");
});

it("Rules shows the annual window apart from the period, and configuration findings per rule", async () => {
  await mount("#rules");
  const table = screen.getByRole("table", { name: "Rule effectiveness" });
  const cells = [...table.querySelectorAll("tbody td")].map((td) => td.textContent);
  expect(cells).toEqual(["report-cache", "100", "80", "20", "Not reported"]);
  expect(view().textContent).toContain("Annual window (year not reported)");
  expect(view().textContent).toContain("separate window from the overview period");
  const block = view().querySelector('[data-rule-id="report-cache"]') as HTMLElement;
  expect(within(block).getByText("Unknown sensitivity")).toBeInTheDocument();
  fireEvent.click(within(block).getByRole("button", { name: "Propose a change to this rule" }));
  await waitFor(() => expect(chatBodies()).toHaveLength(1));
  expect(chatBodies()[0].history.at(-1).content).toMatch(
    /^Propose a change to rule report-cache\./,
  );
});

it("Evidence shows the investigation's current and previous periods", async () => {
  await mount("#evidence");
  const table = screen.getByRole("table", { name: "Previous and current period" });
  const rows = [...table.querySelectorAll("tbody tr")].map((tr) =>
    [...tr.querySelectorAll("td")].map((td) => td.textContent),
  );
  expect(rows[0]).toEqual([
    "Period",
    "2026-09-12 to 2026-09-19 (UTC, end exclusive)",
    "2026-09-19 to 2026-09-26 (UTC, end exclusive)",
  ]);
  expect(rows[1]).toEqual(["Cache hit rate", "80.0% (560 over 700)", "80.0% (560 over 700)"]);
});

it("Evidence lists the fixture's rows by id, and an investigation's absence is said", async () => {
  await mount("#evidence");
  expect(view().querySelector(`[data-evidence-id="${OPPORTUNITY}"]`)).not.toBeNull();
  document.body.innerHTML = "";
  serve({}, "captured");
  await mount("#evidence");
  expect(view().textContent).toContain("Iris has not compared periods in this session.");
});

it("Results compares a pinned baseline of the same tenant with this capture", async () => {
  localStorage.setItem(
    `iris.baseline.${TENANT}`,
    JSON.stringify({ tenant_id: TENANT, captured_at: CAPTURED - 604800, metrics: PREVIOUS }),
  );
  await mount("#results");
  const table = screen.getByRole("table", { name: /^Baseline/ });
  expect(table.textContent).toContain("70.0% (420 over 600)");
  expect(table.textContent).toContain("+10.0 percentage points");
});

it("Results pins this capture as the baseline under the tenant's key", async () => {
  await mount("#results");
  fireEvent.click(screen.getByRole("button", { name: "Pin this capture as the baseline" }));
  expect(JSON.parse(localStorage.getItem(`iris.baseline.${TENANT}`)!)).toEqual({
    tenant_id: TENANT,
    captured_at: CAPTURED,
    metrics: METRICS,
  });
});

it("Accounts lists the tenants read-only; this session's tenant has no button", async () => {
  await mount("#accounts");
  const table = await screen.findByRole("table", { name: "Tenants in this account" });
  const here = table.querySelector(`[data-tenant-id="${TENANT}"]`) as HTMLElement;
  expect(here.textContent).toContain("This session");
  expect(within(here).queryByRole("button")).toBeNull();
  const other = table.querySelector('[data-tenant-id^="f65d9135"]') as HTMLElement;
  expect(other.textContent).toContain("50.0%");
  expect(other.textContent).toContain("of 2,000 requests");
});

it("Accounts opens another tenant through the shell, never by creating or switching a session here", async () => {
  const posted = vi.spyOn(window.parent, "postMessage");
  await mount("#accounts");
  const table = await screen.findByRole("table", { name: "Tenants in this account" });
  const other = table.querySelector('[data-tenant-id^="f65d9135"]') as HTMLElement;
  // Since #113 the shell goes back to the tenant's last session when there is
  // one, so neither the button nor the heading promises a new session.
  expect(screen.getByRole("heading", { name: "Accounts" }).parentElement?.textContent).not.toMatch(
    /new session/i,
  );
  expect(within(other).queryByRole("button", { name: /new session/i })).toBeNull();
  fireEvent.click(within(other).getByRole("button", { name: "Open" }));
  expect(posted).toHaveBeenCalledWith(
    { type: "iris.openTenant", tenant_id: "f65d9135-0000-4000-8000-000000000000" },
    window.location.origin,
  );
  expect(
    fetchMock.mock.calls.some(
      ([input, init]) =>
        /\/v1\/sessions(\?|$)/.test(pathOf(input)) && (init as RequestInit)?.method === "POST",
    ),
  ).toBe(false);
  posted.mockRestore();
});

it("docks the chat beside a tab other than Overview; it collapses to the rail and comes back", async () => {
  await mount();
  const chat = screen.getByRole("region", { name: "Chat with Iris" });
  expect(document.getElementById("collapse")).not.toBeVisible();
  tab("Findings");
  expect(chat).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
  expect(chat).not.toBeVisible();
  expect(localStorage.getItem("iris.dockCollapsed")).toBe("1");
  fireEvent.click(screen.getByRole("button", { name: /Ask Iris/ }));
  expect(chat).toBeVisible();
  expect(localStorage.getItem("iris.dockCollapsed")).toBe("0");
});

it("a collapsed dock never hides the chat on Overview", async () => {
  localStorage.setItem("iris.dockCollapsed", "1");
  await mount("#rules");
  expect(document.getElementById("chat")).not.toBeVisible();
  tab("Overview");
  expect(screen.getByRole("region", { name: "Chat with Iris" })).toBeVisible();
});

it("docked beside a tab, the question says which tab, and the note can be left out", async () => {
  await mount("#rules");
  ask("Is this rule safe?");
  await waitFor(() => expect(chatBodies()).toHaveLength(1));
  expect(chatBodies()[0].history[0].content).toBe(
    "Is this rule safe?\n\n(I am looking at the Rules tab of the Iris workspace.)",
  );
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Don't include this page" }));
  ask("And now?");
  await waitFor(() => expect(chatBodies()).toHaveLength(2));
  expect(chatBodies()[1].history.at(-1).content).toBe("And now?");
});

it("sends a question and shows Iris's answer", async () => {
  await mount();
  ask("How is the cache?");
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  expect(messages().querySelector("li.agent strong")?.textContent).toBe("80.0%");
});

// ------------------------------------------------------ the honesty rules --

it("rule 1: no synthetic data; with state 409 Iris says she has not collected, and shows no numbers", async () => {
  serve({ refresh: captured("first_collect_produced_nothing", "refresh") }, "fresh_session");
  start();
  await waitFor(() =>
    expect(view().textContent).toContain("Iris has not collected an overview here yet"),
  );
  expect(view().textContent).not.toMatch(/\d+(\.\d)?%/);
  expect(view().querySelector(".kpi")).toBeNull();
  expect(requested(/iris-state\.json|demo-state\.json/)).toBe(false);
});

it.each([
  ["409", captured("busy_session", "chat")],
  ["502", () => detail("native session operation failed", 502)],
  ["504", () => detail("timed out", 504)],
  [
    "a network error",
    () => {
      throw new TypeError("Failed to fetch");
    },
  ],
])("rule 2: a turn refused with %s is not answered in Iris's voice", async (_, route) => {
  serve({ chat: route as Route });
  await mount();
  ask("How is the cache?");
  const line = await waitFor(() => {
    const found = messages().querySelector("li.error");
    expect(found).not.toBeNull();
    return found as HTMLElement;
  });
  expect(line.textContent).toMatch(
    /^Iris did not answer\. .+\. Nothing has been computed in her place\./,
  );
  expect(messages().querySelector("li.agent")).toBeNull();
});

it("rule 2: a turn that ends without an answer is not answered either", async () => {
  serve({ chat: () => json({ text: "", tools: [], failed: true, item_id: null }) });
  await mount();
  ask("How is the cache?");
  await waitFor(() => expect(messages().querySelector("li.error")).not.toBeNull());
  expect(messages().querySelector("li.error")?.textContent).toContain("Iris did not answer.");
  expect(messages().querySelector("li.agent")).toBeNull();
});

it("rule 3: with state loaded and the turn refused, no number, finding or rule from state reaches the chat", async () => {
  serve({ chat: () => detail("native session operation failed", 502) });
  await mount();
  ask("What is my hit rate and which rule is risky?");
  await waitFor(() => expect(messages().querySelector("li.error")).not.toBeNull());
  const text = messages().textContent ?? "";
  for (const fromState of [
    "80.0%",
    "560",
    "700",
    "140",
    "report-cache",
    "sensitivity",
    "per-request",
  ])
    expect(text).not.toContain(fromState);
});

it("rule 4: host error text is never shown", async () => {
  const SENTINEL = "SENTINEL-host-secret-4f2a";
  serve({ chat: () => detail(`Traceback: ${SENTINEL}`, 500) });
  await mount();
  ask("Hello");
  await waitFor(() => expect(messages().querySelector("li.error")).not.toBeNull());
  expect(document.body.textContent).not.toContain(SENTINEL);

  // Nor a readiness failure's.
  document.body.innerHTML = "";
  serve({ readiness: () => detail(`boom ${SENTINEL}`, 500) });
  start();
  await waitFor(() =>
    expect(document.getElementById("host-status")?.textContent).toMatch(/^This host will not/),
  );
  expect(document.body.textContent).not.toContain(SENTINEL);
});

it.each(TABS)(
  "rule 5: the synthetic fixture chip is in the header on the %s tab",
  async (label) => {
    await mount();
    tab(label);
    expect(screen.getByText("Synthetic fixture")).toBeVisible();
  },
);

it("rule 5: the chip follows the overview's mode when readiness does not say fixture", async () => {
  serve({ readiness: () => json({ ...READINESS, fixture: false }) });
  await mount();
  expect(screen.getByText("Synthetic fixture")).toBeVisible();
});

it("rule 5: no chip for a live tenant", async () => {
  serve({
    readiness: () => json({ ...READINESS, fixture: false }),
    state: () => json(makeState({ overview: { ...makeState().overview, mode: "live" } })),
  });
  await mount();
  expect(screen.getByText("Synthetic fixture")).not.toBeVisible();
});

it("rule 6: a 409 on the first read collects exactly once, and a failed collection does not loop", async () => {
  serve({ refresh: captured("first_collect_produced_nothing", "refresh") }, "fresh_session");
  start();
  await waitFor(() =>
    expect(view().textContent).toContain("Iris has not collected an overview here yet"),
  );
  expect(calls("refresh")).toHaveLength(1);
  // A turn re-reads state (still 409); that re-read never collects.
  ask("Why is there nothing?");
  await waitFor(() => expect(calls("state").length).toBeGreaterThanOrEqual(2));
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(calls("refresh")).toHaveLength(1);
});

it("rule 6: a successful collection does not trigger another", async () => {
  serve({}, "fresh_session");
  start();
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  expect(calls("refresh")).toHaveLength(1);
});

it("rule 7 (show, the default): a stale capture is drawn labelled, with one background refresh", async () => {
  expect(DEFAULT_ON_STALE).toBe("show");
  const held = deferred<Response>();
  serve({ refresh: () => held.promise }, "stale_capture");
  start();
  // Drawn while the refresh is still running.
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  expect(document.getElementById("freshness")?.textContent).toMatch(
    /Iris read this tenant .+, may be out of date$/,
  );
  await waitFor(() => expect(calls("refresh")).toHaveLength(1));
  held.resolve(replay(ADAPTER.stale_capture.refresh));
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).not.toContain("may be out of date"),
  );
  expect(calls("refresh")).toHaveLength(1);
});

it("rule 7 (collect): a stale capture draws nothing until the one refresh returns", async () => {
  const held = deferred<Response>();
  serve({ refresh: () => held.promise }, "stale_capture");
  start("", "collect");
  await waitFor(() => expect(calls("refresh")).toHaveLength(1));
  expect(view().textContent).not.toContain("80.0%");
  expect(view().querySelector(".kpi")).toBeNull();
  held.resolve(replay(ADAPTER.stale_capture.refresh));
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  expect(document.getElementById("freshness")?.textContent).not.toContain("may be out of date");
  expect(calls("refresh")).toHaveLength(1);
});

it("rule 8: a first collection and a stale refresh say different things", async () => {
  const first = deferred<Response>();
  serve({ refresh: () => first.promise }, "fresh_session");
  start();
  await waitFor(() => expect(document.getElementById("collecting")).toBeVisible());
  const firstText = document.getElementById("collecting")?.textContent ?? "";
  expect(firstText).toContain("first overview");
  first.resolve(replay(ADAPTER.captured.refresh));
  await waitFor(() => expect(document.getElementById("collecting")).not.toBeVisible());

  document.body.innerHTML = "";
  const stale = deferred<Response>();
  serve({ refresh: () => stale.promise }, "stale_capture");
  start();
  await waitFor(() => expect(document.getElementById("collecting")).toBeVisible());
  const staleText = document.getElementById("collecting")?.textContent ?? "";
  expect(staleText).toContain("out-of-date capture");
  expect(staleText).not.toContain("first overview");
  stale.resolve(replay(ADAPTER.stale_capture.refresh));
  await waitFor(() => expect(document.getElementById("collecting")).not.toBeVisible());
});

it("a collection that produced nothing new keeps the capture, with its time, and the chat", async () => {
  serve({}, "refresh_read_nothing");
  await mount();
  ask("How is the cache?");
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Collect a fresh overview" }));
  await waitFor(() =>
    expect(messages().textContent).toContain("That collection produced no new overview."),
  );
  expect(view().textContent).toContain("80.0%");
  expect(document.getElementById("freshness")?.textContent).toMatch(/^Iris read this tenant /);
  expect(within(messages()).getByText("How is the cache?")).toBeInTheDocument();
});

// Review of #118/#119: a refresh 409 is "that collection produced no overview"
// only when a collection ran and missed. When the adapter refused to start one,
// its own detail says why and when to retry, and no collect is recorded.
it.each([
  ["a repeat collect inside the hold", "repeat_collect_refused"],
  ["a busy session", "busy_session"],
] as const)(
  "a collect refused for %s shows the adapter's detail, not 'produced no overview'",
  async (_, scenario) => {
    const refusal = ADAPTER[scenario].refresh;
    const said = (refusal.body as { detail: string }).detail.replace(/[.\s]+$/, "");
    serve({ refresh: () => replay(refusal) });
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Collect a fresh overview" }));
    await waitFor(() => expect(messages().textContent).toContain(said));
    expect(messages().textContent).not.toContain("produced no");
    expect(messages().textContent).not.toContain("You had Iris collect a fresh overview.");
    expect(calls("refresh")).toHaveLength(1);
    // The capture on screen is untouched.
    expect(view().textContent).toContain("80.0%");
  },
);

it("rule 9: chat and refresh run on a 330 s client deadline, and chat sends deadline 300", async () => {
  await mount();
  expect(apiOptions).toContainEqual(expect.objectContaining({ timeoutMs: 330000 }));
  expect(apiOptions.every((o) => (o as { timeoutMs: number }).timeoutMs === 330000)).toBe(true);
  ask("Hello");
  await waitFor(() => expect(chatBodies()).toHaveLength(1));
  expect(chatBodies()[0].deadline).toBe(300);
});

it("rule 10: a state for another tenant is refused and not drawn", async () => {
  serve({
    state: () =>
      json(makeState({ overview: { ...makeState().overview, tenant_id: "someone-else" } })),
  });
  start();
  await waitFor(() => expect(view().textContent).toContain("different tenant"));
  expect(view().textContent).not.toContain("80.0%");
  expect(view().querySelector(".kpi")).toBeNull();
});

it("rule 10: Results refuses a baseline from another tenant", async () => {
  localStorage.setItem(
    `iris.baseline.${TENANT}`,
    JSON.stringify({
      tenant_id: "someone-else",
      captured_at: CAPTURED - 604800,
      metrics: PREVIOUS,
    }),
  );
  await mount("#results");
  expect(view().textContent).toContain(
    "This baseline belongs to a different tenant, so it is not compared.",
  );
  expect(screen.queryByRole("table", { name: /^Baseline/ })).toBeNull();
});

it("rule 11: when no turn has completed here, the unverified line shows", async () => {
  serve({}, "fresh_session");
  await mount();
  expect(document.getElementById("host-status")?.textContent).toContain(
    "Iris has not completed a turn in this session yet",
  );
  expect(document.getElementById("host-details")).toBeVisible();
  expect(document.getElementById("host-details")?.textContent).toContain(
    "Not verified: no model turn has completed",
  );
});

it("rule 11: a readiness failure says the host will not run turns, never a blank bar", async () => {
  serve({ readiness: () => detail("Iris is not bound here", 403) });
  start();
  await waitFor(() =>
    expect(document.getElementById("host-status")?.dataset.state).toBe("refused"),
  );
  expect(document.getElementById("host-status")?.textContent).toMatch(
    /^This host will not run turns in this session: .+/,
  );
});

it("rule 12: a refresh keeps the chat", async () => {
  await mount();
  ask("How is the cache?");
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Collect a fresh overview" }));
  await waitFor(() => expect(calls("refresh")).toHaveLength(1));
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
  expect(within(messages()).getByText("How is the cache?")).toBeInTheDocument();
  expect(messages().querySelector("li.agent strong")?.textContent).toBe("80.0%");
});

it("rule 12: a reload rebuilds the chat from the session, and names a refresh turn", async () => {
  items = [
    {
      id: "u0",
      type: "message",
      role: "user",
      content: [
        {
          type: "input_text",
          text: "Call iris_overview and iris_audit for the selected tenant. Do not propose changes.",
        },
      ],
    },
    {
      id: "a0",
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: "Collected." }],
    },
    {
      id: "u1",
      type: "message",
      role: "user",
      content: [
        {
          type: "input_text",
          text: "Is it safe?\n\n(I am looking at the Rules tab of the Iris workspace.)",
        },
      ],
    },
    {
      id: "a1",
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: "It looks **safe**." }],
    },
  ];
  await mount();
  await waitFor(() => expect(messages().querySelectorAll("li.agent")).toHaveLength(2));
  expect(
    within(messages()).getByText("You had Iris collect a fresh overview."),
  ).toBeInTheDocument();
  expect(within(messages()).getByText("Is it safe?")).toBeInTheDocument();
  expect(messages().textContent).not.toContain("I am looking at");
  expect(messages().textContent).not.toContain("Call iris_overview");
});

// QA 2026-09-26 B4: reloaded while Iris was answering, the page showed the
// question and never the answer, until one more reload. The reload now follows
// the turn that is still running and shows its answer when it ends.
const userItem = (id: string, text: string) => ({
  id,
  type: "message",
  role: "user",
  content: [{ type: "input_text", text }],
});
const assistantItem = (id: string, text: string) => ({
  id,
  type: "message",
  role: "assistant",
  content: [{ type: "output_text", text }],
});

it("B4: a reload mid-turn shows the answer when the turn ends, without another reload", async () => {
  items = [userItem("u1", "How is the cache?")];
  sessionStatus = "running";
  start();
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).toMatch(/Iris read this tenant /),
  );
  await waitFor(() =>
    expect(messages().querySelector("[role=status]")?.textContent).toBe("Iris is working…"),
  );
  // Iris is busy: the page does not offer a second turn over the running one.
  expect(document.getElementById("send")).toBeDisabled();
  items.push(
    { id: "f1", type: "function_call", name: "iris__iris_overview", arguments: "{}" },
    { id: "o1", type: "function_call_output", output: "SECRET-TOOL-OUTPUT" },
    assistantItem("a1", "Hit rate is **80.0%**.\n\nOne rule causes most misses."),
  );
  sessionStatus = "idle";
  await waitFor(() =>
    expect(messages().querySelector("li.agent")?.textContent).toContain("One rule causes"),
  );
  expect(messages().querySelectorAll("li.agent > p")).toHaveLength(2);
  expect(messages().textContent).not.toContain("SECRET-TOOL-OUTPUT");
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(messages().querySelector("[role=status]")).toBeNull();
  expect(calls("chat")).toHaveLength(0);
  expect(calls("refresh")).toHaveLength(0);
});

it("B4: a reload during the first collection waits for it, then draws its capture without collecting again", async () => {
  items = [userItem("u0", "Call iris_overview and iris_audit for the selected tenant. Go.")];
  sessionStatus = "running";
  serve(
    {
      state: () =>
        sessionStatus === "running"
          ? detail("No session overview yet; use Refresh from host", 409)
          : json(makeState()),
    },
    "fresh_session",
  );
  start();
  await waitFor(() =>
    expect(messages().querySelector("[role=status]")?.textContent).toBe("Iris is working…"),
  );
  expect(calls("refresh")).toHaveLength(0);
  items.push(assistantItem("a0", "Collected."));
  sessionStatus = "idle";
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
  expect(calls("refresh")).toHaveLength(0);
  expect(messages().querySelector("li.agent")?.textContent).toBe("Collected.");
});

it("rule 13: Stop calls cancel, stops streaming and clears the progress line", async () => {
  const held = deferred<Response>();
  serve({ chat: () => held.promise });
  await mount();
  ask("Take your time");
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeVisible());
  await waitFor(() => expect(messages().querySelector("li.progress")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() => expect(calls("cancel")).toHaveLength(1));
  await waitFor(() => expect(messages().querySelector("li.progress")).toBeNull());
  const polls = fetchMock.mock.calls.filter(([input]) => pathOf(input).includes("/items")).length;
  await new Promise((r) => {
    setTimeout(r, 60);
  });
  expect(fetchMock.mock.calls.filter(([input]) => pathOf(input).includes("/items")).length).toBe(
    polls,
  );
  held.resolve(detail("Iris's turn was cancelled", 409));
  await waitFor(() => expect(messages().querySelector("li.error")).not.toBeNull());
});

it("rule 14: the native chat link is always there, and no files says no files", async () => {
  await mount();
  expect(screen.getByRole("link", { name: "Open native chat" })).toHaveAttribute("href", "/c/s1");
  tab("Evidence");
  fireEvent.click(screen.getByRole("button", { name: "List session downloads" }));
  await waitFor(() => expect(view().textContent).toContain("No files in this session yet."));
  expect(view().textContent).not.toMatch(/no reports/i);
});

it("rule 14: the session's report files are offered as downloads", async () => {
  serve({
    files: () =>
      json({
        data: [
          { id: "f1", filename: "report.json" },
          { id: "f2", filename: "notes.txt" },
          { id: "f3", filename: "proposal.json" },
        ],
      }),
  });
  await mount("#evidence");
  fireEvent.click(screen.getByRole("button", { name: "List session downloads" }));
  const link = await screen.findByRole("link", { name: "report.json" });
  expect(link).toHaveAttribute("href", "/v1/sessions/s1/resources/files/f1/content");
  expect(screen.getByRole("link", { name: "proposal.json" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "notes.txt" })).toBeNull();
});

it("rule 15: tool outputs and arguments never reach the chat, live or on reload", async () => {
  const SENTINEL = "SENTINEL-tenant-evidence-9b1c";
  const toolItems = [
    {
      id: "c1",
      type: "function_call",
      name: "mcp__iris__iris_overview",
      arguments: JSON.stringify({ tenant: SENTINEL }),
    },
    {
      id: "o1",
      type: "function_call_output",
      output: JSON.stringify({ evidence: [{ id: OPPORTUNITY, data: SENTINEL }] }),
    },
    { id: "r1", type: "reasoning", summary: [{ type: "summary_text", text: SENTINEL }] },
  ];
  const held = deferred<Response>();
  serve({ chat: () => held.promise });
  await mount();
  ask("Read it");
  items = toolItems;
  await waitFor(() => expect(messages().textContent).toContain("Reading the tenant's traffic"));
  expect(document.body.textContent).not.toContain(SENTINEL);
  expect(messages().textContent).not.toContain("iris_overview");
  held.resolve(json({ text: ANSWER, tools: ["iris_overview"], failed: false, item_id: "a1" }));
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  expect(document.body.textContent).not.toContain(SENTINEL);
  expect(messages().textContent).not.toContain("iris_overview");

  // And on a reload, from the session record.
  document.body.innerHTML = "";
  items = toolItems;
  serve();
  await mount();
  expect(document.body.textContent).not.toContain(SENTINEL);
  expect(messages().textContent).not.toContain("iris_overview");
});

it("rule 16: a proposal is never presented as applied", async () => {
  await mount("#proposals");
  expect(view().textContent).toContain("Not applied. Needs external approval.");
  const buttons = [...view().querySelectorAll("button")].map((b) => b.textContent ?? "");
  expect(buttons.filter((b) => /apply|approve|deploy|publish/i.test(b))).toEqual([]);
  expect(view().textContent).not.toMatch(/\b(was|is|been|now) (applied|live|deployed)\b/i);
});

it("rule 16: with no proposal yet, the label is still there and nothing can apply", async () => {
  serve({ state: () => json(makeState({ proposal: null })) });
  await mount("#proposals");
  expect(view().textContent).toContain("Not applied. Needs external approval.");
  expect(view().textContent).toContain("Iris has not drafted a proposal in this session.");
  expect(
    [...view().querySelectorAll("button")].some((b) => /apply/i.test(b.textContent ?? "")),
  ).toBe(false);
});

it("rule 17: no em dashes in Iris's UI files", () => {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? walk(path) : [path];
    });
  const offenders = walk(UI).filter((path) => readFileSync(path, "utf8").includes("—"));
  expect(offenders).toEqual([]);
});

// -------------------------------------------------- markdown and the page --

it("Iris's markdown renders as marks, and HTML in her text stays text", async () => {
  serve({
    chat: () =>
      json({
        text: '**Bold** and `code`\n- one\n- two\n<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>',
        tools: [],
        failed: false,
        item_id: "a1",
      }),
  });
  await mount();
  ask("Format test");
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  const line = messages().querySelector("li.agent") as HTMLElement;
  expect(line.querySelector("strong")?.textContent).toBe("Bold");
  expect(line.querySelector("code")?.textContent).toBe("code");
  expect(line.querySelectorAll("li")).toHaveLength(2);
  expect(line.querySelector("img, script")).toBeNull();
  expect(line.textContent).toContain("<img src=x");
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
});

it("text from state is shown as text, never parsed as markup", async () => {
  const evil = '<img src=x onerror="window.pwned=1"><b>bold</b>';
  serve({
    state: () =>
      json(
        makeState({
          audit: {
            ...makeState().audit,
            findings: [{ ...makeState().audit.findings[0], explanation: evil }],
          },
        }),
      ),
  });
  await mount("#findings");
  expect(view().textContent).toContain(evil);
  expect(view().querySelector("img, b")).toBeNull();
});

it("the page declares the host theme source and the stale default", () => {
  expect(/<html[^>]*data-theme-source="host"/.test(html)).toBe(true);
  expect(DEFAULT_ON_STALE).toBe("show");
  expect(html).toContain('href="kernel/brand.css"');
  expect(html).not.toMatch(/Import report/i);
  expect(script).not.toMatch(
    /iris-state\.json|demo-state\.json|innerHTML|localStorage\[?["']iris\.theme/,
  );
});

it("the host's theme message is followed; other origins are ignored", async () => {
  await mount();
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { irisHostTheme: "dark" },
      origin: window.location.origin,
    }),
  );
  expect(document.documentElement.dataset.theme).toBe("dark");
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { irisHostTheme: "light" },
      origin: "https://evil.example",
    }),
  );
  expect(document.documentElement.dataset.theme).toBe("dark");
});

// ------------------------------------------ an incoherent capture (Q19) --
//
// Ported from the pinned workspace's normalize(): a capture whose own numbers
// disagree is refused, with the mismatch named, and no number from it is
// drawn. The incident: "80.0%" captioned "120 hits over 700 requests" beside
// "Requests 1,000".

function withMetrics(changes: Record<string, unknown>) {
  const state = makeState();
  Object.assign(state.overview.metrics, changes);
  return state;
}

async function refusedCapture(state: unknown, hash = "") {
  serve({ state: () => json(state), refresh: () => json(state) });
  start(hash);
  await waitFor(() => expect(view().textContent).toContain("Iris's capture is not shown."));
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
}

it("Q19: the incident capture (80.0%, 120 hits over 700, Requests 1,000) is refused, naming each mismatch", async () => {
  await refusedCapture(
    withMetrics({ hit_rate: 0.8, cache_hits: 120, hit_rate_denominator: 700, requests: 1000 }),
  );
  const text = view().textContent ?? "";
  expect(text).toContain("The overview: the hit rate's denominator is not the request count.");
  expect(text).toContain(
    "The overview: the stated hit rate is not its own hits over its own denominator.",
  );
  expect(text).toContain(
    "The overview: hits and misses do not add up to the hit rate's denominator.",
  );
  expect(view().querySelector(".kpi")).toBeNull();
  expect(document.getElementById("freshness")?.textContent).toBe("Iris's capture is not shown");
  for (const label of ["Overview", "Findings", "Rules", "Evidence", "Results"]) {
    tab(label);
    expect(view().textContent).not.toMatch(/\d+(\.\d+)?%|1,000|\b120\b|\b700\b/);
    expect(view().textContent).toContain("Iris's capture is not shown.");
  }
});

it.each([
  [
    "a negative miss count",
    { cache_hits: 800, cache_misses: -100 },
    "The overview: cache_misses is not a count.",
  ],
  [
    "more hits than requests",
    { cache_hits: 900, cache_misses: 0, hit_rate_denominator: 900, requests: 700, hit_rate: 1 },
    "The overview: there are more hits than requests.",
  ],
  [
    "hits and misses not adding up",
    { cache_misses: 100 },
    "The overview: hits and misses do not add up to the hit rate's denominator.",
  ],
  [
    "a hit rate outside 0 to 1",
    { hit_rate: 1.2 },
    "The overview: the hit rate is not between 0 and 1.",
  ],
  [
    "a complete period with a missing day",
    { covered_days: 6 },
    "The overview: the period is marked complete, but not every requested day is covered.",
  ],
  [
    "more covered days than requested",
    { covered_days: 8, period_complete: false },
    "The overview: more days are covered than were requested.",
  ],
  ["a negative count", { requests: -1 }, "The overview: requests is not a count."],
  [
    "a negative response time",
    { latency: { response_time_ms: -5, label: "x" } },
    "The overview: the response time is not a duration.",
  ],
])("Q19: a capture with %s is refused", async (_, changes, named) => {
  await refusedCapture(withMetrics(changes));
  expect(view().textContent).toContain(named);
  expect(view().querySelector(".kpi")).toBeNull();
});

it("Q19: a finding or audit for another tenant, or a malformed rule summary, is refused", async () => {
  const state = makeState();
  state.audit.findings[0].tenant_id = "someone-else";
  await refusedCapture(state);
  expect(view().textContent).toContain(
    `Finding ${FINDING} is for a different tenant than the overview.`,
  );

  document.body.innerHTML = "";
  await refusedCapture(makeState({ audit: { ...makeState().audit, tenant_id: "someone-else" } }));
  expect(view().textContent).toContain("The audit is for a different tenant than the overview.");

  document.body.innerHTML = "";
  await refusedCapture(makeState({ rules: [null] }), "#rules");
  expect(view().textContent).toContain("A rule summary is malformed.");
});

it("Q19: a measured zero week is not a mismatch; it is drawn as zero", async () => {
  serve({
    state: () =>
      json(
        withMetrics({
          requests: 0,
          cache_hits: 0,
          cache_misses: 0,
          hit_rate: null,
          hit_rate_denominator: 0,
        }),
      ),
  });
  await mount();
  expect(view().textContent).not.toContain("not shown");
  expect(view().querySelectorAll(".kpi")).toHaveLength(4);
});

it("Q19: a partial capture (3 of 7 days, period not complete) is drawn, not refused", async () => {
  // The live tenant's shape: collect.py sets period_complete only when every
  // requested day is covered, so covered != requested is not a mismatch here.
  serve({
    state: () => json(withMetrics({ covered_days: 3, requested_days: 7, period_complete: false })),
  });
  await mount();
  expect(view().textContent).not.toContain("not shown");
  const tiles = [...view().querySelectorAll(".kpi")];
  expect(tiles).toHaveLength(4);
  const tile = (label: string) =>
    tiles.find((t) => t.querySelector(".label")?.textContent === label)!;
  expect(tile("Cache hit rate").querySelector(".value")?.textContent).toBe("80.0%");
  expect(tile("Cache hit rate").querySelector(".note")?.textContent).toBe(
    "560 hits over 700 requests",
  );
  expect(tile("Requests").querySelector(".value")?.textContent).toBe("700");
  expect(tile("Cache misses").querySelector(".value")?.textContent).toBe("140");
  expect(view().textContent).toContain("3 / 7 days");
  expect(view().textContent).toContain("Partial capture.");
});

it("Q19: an investigation whose periods disagree is not drawn on the Evidence tab", async () => {
  const state = makeState();
  Object.assign(state.investigation.current, { cache_hits: 120, requests: 1000 });
  serve({ state: () => json(state) });
  await mount("#evidence");
  expect(view().textContent).toContain("Iris's period comparison is not shown.");
  expect(view().textContent).toContain(
    "The current period: hits and misses do not add up to the hit rate's denominator.",
  );
  expect(screen.queryByRole("table", { name: "Previous and current period" })).toBeNull();
});

// ---------------------------------------------------- review follow-ups --

it("a review note survives a redraw of the page", async () => {
  await mount("#findings");
  const box = () => screen.getByRole("textbox", { name: `Review note for ${FINDING}` });
  fireEvent.input(box(), { target: { value: "Keep report-cache at 60 s." } });
  // Every turn ends in a redraw.
  ask("Anything else?");
  await waitFor(() => expect(messages().querySelector("li.agent")).not.toBeNull());
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(box()).toHaveValue("Keep report-cache at 60 s.");
  expect(box().closest("details")).toHaveAttribute("open");
});

it("with readiness failing, a capture for another tenant is not drawn", async () => {
  serve({
    readiness: () => detail("Iris is not bound here", 403),
    state: () =>
      json(makeState({ overview: { ...makeState().overview, tenant_id: "someone-else" } })),
  });
  start();
  await waitFor(() => expect(view().textContent).toContain("Iris's capture is not shown."));
  expect(view().textContent).toContain("This session's tenant could not be confirmed");
  expect(view().textContent).not.toContain("80.0%");
  expect(view().querySelector(".kpi")).toBeNull();
});

it("with the session's tenant unknown, Accounts offers no Open and says why", async () => {
  serve({ readiness: () => detail("Iris is not bound here", 403) });
  start("#accounts");
  const table = await screen.findByRole("table", { name: "Tenants in this account" });
  expect(within(table).queryAllByRole("button")).toEqual([]);
  expect(view().textContent).toContain("This session's tenant could not be confirmed");
});

it("collect mode: a stale capture whose refresh read nothing says so, not 'first overview'", async () => {
  serve({ refresh: captured("first_collect_produced_nothing", "refresh") }, "stale_capture");
  start("", "collect");
  await waitFor(() => expect(view().textContent).toContain("The last capture is out of date"));
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
  expect(screen.queryByRole("button", { name: "Collect the first overview" })).toBeNull();
  expect(within(view()).getByRole("button", { name: "Collect a fresh overview" })).toBeVisible();
  expect(messages().textContent).toContain(
    "The last capture is out of date and is not shown here.",
  );
  expect(messages().textContent).not.toContain("nothing to show yet");
});

it("missing hit and coverage counts read as not measured, once", async () => {
  serve({
    state: () =>
      json(
        withMetrics({
          cache_hits: null,
          cache_misses: null,
          hit_rate: null,
          hit_rate_denominator: null,
          requests: null,
          covered_days: null,
          requested_days: null,
          period_complete: false,
        }),
      ),
  });
  await mount();
  expect(view().textContent).toContain("not measured in this capture");
  expect(view().textContent).toContain("Coverage not measured");
  expect(view().textContent).not.toContain("Not measured hits over Not measured requests");
  expect(view().textContent).not.toContain("Not measured / Not measured days");
});

// ------------------------------------------- QA 2026-09-26 F1 to F4 --

it("F1: the readiness line follows the first collection without a reload", async () => {
  let completed = false;
  serve(
    {
      readiness: () =>
        json({
          ...(ADAPTER.fresh_session.readiness.body as Record<string, unknown>),
          turn_completed_here: completed,
        }),
      refresh: () => {
        completed = true;
        return replay(ADAPTER.captured.refresh);
      },
    },
    "fresh_session",
  );
  start();
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  await waitFor(() =>
    expect(document.getElementById("host-status")?.textContent).toContain(
      "Iris has completed a turn in this session",
    ),
  );
  expect(document.getElementById("host-details")).not.toBeVisible();
});

it("F2: while the page loads it says so, and offers no first collection over a capture", async () => {
  const held = deferred<Response>();
  serve({ items: () => held.promise });
  start();
  expect(view().textContent).not.toContain("has not collected");
  expect(screen.queryByRole("button", { name: "Collect the first overview" })).toBeNull();
  expect(view().textContent).toContain("Reading this session");
  expect(document.getElementById("freshness")?.textContent).not.toContain("has not read");
  held.resolve(json({ data: [], has_more: false }));
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
});

/** An overview of the week before FULL's, as a comparison turn leaves it. */
function earlierWindow(): State {
  const earlier = makeState();
  earlier.overview = {
    ...earlier.overview,
    metrics: {
      ...earlier.overview.metrics,
      ...structuredClone(FULL.investigation.previous),
    },
  };
  return earlier;
}

it("F4: the coverage strip shows each day of the period once", async () => {
  const state = makeState();
  const summaries = state.overview.evidence.filter((e) => e.source_tool === "get_summary");
  state.overview.evidence = [
    ...state.overview.evidence,
    // The same days read again later in the session, and days outside it.
    ...structuredClone(summaries).map((e, i) => ({ ...e, id: `again-${i}` })),
    { ...structuredClone(summaries[0]), id: "before", start_date: "2026-09-12" } as never,
    { ...structuredClone(summaries[0]), id: "after", start_date: "2026-09-26" } as never,
  ];
  serve({ state: () => json(state) });
  await mount();
  const days = [...view().querySelectorAll(".coverage .day")].map((d) => d.textContent);
  expect(days).toEqual(["09-19", "09-20", "09-21", "09-22", "09-23", "09-24", "09-25"]);
  expect(view().textContent).toContain("7 / 7 days");
});

it("F4: an overview Iris read for an earlier window does not replace the current period", async () => {
  let current = true;
  serve({ state: () => json(current ? makeState() : earlierWindow()) });
  await mount();
  expect(view().textContent).toContain("2026-09-19 to 2026-09-26");
  current = false;
  ask("Compare with the previous week.");
  await waitFor(() => expect(calls("state").length).toBeGreaterThanOrEqual(2));
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(view().textContent).toContain("2026-09-19 to 2026-09-26");
  expect(view().textContent).not.toContain("2026-09-12 to 2026-09-19");
});

it("F4: on a load where the newest overview is an earlier window, it is labelled, not passed off as current", async () => {
  serve({ state: () => json(earlierWindow()) });
  await mount();
  expect(view().textContent).toContain("2026-09-12 to 2026-09-19");
  expect(view().textContent).toContain("not this tenant's current period");
});

/** Text nodes directly under an element that read "null" or "undefined". */
function strayText(root: Element) {
  return [...root.querySelectorAll("*")]
    .concat(root)
    .flatMap((e) => [...e.childNodes])
    .filter((n) => n.nodeType === Node.TEXT_NODE && /^\s*(null|undefined)\s*$/.test(n.textContent!))
    .map((n) => n.textContent);
}

it("F4 (review R1): a normal Overview has no stray 'null' text", async () => {
  await mount();
  expect(view().textContent).toContain("2026-09-19 to 2026-09-26");
  expect(view().textContent).not.toContain("earlier window");
  expect(strayText(view())).toEqual([]);
  expect(view().textContent).not.toMatch(/\bnull\b/);
});

it("F4 (review N5): a partial current capture (3 of 7 days) is not labelled an earlier window", async () => {
  serve({
    state: () => json(withMetrics({ covered_days: 3, requested_days: 7, period_complete: false })),
  });
  await mount();
  expect(view().textContent).toContain("3 / 7 days");
  expect(view().textContent).toContain("Partial capture.");
  expect(view().textContent).not.toContain("earlier window");
  expect(view().textContent).not.toContain("not this tenant's current period");
  expect(strayText(view())).toEqual([]);
});

it("B4 (review N1): a turn that ends while the page reads its history still shows its answer", async () => {
  items = [userItem("u1", "How is the cache?")];
  sessionStatus = "running";
  let reads = 0;
  serve({
    items: () => {
      const page = json({ data: items.slice().reverse(), has_more: false });
      // The turn ends just after the history read: first read has only the question.
      if ((reads += 1) === 1) {
        items = [...items, assistantItem("a1", "It ended in between.")];
        sessionStatus = "idle";
      }
      return page;
    },
  });
  start();
  await waitFor(() =>
    expect(messages().querySelector("li.agent")?.textContent).toBe("It ended in between."),
  );
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(messages().querySelector("[role=status]")).toBeNull();
});

// ------------------------------------------------ QA re-walk 2026-09-26 N2 --
//
// iris_overview narrows a partial capture's dates to the days it covered: a
// budget-cut read of the current period 09-19..09-26 comes back as 09-19..09-22
// (or 09-23..09-26 when the first days are missing). That is the current
// period, partially covered. The test above keeps the full period dates; these
// use the tool's real shape.

const PARTIAL = { covered_days: 3, requested_days: 7, period_complete: false };

it.each([
  ["missing its last days", "2026-09-19", "2026-09-22"],
  ["missing its first days", "2026-09-23", "2026-09-26"],
  ["cut short by the budget, UTC-dated", "2026-09-20", "2026-09-22"],
])(
  "N2: a partial capture with the tool's narrowed dates (%s) is the current period, not an earlier window",
  async (_name, start_date, end_date) => {
    serve({ state: () => json(withMetrics({ ...PARTIAL, start_date, end_date })) });
    await mount();
    expect(view().textContent).toContain(`${start_date} to ${end_date}`);
    expect(view().textContent).toContain("3 / 7 days");
    expect(view().textContent).toContain("Partial capture.");
    expect(view().textContent).toContain("80.0%");
    expect(view().textContent).not.toContain("earlier window");
    expect(view().textContent).not.toContain("not this tenant's current period");
    expect(strayText(view())).toEqual([]);
  },
);

it("N2: an earlier window is still labelled when its own dates are narrowed", async () => {
  const earlier = earlierWindow();
  Object.assign(earlier.overview.metrics, {
    ...PARTIAL,
    start_date: "2026-09-15",
    end_date: "2026-09-19",
  });
  serve({ state: () => json(earlier) });
  await mount();
  expect(view().textContent).toContain("not this tenant's current period");
});

it("N2: the live refresh of a stale capture takes a narrowed partial capture, and drops the stale label", async () => {
  const stale = ADAPTER.stale_capture.state.body as State;
  const fresh = structuredClone(ADAPTER.stale_capture.refresh.body) as State;
  Object.assign(fresh.overview.metrics, { ...PARTIAL, end_date: "2026-09-22" });
  const held = deferred<Response>();
  serve({ state: () => json(stale), refresh: () => held.promise });
  start();
  await waitFor(() => expect(view().textContent).toContain("2026-09-19 to 2026-09-26"));
  await waitFor(() => expect(calls("refresh")).toHaveLength(1));
  held.resolve(json(fresh));
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).not.toContain("may be out of date"),
  );
  expect(view().textContent).toContain("2026-09-19 to 2026-09-22");
  expect(view().textContent).toContain("3 / 7 days");
  expect(view().textContent).not.toContain("2026-09-19 to 2026-09-26");
  expect(view().textContent).not.toContain("earlier window");
});

// -------------------------------------- one automatic collection per session --
//
// QA re-walk N1: every open of a session with no current overview collected
// again, which spent the session's 60-call tool budget, and on a live tenant
// each one is a warehouse read. The page now collects on its own at most once
// per session and then offers "Collect now".

const collectTurn = (id: string, created_at?: number) => ({
  ...userItem(
    id,
    "Call iris_overview and iris_audit for the selected tenant. Do not propose changes.",
  ),
  ...(created_at === undefined ? {} : { created_at }),
});

it("a session whose record already holds a collect turn does not collect on open; Collect now does", async () => {
  items = [collectTurn("u0", CAPTURED - 600), assistantItem("a0", "No overview this time.")];
  let collected = false;
  serve(
    {
      state: () => (collected ? json(makeState()) : detail("No current-period overview yet", 409)),
      refresh: () => {
        collected = true;
        return json(makeState());
      },
    },
    "fresh_session",
  );
  start();
  await waitFor(() => expect(view().textContent).toContain("No current overview yet"));
  expect(view().textContent).toContain("only once per session");
  await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  expect(calls("refresh")).toHaveLength(0);
  fireEvent.click(within(view()).getByRole("button", { name: "Collect now" }));
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  expect(calls("refresh")).toHaveLength(1);
});

it("a reload in the same tab after a failed automatic collection does not collect again", async () => {
  serve({ refresh: captured("first_collect_produced_nothing", "refresh") }, "fresh_session");
  start();
  await waitFor(() =>
    expect(view().textContent).toContain("Iris has not collected an overview here yet"),
  );
  expect(calls("refresh")).toHaveLength(1);
  expect(within(view()).getByRole("button", { name: "Collect now" })).toBeVisible();
  // The record did not get the turn (the mock keeps no items): the tab's own
  // marker still holds.
  const reload = async () => {
    document.body.innerHTML = "";
    start();
    await waitFor(() => expect(view().textContent).toContain("No current overview yet"));
    await waitFor(() => expect(document.getElementById("send")).not.toBeDisabled());
  };
  await reload();
  await reload();
  await reload();
  expect(calls("refresh")).toHaveLength(1);
  fireEvent.click(within(view()).getByRole("button", { name: "Collect now" }));
  await waitFor(() => expect(calls("refresh")).toHaveLength(2));
});

it("a stale capture with a collect turn after it is shown labelled, and not refreshed again on open", async () => {
  const stale = ADAPTER.stale_capture.state.body as State;
  items = [collectTurn("u9", stale.captured_at + 300)];
  serve({}, "stale_capture");
  start();
  await waitFor(() => expect(view().textContent).toContain("80.0%"));
  await waitFor(() => expect(document.getElementById("refresh")).not.toBeDisabled());
  expect(document.getElementById("freshness")?.textContent).toContain("may be out of date");
  expect(document.getElementById("notice")?.textContent).toContain("only once per session");
  expect(calls("refresh")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Collect a fresh overview" }));
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).not.toContain("may be out of date"),
  );
  expect(calls("refresh")).toHaveLength(1);
  expect(document.getElementById("notice")).not.toBeVisible();
});

it("a stale capture whose only collect turn produced it is still refreshed once in the background", async () => {
  const stale = ADAPTER.stale_capture.state.body as State;
  items = [collectTurn("u0", stale.captured_at - 60)];
  serve({}, "stale_capture");
  start();
  await waitFor(() => expect(calls("refresh")).toHaveLength(1));
  await waitFor(() =>
    expect(document.getElementById("freshness")?.textContent).not.toContain("may be out of date"),
  );
});

// --------------------------------------------- QA re-walk minor items --

it("a finding's Python list reads as its items, as text", async () => {
  const state = makeState();
  state.audit.findings[0] = {
    ...state.audit.findings[0],
    explanation:
      "Rule report-cache caches ['[literal]'], for which no sensitivity classification was supplied.",
    next_step: "Check ['orders', \"o'brien\", '<b>x</b>'] first.",
  };
  serve({ state: () => json(state) });
  await mount("#findings");
  const card = view().querySelector(`[data-finding-id="${state.audit.findings[0].id}"]`)!;
  expect(card.textContent).toContain(
    "Rule report-cache caches [literal], for which no sensitivity classification was supplied.",
  );
  expect(card.textContent).toContain("Next: Check orders, o'brien and <b>x</b> first.");
  expect(card.textContent).not.toContain("['");
  expect([...card.querySelectorAll("code")].map((c) => c.textContent)).toEqual([
    "[literal]",
    "orders",
    "o'brien",
    "<b>x</b>",
  ]);
  expect(card.querySelector("b")).toBeNull();
});

it("on a phone the Accounts table stacks, each cell named by its column", async () => {
  await mount("#accounts");
  await waitFor(() => expect(view().querySelector("table.accounts tbody tr")).not.toBeNull());
  const rows = [...view().querySelectorAll("table.accounts tbody tr")];
  const production = rows.find((r) => r.textContent?.includes("Production"))!;
  expect([...production.querySelectorAll("td")].map((td) => td.dataset.label ?? "")).toEqual([
    "",
    "Hit rate",
    "Cache misses",
    "Requests",
    "Coverage",
    "Captured",
    "",
  ]);
  const css = readFileSync(join(UI, "style.css"), "utf8");
  const phone = /@media \(max-width: 560px\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
  expect(phone).toMatch(/table\.accounts td \{[^}]*display: block/);
  expect(phone).toMatch(/table\.accounts thead \{[^}]*display: none/);
  expect(phone).toMatch(/td\[data-label\]::before \{[^}]*content: attr\(data-label\)/);
});
