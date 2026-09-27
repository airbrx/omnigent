// Airbrx workspace kernel: theme. The host's appearance on the URL at mount
// (?theme=light|dark, else prefers-color-scheme), then by postMessage from this
// origin only. No picker and nothing stored: the host decides.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  function apply(mode) {
    if (mode === "dark" || mode === "light") {
      document.documentElement.dataset.theme = mode;
      return true;
    }
    return false;
  }

  /**
   * Apply the mount theme and follow the host. `messageKey` is the field the
   * host's message carries, e.g. "<agent>HostTheme". Returns {apply, stop}.
   */
  function init({ messageKey } = {}) {
    const params = new URLSearchParams(location.search);
    const dark =
      typeof matchMedia === "function" &&
      matchMedia("(prefers-color-scheme: dark)").matches;
    if (!apply(params.get("theme"))) apply(dark ? "dark" : "light");
    const listener = (event) => {
      if (event.origin !== location.origin) return;
      const data = event.data;
      if (messageKey && data && typeof data[messageKey] === "string")
        apply(data[messageKey]);
    };
    window.addEventListener("message", listener);
    return {
      apply,
      stop: () => window.removeEventListener("message", listener),
    };
  }

  AW.theme = { init, apply };
})();
