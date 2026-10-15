// Tally's framed workspace (`omnigent/airbrx/tally/ui/`). It is plain DOM, not
// part of the web bundle, so its markup and script are read from disk and run
// against jsdom, as evaWorkspaceApp.test.ts runs Eva's app.
//
// These cover Refresh while her runner is still starting: after a deploy or
// idle it takes about two minutes to reconnect, and the adapter answers 502 or
// 503 until it does.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const UI = join(dirname(fileURLToPath(import.meta.url)), "../../../omnigent/airbrx/tally/ui");
const html = readFileSync(join(UI, "index.html"), "utf8");
const script = readFileSync(join(UI, "app.js"), "utf8");
const BODY = /<body>([\s\S]*)<script src="app\.js"><\/script>/.exec(html)?.[1] ?? "";

// A state as the adapter's refresh answers it, only as full as rendering needs.
const STATE = { empty: false, refreshed_at: Date.now() / 1000, stale: false, errors: [] };

type Route = (init?: RequestInit) => Response | Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;
const listeners: [string, EventListener][] = [];
const realAdd = window.addEventListener.bind(window);

function serve(overrides: Record<string, Route> = {}) {
  fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).startsWith("/v1/sessions/s1/items")
      ? "items"
      : String(input).replace(/^api\//, "");
    if (path in overrides) return overrides[path](init);
    if (path === "items") return Response.json({ data: [], has_more: false });
    if (path === "readiness")
      return Response.json({ turn_completed_here: false, last_task_failed: false });
    if (path === "state")
      return Response.json({ detail: "No workspace state yet; use Refresh" }, { status: 409 });
    if (path === "cancel") return Response.json({});
    throw new Error(`unexpected fetch ${input}`);
  });
  window.fetch = fetchMock as never;
}

const unavailable = () =>
  Response.json({ detail: "Native session operation failed" }, { status: 503 });

/** Run the app and wait for its first load, so no load outlives the test. */
async function mount() {
  window.history.replaceState(null, "", "/v1/tally/sessions/s1/ui/");
  document.body.innerHTML = BODY;
  document.body.dataset.pollMs = "10";
  // A 10ms step keeps the whole retry window (36 steps) well inside a test.
  document.body.dataset.retryMs = "10";
  // Indirect eval: the app is an IIFE written for a browser <script>.
  (0, eval)(script);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "api/state")).toBe(true));
  await waitFor(() => expect(screen.getByRole("button", { name: /refresh/i })).toBeEnabled());
}

/** Long enough for a retry at the 10ms step to have been made, were one coming. */
const settle = () =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, 50);
  });

const refreshCalls = () => fetchMock.mock.calls.filter(([url]) => url === "api/refresh").length;
const messages = () => document.getElementById("messages") as HTMLElement;

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

it("a refresh that finds her runner starting says so, retries, and succeeds", async () => {
  let calls = 0;
  let sawStarting = false;
  serve({
    refresh: () => {
      calls += 1;
      if (calls > 1 && messages().textContent?.includes("Tally is starting up… retrying"))
        sawStarting = true;
      return calls === 1 ? unavailable() : Response.json(STATE);
    },
  });
  await mount();
  fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await waitFor(() =>
    expect(messages()).toHaveTextContent("Refreshed: Tally has read the portal."),
  );
  expect(refreshCalls()).toBe(2);
  expect(sawStarting).toBe(true);
  expect(messages()).not.toHaveTextContent("could not be reached");
  expect(within(messages()).queryByRole("button", { name: "Try again" })).toBeNull();
});

it("a runner that never comes up gives up with the plain message and Try again", async () => {
  serve({ refresh: unavailable });
  await mount();
  fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
  await waitFor(
    () =>
      expect(messages()).toHaveTextContent(
        "Tally could not be reached just now. Her runner may be offline or restarting.",
      ),
    { timeout: 3000 },
  );
  // 10, 20, 30, 40, 50, 60, 60, 60ms waits fit the 360ms window; the next does not.
  expect(refreshCalls()).toBe(9);
  expect(within(messages()).getByRole("button", { name: "Try again" })).toBeEnabled();
  expect(messages()).not.toHaveTextContent("Tally is starting up");
});

it.each([
  [401, "Tally requires host authentication", "Your Omnigent sign-in has expired."],
  [504, "Tally's turn timed out and was cancelled", "Tally took too long"],
])(
  "a %i is an answer, not a runner starting, and is not retried",
  async (status, detail, shown) => {
    serve({ refresh: () => Response.json({ detail }, { status }) });
    await mount();
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(messages()).toHaveTextContent(shown));
    await settle();
    expect(refreshCalls()).toBe(1);
    expect(messages()).not.toHaveTextContent("Tally is starting up");
  },
);

it("a failed chat turn is never resent on its own, since it may have been posted", async () => {
  serve({ chat: unavailable });
  await mount();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "How are costs?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(messages()).toHaveTextContent("Tally could not be reached just now."));
  await settle();
  expect(fetchMock.mock.calls.filter(([url]) => url === "api/chat")).toHaveLength(1);
});
