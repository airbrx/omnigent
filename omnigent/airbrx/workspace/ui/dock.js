// Airbrx workspace kernel: the chat dock. Docked beside a tab, the chat can
// collapse to a 44 px rail; the choice is remembered in localStorage, which can
// be unavailable, so every access is guarded and the dock still works.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  function createDock({
    chat,
    rail,
    collapseButton,
    storageKey,
    body = document.body,
    onChange,
  } = {}) {
    let collapsed = false;
    try {
      collapsed = localStorage.getItem(storageKey) === "1";
    } catch {
      collapsed = false;
    }
    let docked = false;

    /** Show the dock for the open tab. Only a docked chat can collapse. */
    function render(options) {
      if (options && "docked" in options) docked = Boolean(options.docked);
      const isCollapsed = docked && collapsed;
      body.classList.toggle("docked", docked);
      body.classList.toggle("collapsed", isCollapsed);
      if (chat) chat.hidden = isCollapsed;
      if (rail) rail.hidden = !isCollapsed;
      if (collapseButton) collapseButton.hidden = !docked;
      if (typeof onChange === "function") onChange({ docked, collapsed: isCollapsed });
    }

    function setCollapsed(value) {
      collapsed = Boolean(value);
      try {
        localStorage.setItem(storageKey, collapsed ? "1" : "0");
      } catch {
        // Storage can be unavailable; the dock still works for this visit.
      }
      render();
    }

    if (collapseButton)
      collapseButton.addEventListener("click", () => setCollapsed(true));
    if (rail) rail.addEventListener("click", () => setCollapsed(false));

    return { collapsed: () => collapsed, setCollapsed, render };
  }

  AW.createDock = createDock;
})();
