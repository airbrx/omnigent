// The adapter the Omnigent server injects into the pinned Iris workspace
// (`omnigent/airbrx/iris/host.js`). It is plain DOM, not a module, and it is
// not part of the web bundle — so it is loaded from disk and evaluated
// against a stand-in for the packaged page's own markup.
//
// It is tested here rather than not at all because the behaviour it exists
// for is a claim about honesty: when this host refuses a turn, the workspace
// must say the host refused and why, and must NOT print the sentence the
// packaged page computes from the captured report and renders in Iris's
// voice. That is precisely the kind of thing that looks fine by eye and is
// wrong in the one case anyone cares about.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, expect, it, vi } from "vitest";

const adapterSource = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../../../omnigent/airbrx/iris/host.js"),
  "utf8",
);

const SESSION_PATH = "/v1/iris/sessions/owned-session/ui/";

/** The parts of the packaged page this adapter reaches for. */
function packagedPage() {
  document.body.innerHTML = `
    <div class="shell">
      <div class="chat" id="chat"><div class="message assistant">Start with a skill below.</div></div>
    </div>`;
}

/** Evaluate the adapter, then fire the load event it waits for. */
function mountAdapter() {
  // Indirect eval: the adapter is an IIFE written for a browser <script>, and
  // this runs it against the same jsdom globals a real page would give it.
  (0, eval)(adapterSource);
  window.dispatchEvent(new Event("DOMContentLoaded"));
}

/** What the packaged page does on a failed turn, reduced to its essentials. */
function pageFallback(text: string) {
  const div = document.createElement("div");
  div.className = "message assistant";
  div.textContent = text;
  document.querySelector("#chat")?.append(div);
  return div;
}

let realFetch: typeof window.fetch;

beforeEach(() => {
  realFetch = window.fetch;
  window.history.replaceState({}, "", `${SESSION_PATH}?theme=dark`);
  packagedPage();
});

afterEach(() => {
  window.fetch = realFetch;
  vi.restoreAllMocks();
});

it("states plainly that no turn has ever completed in this session", async () => {
  window.fetch = vi.fn(async () =>
    Response.json({
      tenant_id: "fixture-tenant",
      fixture: true,
      turn_completed_here: false,
      last_task_failed: false,
      verified: ["Iris is a registered agent on this host"],
      unverified: [
        "no turn has completed in this session, so whether the execution host can reach the model is unknown",
      ],
    }),
  ) as never;
  mountAdapter();
  await vi.waitFor(() =>
    expect(document.querySelector(".host-status")?.textContent).toBe(
      "No turn has ever completed in this session.",
    ),
  );
  expect(document.querySelector(".host-bar")?.textContent).toContain(
    "Not verified: no turn has completed in this session",
  );
  expect(document.querySelector(".host-status")).toHaveAttribute("data-state", "unverified");
});

it("names the host's refusal instead of letting the page answer in Iris's voice", async () => {
  window.fetch = vi.fn(async (input: RequestInfo | URL) =>
    String(input).includes("readiness")
      ? Response.json({ turn_completed_here: false, verified: [], unverified: [] })
      : Response.json(
          { detail: "Iris is busy; cancel or wait for the current turn" },
          { status: 409 },
        ),
  ) as never;
  mountAdapter();

  // The page's own sequence: ask the host, fail, then print its snapshot
  // sentence. The adapter has to be the thing that decides what that bubble
  // ends up saying.
  const response = await window.fetch("api/chat", { method: "POST", body: "{}" });
  expect(response.status).toBe(409);
  const fallback = pageFallback(
    "The host could not complete a verified turn. Snapshot answer — 42% of queries hit cache.",
  );

  await vi.waitFor(() => expect(fallback.dataset.hostRefusal).toBe("409"));
  expect(fallback.textContent).toBe(
    "Iris did not answer. This host refused the turn: Iris is busy; cancel or wait for" +
      " the current turn (HTTP 409). Nothing has been computed in her place. Open native" +
      " chat to see the session's own record of what happened.",
  );
  expect(fallback.textContent).not.toContain("Snapshot answer");
  expect(fallback.textContent).not.toContain("42%");
});

it("leaves a successful turn's answer exactly as the page wrote it", async () => {
  window.fetch = vi.fn(async (input: RequestInfo | URL) =>
    String(input).includes("readiness")
      ? Response.json({ turn_completed_here: true, verified: [], unverified: [] })
      : Response.json({ text: "The denominator is 700 queries.", tools: ["iris_overview"] }),
  ) as never;
  mountAdapter();
  await window.fetch("api/chat", { method: "POST", body: "{}" });
  const answer = pageFallback("The denominator is 700 queries.");
  // One macrotask: long enough for the adapter to have rewritten this bubble
  // if it were ever going to, which for a turn that succeeded it must not.
  await new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
  expect(answer.textContent).toBe("The denominator is 700 queries.");
  expect(answer.dataset.hostRefusal).toBeUndefined();
  await vi.waitFor(() =>
    expect(document.querySelector(".host-status")?.textContent).toBe(
      "A turn has completed in this session, so hosted turns work here.",
    ),
  );
});

it("rewrites the development bridge's recollect GET onto the hosted POST", async () => {
  const calls: [string, string | undefined][] = [];
  window.fetch = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    calls.push([String(input), options?.method]);
    return Response.json({ turn_completed_here: true, verified: [], unverified: [] });
  }) as never;
  mountAdapter();
  await window.fetch("api/state?fresh=1");
  expect(calls).toContainEqual(["api/refresh", "POST"]);
  expect(calls.map(([url]) => url)).not.toContain("api/state?fresh=1");
});

it("reports a refused readiness check rather than showing a hopeful blank", async () => {
  window.fetch = vi.fn(async () =>
    Response.json({ detail: "Iris requires session edit permission" }, { status: 403 }),
  ) as never;
  mountAdapter();
  await vi.waitFor(() =>
    expect(document.querySelector(".host-status")?.textContent).toBe(
      "This host will not run turns in this session: Iris requires session edit permission",
    ),
  );
  expect(document.querySelector(".host-status")).toHaveAttribute("data-state", "refused");
});

it("offers native chat for the same session, so the two views are one conversation", () => {
  window.fetch = vi.fn(async () => Response.json({})) as never;
  mountAdapter();
  const link = document.querySelector<HTMLAnchorElement>(".host-bar a");
  expect(link?.getAttribute("href")).toBe("/c/owned-session");
  expect(link?.target).toBe("_top");
});
