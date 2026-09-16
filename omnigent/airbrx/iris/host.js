// Host adapter for the unchanged Iris workspace assets.
//
// The packaged UI is pinned and served byte for byte — package.py verifies the
// archive's digest before a single file leaves it — so everything the hosted
// mount needs that the page does not already do lives here, in the one script
// the server injects ahead of the page's own. Four jobs:
//
//   1. Translate the page's development-bridge calls onto the hosted routes.
//   2. Say plainly what this host has and has not verified about running a
//      real turn in this session.
//   3. Never let a host refusal be answered by a sentence computed on this
//      page in the assistant's voice. The page's own fallback does exactly
//      that, and a review of a security tool that answers when it could not
//      ask is the review nobody survives.
//   4. Make "system" mean the Omnigent theme, because in a drawer the host is
//      the system.
//
// Plain DOM throughout: no framework reaches this file, and the packaged
// stylesheet already styles button, a and :focus-visible, so the controls
// below inherit the workspace's own light and dark treatment.
(() => {
  const nativeFetch = window.fetch.bind(window);
  const params = new URLSearchParams(location.search);

  // ---------------------------------------------------------------- theme --
  // theme.js runs after this script and treats "system" as the OS preference.
  // Answer that one media query from the theme the shell passed in and pass
  // every other query through untouched. Only JavaScript callers see this;
  // the stylesheet's own width and colour queries are unaffected.
  const allowedThemes = ["light", "dark"];
  let hostTheme = allowedThemes.includes(params.get("theme")) ? params.get("theme") : null;
  if (hostTheme) {
    const realMatchMedia = window.matchMedia.bind(window);
    window.matchMedia = (query) => {
      if (!/prefers-color-scheme/i.test(String(query))) return realMatchMedia(query);
      return {
        media: String(query),
        matches: /dark/i.test(String(query)) === (hostTheme === "dark"),
        onchange: null,
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        dispatchEvent: () => false,
      };
    };
  }
  // The shell toggles its theme without remounting the iframe, because a new
  // src would reload the page and throw away the conversation in it. An
  // explicit pick inside the workspace still wins: that is this page's setting,
  // not the host's.
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    const next = event.data && event.data.irisHostTheme;
    if (!allowedThemes.includes(next)) return;
    hostTheme = next;
    let chosen = "system";
    try {
      chosen = localStorage.getItem("iris.theme") || "system";
    } catch {
      // Storage may be disabled for an embedded document; "system" is the
      // documented default, so following the host is the right reading.
    }
    if (chosen === "system") document.documentElement.dataset.theme = next;
  });

  // ------------------------------------------------------------- requests --
  // Routes this adapter is accountable for. A failure on any of them is a
  // failure of the host to do the thing the page just asked for, and must be
  // reported as that rather than smoothed over.
  const HOSTED_API = /(?:^|\/)api\/(state|chat|refresh|cancel)(?:$|\?)/;
  // Armed the moment a hosted call refuses, disarmed one task later. The
  // page's catch block runs in the microtask that follows the rejected fetch,
  // so anything it writes into the transcript lands inside this window and
  // nothing else does.
  let pendingRefusal = null;
  let lastRefusal = null;

  async function refusalOf(response) {
    // FastAPI puts the reason in `detail`; the development bridge used `error`.
    try {
      const body = await response.clone().json();
      const detail = body && (body.detail || body.error);
      if (typeof detail === "string" && detail.trim()) return detail.trim();
    } catch {
      // A non-JSON body is not a reason, and inventing one would be the same
      // dishonesty this file exists to prevent. Fall through to the status.
    }
    return `the host returned ${response.status} without a reason`;
  }

  function arm(refusal) {
    pendingRefusal = refusal;
    lastRefusal = refusal;
    setTimeout(() => {
      if (pendingRefusal === refusal) pendingRefusal = null;
    }, 0);
    renderState();
  }

  window.fetch = async (input, options = {}) => {
    let target = input;
    let request = options;
    if (target === "api/state?fresh=1") {
      // The development bridge recollected on a GET. Collecting here spends a
      // real turn on the native session, so the hosted route is a POST.
      target = "api/refresh";
      request = {
        ...options,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      };
    }
    const accountable = typeof target === "string" && HOSTED_API.test(target);
    let response;
    try {
      response = await nativeFetch(target, request);
    } catch (cause) {
      if (accountable) arm({ status: 0, reason: "the host could not be reached" });
      throw cause;
    }
    if (accountable && !response.ok) {
      arm({ status: response.status, reason: await refusalOf(response) });
    }
    return response;
  };

  // ------------------------------------------------------------ transcript --
  // The packaged page answers a failed turn with "Snapshot answer — …", a
  // sentence it computes from the captured report and prints in the
  // assistant's voice. Replace it with what actually happened. This does not
  // match on that wording: any assistant bubble that appears while a refusal
  // is armed is the fallback, whatever the page decides to call it.
  function watchTranscript() {
    const chat = document.querySelector("#chat");
    if (!chat) return;
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!pendingRefusal) return;
          if (!(node instanceof HTMLElement) || !node.classList.contains("assistant")) continue;
          const refusal = pendingRefusal;
          pendingRefusal = null;
          node.dataset.hostRefusal = String(refusal.status);
          node.textContent =
            `Iris did not answer. This host refused the turn: ${refusal.reason}` +
            (refusal.status ? ` (HTTP ${refusal.status}).` : ".") +
            " Nothing has been computed in her place. Open native chat to see the" +
            " session's own record of what happened.";
          return;
        }
      }
    }).observe(chat, { childList: true });
  }

  // -------------------------------------------------------------- readiness --
  // What the host has actually checked, and what it has not. Deliberately not
  // a prediction: whether the execution host can reach the model is unknowable
  // from the server side until a turn runs, so an unrun session says so in as
  // many words instead of showing a hopeful spinner.
  let readiness = null;
  let readinessError = null;
  let statusLine = null;
  let detailList = null;

  function renderState() {
    if (!statusLine || !detailList) return;
    const lines = [];
    let headline;
    if (readinessError) {
      headline = `This host will not run turns in this session: ${readinessError}`;
    } else if (!readiness) {
      headline = "Checking what this host can verify about this session…";
    } else if (readiness.turn_completed_here) {
      headline = "A turn has completed in this session, so hosted turns work here.";
    } else {
      headline = "No turn has ever completed in this session.";
    }
    statusLine.textContent = headline;
    statusLine.dataset.state = readinessError
      ? "refused"
      : readiness && readiness.turn_completed_here
        ? "ok"
        : "unverified";
    if (readiness) {
      for (const item of readiness.verified || []) lines.push(`Verified: ${item}`);
      for (const item of readiness.unverified || []) lines.push(`Not verified: ${item}`);
      if (readiness.last_task_failed) {
        lines.push(
          "Not verified: this session's last task failed. Open native chat for the" +
            " host's own record; this page is not shown the host's error text.",
        );
      }
    }
    // When the readiness check is itself the thing that refused, the headline
    // already carries the reason; repeating it verbatim underneath reads as
    // two separate problems.
    if (lastRefusal && lastRefusal.reason !== readinessError) {
      lines.push(
        `Last refusal from this host: ${lastRefusal.reason}` +
          (lastRefusal.status ? ` (HTTP ${lastRefusal.status})` : ""),
      );
    }
    detailList.replaceChildren(
      ...lines.map((line) => {
        const item = document.createElement("li");
        item.textContent = line;
        return item;
      }),
    );
  }

  async function loadReadiness() {
    try {
      const response = await nativeFetch("api/readiness", { cache: "no-store" });
      if (!response.ok) {
        readinessError = await refusalOf(response);
      } else {
        readiness = await response.json();
        readinessError = null;
      }
    } catch {
      readinessError = "the host could not be reached";
    }
    renderState();
  }

  // ----------------------------------------------------------------- chrome --
  function control(label, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  window.addEventListener("DOMContentLoaded", () => {
    const match = location.pathname.match(/\/iris\/sessions\/([^/]+)\/ui\//);
    if (!match) return;
    const session = encodeURIComponent(decodeURIComponent(match[1]));

    const style = document.createElement("style");
    style.textContent = `
      .host-bar { padding: 12px 16px; border-bottom: 1px solid var(--line); display: flex;
        flex-direction: column; gap: 8px; }
      .host-bar .host-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
      .host-bar .host-status { font-weight: 550; display: flex; gap: 8px; align-items: baseline; }
      .host-bar .host-status::before { content: "●"; color: var(--muted); font-size: 10px; }
      .host-bar .host-status[data-state="ok"]::before { color: var(--orange); }
      .host-bar .host-status[data-state="refused"]::before { color: var(--warning); }
      .host-bar ul { margin: 0; padding-left: 18px; color: var(--muted); }
      .host-bar li { margin: 2px 0; }
      .host-bar .host-files { display: flex; gap: 12px; flex-wrap: wrap; }
      @media (max-width: 600px) { .host-bar { padding: 12px; } }
    `;
    document.head.append(style);

    const bar = document.createElement("section");
    bar.className = "host-bar";
    bar.setAttribute("aria-label", "Native Iris session");

    statusLine = document.createElement("p");
    statusLine.className = "host-status";
    statusLine.setAttribute("role", "status");
    statusLine.style.margin = "0";
    detailList = document.createElement("ul");

    const actions = document.createElement("div");
    actions.className = "host-actions";
    const chat = document.createElement("a");
    chat.href = `/c/${session}`;
    chat.target = "_top";
    chat.textContent = "Open native chat";

    const outcome = document.createElement("span");
    outcome.setAttribute("role", "status");
    const files = document.createElement("span");
    files.className = "host-files";

    const cancel = control("Cancel current turn", async () => {
      try {
        const res = await nativeFetch("api/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        outcome.textContent = res.ok
          ? "Cancellation requested. Open native chat to resume."
          : `Cancellation refused: ${await refusalOf(res)}`;
      } catch {
        outcome.textContent = "Connection unavailable. Open native chat to check the turn.";
      }
      loadReadiness();
    });

    const downloads = control("Show session downloads", async () => {
      try {
        const res = await nativeFetch(`/v1/sessions/${session}/resources/files?limit=100`);
        if (!res.ok) throw Error();
        const data = await res.json();
        files.replaceChildren();
        for (const file of data.data.filter((f) =>
          ["report.json", "report.md", "proposal.json"].includes(f.filename),
        )) {
          const link = document.createElement("a");
          link.href = `/v1/sessions/${session}/resources/files/${encodeURIComponent(file.id)}/content`;
          link.textContent = file.filename;
          link.download = file.filename;
          files.append(link);
        }
        if (!files.childNodes.length) {
          files.textContent = "No reports in this session yet. Use Refresh from host.";
        }
      } catch {
        files.textContent = "Downloads unavailable; check the native session.";
      }
    });

    const recheck = control("Re-check host", loadReadiness);

    actions.append(chat, cancel, downloads, recheck, outcome);
    bar.append(statusLine, detailList, actions, files);
    document.querySelector(".shell").prepend(bar);

    watchTranscript();
    renderState();
    loadReadiness();
  });
})();
