// Eva's framed workspace (`omnigent/airbrx/eva/ui/`). It is plain DOM, not part
// of the web bundle, so its markup and script are read from disk and run
// against jsdom, the same way irisHostAdapter.test.tsx runs Iris's adapter.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const UI = join(dirname(fileURLToPath(import.meta.url)), "../../../omnigent/airbrx/eva/ui");
const html = readFileSync(join(UI, "index.html"), "utf8");
const script = readFileSync(join(UI, "app.js"), "utf8");
const BODY = /<body>([\s\S]*)<script src="app\.js"><\/script>/.exec(html)?.[1] ?? "";

const TABS = [
  ["Leads", "/eva/app/leads"],
  ["Pool", "/eva/app/leads/pool"],
  ["Accounts", "/eva/app/accounts"],
  ["Analytics", "/eva/app/analytics"],
  ["Guardrails", "/eva/app/guardrails"],
  ["Sync", "/eva/app/sync"],
  ["Settings", "/eva/app/settings/token"],
];

/** A refresh's answer: the state itself, in the adapter's shape. */
const STATE = {
  pool: { total: 1, leads: [{ lead_id: "L1", name: "Pat Example", company: "Example Co" }] },
  mine: { total: 0, leads: [] },
  leads: {},
  drafts: [],
  refreshed_at: 1790000000,
  errors: [],
  empty: false,
  stale: false,
  cache_age_seconds: 5,
  outreach_url: "/eva/app",
};

const READY = { verified: [], unverified: [], turn_completed_here: true, last_task_failed: false };
type Route = (init?: RequestInit) => Response | Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;
const listeners: [string, EventListener][] = [];
const realAdd = window.addEventListener.bind(window);

/** Answer the app's relative `api/<path>` calls; overrides win. */
function serve(overrides: Record<string, Route> = {}) {
  fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).replace(/^api\//, "");
    if (path in overrides) return overrides[path](init);
    if (path === "readiness") return Response.json(READY);
    // The adapter answers 409 until there is something to show, as Iris's does.
    if (path === "state")
      return Response.json({ detail: "No workspace state yet; use Refresh" }, { status: 409 });
    if (path === "chat")
      return Response.json({ text: "Two leads are due today.", tools: [], failed: null });
    if (path === "cancel") return Response.json({});
    throw new Error(`unexpected fetch ${input}`);
  });
  window.fetch = fetchMock as never;
}

/** Run the app and wait for its first render, so no load outlives the test. */
async function mount(hash = "") {
  window.history.replaceState(null, "", `/v1/eva/sessions/s1/ui/${hash}`);
  document.body.innerHTML = BODY;
  // Indirect eval: the app is an IIFE written for a browser <script>.
  (0, eval)(script);
  await waitFor(() => expect(document.getElementById("freshness")?.textContent).not.toBe(""));
}

