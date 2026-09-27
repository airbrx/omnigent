// Airbrx workspace kernel: tabs. One array declares them; the open tab lives
// in the URL hash (replaceState, so tabs do not fill the back button).
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  function replaceHash(hash) {
    try {
      window.history.replaceState(null, "", hash);
    } catch {
      location.hash = hash;
    }
  }

  /**
   * `tabs` is [{id, label, home?}, ...]. onShow(tab, prev) draws a tab;
   * clicking the open tab calls restart(id), which calls onRestart(tab) when
   * given and otherwise onShow(tab, tab).
   */
  function createTabs({ nav, tabs, onShow, onRestart } = {}) {
    if (!Array.isArray(tabs) || !tabs.length)
      throw new Error("createTabs needs tabs");
    let current = null;
    const find = (id) => tabs.find((t) => t.id === id);
    const fallback = () => tabs.find((t) => t.home) || tabs[0];

    function renderNav() {
      if (!nav) return;
      if (!nav.children.length)
        nav.append(
          ...tabs.map((t) =>
            AW.el(
              "button",
              {
                type: "button",
                role: "tab",
                "data-tab": t.id,
                onclick: () =>
                  current && t.id === current.id ? restart(t.id) : show(t.id),
              },
              t.label,
            ),
          ),
        );
      for (const button of nav.children) {
        const selected = Boolean(current) && button.dataset.tab === current.id;
        button.setAttribute("aria-selected", String(selected));
        button.classList.toggle("active", selected);
      }
    }

    /** Open tab `id` (an unknown id opens the home tab). Returns the tab. */
    function show(id, { fromHash = false } = {}) {
      const prev = current;
      current = find(id) || fallback();
      if (!fromHash) replaceHash(`#${current.id}`);
      if (typeof document !== "undefined" && document.body)
        document.body.dataset.tab = current.id;
      renderNav();
      if (typeof onShow === "function") onShow(current, prev);
      return current;
    }

    function restart(id) {
      const tab = find(id) || current;
      if (!tab) return;
      if (typeof onRestart === "function") onRestart(tab);
      else if (typeof onShow === "function") onShow(tab, tab);
    }

    /** The tab the hash names, or the home tab. */
    function fromHash() {
      return find((location.hash || "").slice(1)) || fallback();
    }

    const onHash = () => show(fromHash().id, { fromHash: true });
    window.addEventListener("hashchange", onHash);

    return {
      current: () => current,
      show,
      restart,
      fromHash,
      destroy: () => window.removeEventListener("hashchange", onHash),
    };
  }

  AW.createTabs = createTabs;
})();
