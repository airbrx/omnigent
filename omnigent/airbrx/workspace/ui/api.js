// Airbrx workspace kernel: the adapter client and its errors in plain words.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  /**
   * A client for the agent's adapter, relative to the page. GET without a
   * body, POST JSON with one. Each call is abandoned after `timeoutMs` (330 s
   * by default: the adapter's 300 s turn deadline plus room to answer).
   *
   * Failures throw an Error with `.status` (0 = network, 504 = our own
   * deadline, with `.timedOut`) and `.detail`: the FastAPI `detail` string, or
   * null. The message is never meant for display; use plainError.
   */
  function createApi({ base = "api/", timeoutMs = 330000 } = {}) {
    return async function api(path, body) {
      const controller =
        typeof AbortController === "function" ? new AbortController() : null;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        if (controller) controller.abort();
      }, timeoutMs);
      let response;
      try {
        response = await fetch(`${base}${path}`, {
          method: body === undefined ? "GET" : "POST",
          headers:
            body === undefined ? {} : { "Content-Type": "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body),
          credentials: "same-origin",
          signal: controller ? controller.signal : undefined,
        });
      } catch (cause) {
        clearTimeout(timer);
        const error = new Error(timedOut ? "timed out" : "network error");
        error.status = timedOut ? 504 : 0;
        error.timedOut = timedOut;
        error.detail = null;
        error.cause = cause;
        throw error;
      }
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      } finally {
        clearTimeout(timer);
      }
      if (!response.ok) {
        const detail =
          payload && typeof payload.detail === "string" ? payload.detail : null;
        const error = new Error(detail || `HTTP ${response.status}`);
        error.status = response.status;
        error.timedOut = false;
        error.detail = detail;
        throw error;
      }
      return payload;
    };
  }

  /**
   * An adapter failure in words a person can act on, as one or two complete
   * sentences ending in a period. Fixed wording for 401, 502/503, 504 (and our
   * own deadline), other 5xx and network failures. Only a 4xx `detail` is shown
   * as written, because the adapter writes those for people. Host text on a
   * 5xx is never shown: it can carry diagnostics that must not reach a screen.
   */
  function plainError(error, { agent = "The agent" } = {}) {
    const status = error && typeof error.status === "number" ? error.status : 0;
    const detail =
      error && typeof error.detail === "string" ? error.detail.trim() : "";
    if (status === 401)
      return "Your Omnigent sign-in has expired. Reload the page to sign in again.";
    if (status === 502 || status === 503)
      return `${agent} could not be reached just now. The host may be offline or restarting.`;
    if (status === 504 || (error && error.timedOut))
      return `${agent} took too long, so the turn was stopped.`;
    if (!status)
      return "The workspace could not reach Omnigent. Check your connection.";
    if (status >= 500) return "Something went wrong on the Omnigent side.";
    if (detail) return /[.!?]$/.test(detail) ? detail : `${detail}.`;
    return `The request was refused (HTTP ${status}).`;
  }

  AW.createApi = createApi;
  AW.plainError = plainError;
})();
