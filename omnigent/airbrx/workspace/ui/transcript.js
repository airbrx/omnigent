// Airbrx workspace kernel: the chat transcript.
//
// Four kinds of line. "agent" is the agent's answer and renders the safe
// markdown subset; "user", "system" and "error" are plain text. A progress
// line (role=status) says what the agent is doing and stays at the bottom.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});
  const KINDS = new Set(["user", "agent", "system", "error"]);

  /**
   * `list` is the <ol id="messages">. `agentName` names the agent in the
   * lines the kernel writes itself (for example "<agent> is working").
   */
  function createTranscript({ list, agentName = "The agent" } = {}) {
    if (!list) throw new Error("createTranscript needs a list");
    const raw = new WeakMap();
    let progressLine = null;
    let waitingLine = null;

    function keepProgressLast() {
      if (progressLine) list.append(progressLine);
    }

    function scroll() {
      list.scrollTop = list.scrollHeight;
    }

    /** The agent's text as it wrote it, rendered as markdown into `li`. */
    function setText(li, text) {
      const value = String(text ?? "");
      raw.set(li, value);
      if (li.classList.contains("agent")) li.replaceChildren(AW.markdown(value));
      else li.textContent = value;
    }

    /**
     * Add a line and return it. `retry`, when given, adds a "Try again" chip
     * that calls it. The first agent line removes any waiting line.
     */
    function add(kind, text, retry) {
      const safeKind = KINDS.has(kind) ? kind : "system";
      if (safeKind === "agent" && waitingLine) {
        waitingLine.remove();
        waitingLine = null;
      }
      const li = AW.el("li", { class: safeKind });
      setText(li, text);
      if (typeof retry === "function")
        li.append(
          AW.el(
            "button",
            { type: "button", class: "chip retry", onclick: retry },
            "Try again",
          ),
        );
      list.append(li);
      keepProgressLast();
      scroll();
      return li;
    }

    /** Show `label` on the one progress line, or remove it with null. */
    function progress(label) {
      if (label === null || label === undefined || label === "") {
        if (progressLine) progressLine.remove();
        progressLine = null;
        return null;
      }
      if (!progressLine) {
        progressLine = AW.el("li", {
          class: "system progress",
          role: "status",
        });
      }
      progressLine.textContent = String(label);
      list.append(progressLine);
      scroll();
      return progressLine;
    }

    /**
     * A system line that the next agent line replaces, e.g. "<agent> hasn't
     * answered in this session yet." null removes it.
     */
    function waiting(text) {
      if (waitingLine) waitingLine.remove();
      waitingLine = text ? add("system", text) : null;
      return waitingLine;
    }

    return {
      add,
      setText,
      progress,
      waiting,
      text: (li) => raw.get(li),
      agentName,
    };
  }

  AW.createTranscript = createTranscript;
})();
