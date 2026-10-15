// Airbrx workspace kernel: what the person is looking at, for the agent's
// prompt, and the "Don't include this page" control.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  /** Text headed for a prompt: one line, no control characters, bounded. */
  function cleanName(raw, max = 120) {
    if (typeof raw !== "string") return "";
    return raw
      .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, max);
  }

  /**
   * `badge` is the container shown while the page is included, `label` the
   * element that says what it is about, `button` the "Don't include this page"
   * control. render(text) shows `text` (or hides the badge for a falsy text);
   * reset() includes the page again, e.g. on a tab change.
   */
  function createContextToggle({ badge, label, button, onChange } = {}) {
    let included = true;
    let last = "";

    function render(text) {
      if (text !== undefined) last = text ? String(text) : "";
      if (label) label.textContent = last;
      if (badge) badge.hidden = !included || !last;
      if (typeof onChange === "function") onChange({ included, label: last });
    }

    if (button)
      button.addEventListener("click", () => {
        included = false;
        render();
      });

    return {
      included: () => included,
      reset: () => {
        included = true;
        render();
      },
      render,
    };
  }

  AW.cleanName = cleanName;
  AW.createContextToggle = createContextToggle;
})();
