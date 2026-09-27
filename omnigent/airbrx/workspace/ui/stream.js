// Airbrx workspace kernel: the session's own record, streamed into the chat.
//
// The adapter's chat call returns only when the agent's turn is over, which can
// be minutes. While it runs, the dock reads the native session's items (the
// same record native chat shows) and puts the agent's interim messages and what
// it is doing into the chat as they arrive. The same read rebuilds the chat on
// load, so a reload does not lose the conversation, and resumeTurn follows a
// turn that was still running when the page reloaded until its answer lands.
//
// What may reach the chat is a whitelist:
//   message, role assistant   -> an agent line (its output_text parts)
//   message, role user        -> history only: recognise() or a user line
//   function_call             -> live only: a progress label from `doing`,
//                                never the tool's name, never its arguments
//   function_call_output, reasoning, anything else -> nothing, ever. They carry
//                                customer data and evidence.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  /** The session id in a framed workspace path: .../sessions/{id}/ui/... */
  function sessionIdFromPath(pathname = location.pathname) {
    const match = /\/sessions\/([^/]+)\/ui(?:\/|$)/.exec(pathname);
    try {
      return match ? decodeURIComponent(match[1]) : "";
    } catch {
      return "";
    }
  }

  function itemText(item) {
    return (Array.isArray(item.content) ? item.content : [])
      .filter(
        (c) => c && (c.type === "output_text" || c.type === "input_text"),
      )
      .map((c) => (typeof c.text === "string" ? c.text : ""))
      .join("\n");
  }

  function createStream({
    sessionId,
    pollMs = 1500,
    doing = {},
    recognise = () => null,
    stripUser = (text) => text,
    transcript,
  } = {}) {
    if (!transcript) throw new Error("createStream needs a transcript");
    const handled = new Set();
    const answerLines = new Map();
    const label = (name) => {
      const bare = String(name || "").split("__").pop();
      const words =
        doing && Object.hasOwn(doing, bare) && typeof doing[bare] === "string"
          ? doing[bare]
          : "Working";
      return `${words}…`;
    };

    /** The newest `limit` items, oldest first, and whether there are more. */
    async function sessionItems(limit) {
      if (!sessionId) throw new Error("no session");
      const response = await fetch(
        `/v1/sessions/${encodeURIComponent(sessionId)}/items?order=desc&limit=${limit}`,
        { credentials: "same-origin" },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const page = await response.json();
      return {
        items: (Array.isArray(page && page.data) ? page.data : [])
          .slice()
          .reverse(),
        hasMore: Boolean(page && page.has_more),
      };
    }

    /**
     * Put one item in the chat. `live` is a turn in progress: the agent's
     * messages and what it is doing. Otherwise it is history: both sides.
     */
    function applyItem(item, live, shown) {
      if (!item || !item.id) return;
      if (item.type === "message" && item.role === "assistant") {
        const text = itemText(item);
        if (!text.trim()) return;
        const line = answerLines.get(item.id);
        if (line) {
          if (transcript.text(line) !== text) transcript.setText(line, text);
          // The grown text is what the reply will carry, so it is shown too.
          if (shown) shown.add(text.trim());
          return;
        }
        if (handled.has(item.id)) return;
        handled.add(item.id);
        answerLines.set(item.id, transcript.add("agent", text));
        if (shown) shown.add(text.trim());
        return;
      }
      if (handled.has(item.id)) return;
      handled.add(item.id);
      if (item.type === "function_call" && live) {
        transcript.progress(label(item.name));
      } else if (item.type === "message" && item.role === "user" && !live) {
        const text = itemText(item);
        const note = recognise(text);
        if (typeof note === "string" && note) transcript.add("system", note);
        else if (text.trim()) transcript.add("user", String(stripUser(text)));
      }
    }

    /** Mark everything already in the session as shown, before a new turn. */
    async function markExisting() {
      try {
        const { items } = await sessionItems(200);
        for (const item of items) if (item && item.id) handled.add(item.id);
      } catch {
        // Without the record the turn still answers; it just cannot stream.
      }
    }

    /**
     * Stream a running turn into the chat until the returned stop() is
     * awaited. stop() halts polling, applies one last read and clears the
     * progress line. Call it whether the turn answered or failed.
     */
    function watchTurn(shown) {
      let stopped = false;
      let timer = null;
      transcript.progress(`${transcript.agentName} is working…`);
      const tick = async () => {
        if (stopped) return;
        try {
          const { items } = await sessionItems(50);
          if (!stopped) for (const item of items) applyItem(item, true, shown);
        } catch {
          // A missed poll is harmless: the next one, or the answer, catches up.
        }
        if (!stopped) timer = setTimeout(tick, pollMs);
      };
      timer = setTimeout(tick, pollMs);
      let stopping = null;
      return () => {
        if (stopping) return stopping;
        stopped = true;
        clearTimeout(timer);
        stopping = (async () => {
          try {
            const { items } = await sessionItems(50);
            for (const item of items) applyItem(item, true, shown);
          } catch {
            // The adapter's answer still shows.
          }
          transcript.progress(null);
        })();
        return stopping;
      };
    }

    /**
     * Rebuild the chat from the session. Resolves true when it holds at least
     * one agent answer, false otherwise (or when the record cannot be read).
     */
    async function loadHistory() {
      try {
        const { items, hasMore } = await sessionItems(200);
        if (hasMore)
          transcript.add("system", "Earlier messages are in native chat.");
        for (const item of items) applyItem(item, false);
        return answerLines.size > 0;
      } catch {
        return false;
      }
    }

    /** The native session's status ("idle", "running", ...), or "" unread. */
    async function sessionStatus() {
      if (!sessionId) return "";
      try {
        const response = await fetch(
          `/v1/sessions/${encodeURIComponent(sessionId)}`,
          { credentials: "same-origin" },
        );
        if (!response.ok) return "";
        const session = await response.json();
        const status =
          session && typeof session.status === "string" ? session.status : "";
        // A response still open is a running turn whatever the label says.
        return session && session.active_response_id && status === "idle"
          ? "running"
          : status;
      } catch {
        return "";
      }
    }

    const running = (status) => status === "running" || status === "waiting";

    /**
     * Follow a turn that was already running when the page loaded (a reload
     * mid-turn): stream it like watchTurn until the session stops running,
     * then apply one last read so its answer shows without another reload.
     * Call after loadHistory, which marks what was already there as shown.
     * Resolves { resumed, status }: resumed is false when no turn was
     * running, or the session could not be read. It gives up after
     * `timeoutMs` (the adapter's 300 s turn deadline plus room), with status
     * "timeout". Pass `running: true` when the caller already saw the turn
     * running: it is then followed without a fresh check, so a turn that
     * ended in between still gets its last read and its answer shows.
     */
    async function resumeTurn({ timeoutMs = 330000, running: seen = false } = {}) {
      let status = seen ? "running" : await sessionStatus();
      if (!running(status)) return { resumed: false, status };
      const stop = watchTurn(null);
      const until = Date.now() + timeoutMs;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, pollMs));
        const now = await sessionStatus();
        // A missed read keeps following; the next one catches up.
        if (now && !running(now)) {
          status = now;
          break;
        }
        if (Date.now() >= until) {
          status = "timeout";
          break;
        }
      }
      await stop();
      return { resumed: true, status };
    }

    return {
      sessionItems,
      markExisting,
      watchTurn,
      loadHistory,
      sessionStatus,
      resumeTurn,
    };
  }

  AW.createStream = createStream;
  AW.sessionIdFromPath = sessionIdFromPath;
})();