function chatBodies() {
  return fetchMock.mock.calls
    .filter(([url]) => url === "api/chat")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

function ask(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Message Eva" }), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

beforeEach(() => {
  localStorage.clear();
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

it("renders the tabs from the one tab list, Chat first and selected", async () => {
  await mount();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "Chat",
    ...TABS.map(([label]) => label),
  ]);
  expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
  expect(document.querySelectorAll("iframe")).toHaveLength(0);
});

it.each(TABS)("the %s tab frames %s", async (label, path) => {
  await mount();
  fireEvent.click(screen.getByRole("tab", { name: label }));
  expect(screen.getByRole("tab", { name: label })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTitle(`Eva ${label}`)).toHaveAttribute("src", path);
  expect(document.getElementById("home")).not.toBeVisible();
  // Freshness and refresh describe the pipeline, which only the Chat tab shows.
  expect(document.getElementById("actions")).not.toBeVisible();
  expect(window.location.hash).toBe(`#${label.toLowerCase()}`);
});

it("switching tabs keeps earlier pages mounted and shows only the open one", async () => {
  await mount();
  fireEvent.click(screen.getByRole("tab", { name: "Leads" }));
  fireEvent.click(screen.getByRole("tab", { name: "Pool" }));
  expect(screen.getByTitle("Eva Leads")).not.toBeVisible();
  expect(screen.getByTitle("Eva Pool")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Leads" }));
  expect(screen.getByTitle("Eva Leads")).toBeVisible();
  expect(document.querySelectorAll("iframe")).toHaveLength(2);
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(document.getElementById("home")).toBeVisible();
  expect(document.getElementById("frames")).not.toBeVisible();
});

it("opens on the tab the URL names", async () => {
  await mount("#guardrails");
  expect(screen.getByRole("tab", { name: "Guardrails" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTitle("Eva Guardrails")).toHaveAttribute("src", "/eva/app/guardrails");
});

it("docks the chat beside a management tab; it collapses and comes back", async () => {
  await mount();
  const chat = screen.getByRole("region", { name: "Chat with Eva" });
  // On the Chat tab the chat is the point, so there is nothing to collapse.
  expect(document.getElementById("collapse")).not.toBeVisible();

  fireEvent.click(screen.getByRole("tab", { name: "Leads" }));
  expect(chat).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
  expect(chat).not.toBeVisible();
  expect(localStorage.getItem("eva.dockCollapsed")).toBe("1");

  fireEvent.click(screen.getByRole("button", { name: /Ask Eva/ }));
  expect(chat).toBeVisible();
  expect(localStorage.getItem("eva.dockCollapsed")).toBe("0");
});

it("a collapsed dock stays collapsed on the next visit, and never hides the Chat tab", async () => {
  localStorage.setItem("eva.dockCollapsed", "1");
  await mount("#leads");
  expect(document.getElementById("chat")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(screen.getByRole("region", { name: "Chat with Eva" })).toBeVisible();
});

it("sends a question through the adapter and shows Eva's answer", async () => {
  await mount();
  ask("What is due today?");
  expect(await screen.findByText("Two leads are due today.")).toBeInTheDocument();
  expect(chatBodies()[0]).toEqual({ history: [{ role: "user", content: "What is due today?" }] });
});

it("docked beside a tab, the question says what the rep is looking at", async () => {
  await mount();
  fireEvent.click(screen.getByRole("tab", { name: "Leads" }));
  expect(screen.getByText("About Leads")).toBeVisible();
  ask("Is this one qualified?");
  await screen.findByText("Two leads are due today.");
  const [turn] = chatBodies()[0].history;
  expect(turn.content).toContain("Is this one qualified?");
  expect(turn.content).toContain("I am looking at Leads, /eva/app/leads");
});

it("the context can be left out of a question", async () => {
  await mount();
  fireEvent.click(screen.getByRole("tab", { name: "Pool" }));
  fireEvent.click(screen.getByRole("button", { name: "Don't include this page" }));
  ask("Hello");
  await screen.findByText("Two leads are due today.");
  expect(chatBodies()[0].history).toEqual([{ role: "user", content: "Hello" }]);
});

it("the chat is one conversation across tabs", async () => {
  await mount();
  ask("First");
  await screen.findByText("Two leads are due today.");
  fireEvent.click(screen.getByRole("tab", { name: "Accounts" }));
  fireEvent.click(screen.getByRole("button", { name: "Don't include this page" }));
  ask("Second");
  await waitFor(() => expect(chatBodies()).toHaveLength(2));
  expect(chatBodies()[1].history.map((t: { role: string }) => t.role)).toEqual([
    "user",
    "assistant",
    "user",
  ]);
  expect(within(document.getElementById("messages")!).getByText("First")).toBeInTheDocument();
});

it("a failed turn shows the adapter's reason and is not resent", async () => {
  serve({
    chat: () =>
      Response.json(
        { detail: "Eva is busy; cancel or wait for the current turn" },
        { status: 409 },
      ),
  });
  await mount();
  ask("One");
  expect(await screen.findByText(/Eva is busy/)).toBeInTheDocument();
  serve();
  ask("Two");
  await screen.findByText("Two leads are due today.");
  expect(chatBodies()[0].history).toEqual([{ role: "user", content: "Two" }]);
});

it("Stop interrupts the running turn", async () => {
  let answer: (r: Response) => void = () => {};
  serve({
    chat: () =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
  });
  await mount();
  ask("Long one");
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => url === "api/cancel")).toBe(true),
  );
  answer(Response.json({ detail: "Eva's turn was cancelled" }, { status: 409 }));
  expect(await screen.findByText(/cancelled/)).toBeInTheDocument();
});

it("an empty session says so rather than calling it an error", async () => {
  await mount();
  expect(screen.getByText("Nothing loaded yet")).toBeInTheDocument();
  expect(document.getElementById("freshness")).toHaveTextContent(
    "Eva has not read the pipeline yet",
  );
  expect(document.getElementById("notice")).not.toBeVisible();
});

it("Refresh shows the state the adapter answers with", async () => {
  serve({ refresh: () => Response.json(STATE) });
  await mount();
  fireEvent.click(screen.getByRole("button", { name: "Refresh pipeline" }));
  expect(await screen.findByText("Pat Example")).toBeInTheDocument();
  expect(document.getElementById("kpi-pool")).toHaveTextContent("1");
  expect(document.getElementById("freshness")).toHaveTextContent("Eva read the pipeline");
});

it("a lead's outreach link opens in the Leads tab, beside Eva", async () => {
  serve({ state: () => Response.json(STATE) });
  await mount();
  fireEvent.click(await screen.findByText("Pat Example"));
  fireEvent.click(screen.getByRole("link", { name: "Open in outreach app" }));
  expect(screen.getByRole("tab", { name: "Leads" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTitle("Eva Leads")).toHaveAttribute("src", "/eva/app/leads/L1");
  expect(screen.getByRole("region", { name: "Chat with Eva" })).toBeVisible();
});
