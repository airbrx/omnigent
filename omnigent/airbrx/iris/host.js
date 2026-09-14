// Host adapter for the unchanged Iris workspace assets.
(() => {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    if (input === "api/state?fresh=1") {
      return nativeFetch("api/refresh", {
        ...options, method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
    }
    return nativeFetch(input, options);
  };
  window.addEventListener("DOMContentLoaded", () => {
    const match = location.pathname.match(/\/iris\/sessions\/([^/]+)\/ui\//);
    if (!match) return;
    const session = encodeURIComponent(decodeURIComponent(match[1]));
    const bar = document.createElement("section");
    bar.setAttribute("aria-label", "Native Iris session");
    bar.style.cssText = "padding:12px;display:flex;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--border)";
    const chat = document.createElement("a");
    chat.href = `/c/${session}`; chat.target = "_top"; chat.textContent = "Open native chat";
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel current turn";
    const status = document.createElement("span");
    status.setAttribute("role", "status");
    cancel.onclick = async () => {
      try {
        const res = await nativeFetch("api/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        status.textContent = res.ok ? "Cancellation requested. Open native chat to resume." : "Cancellation failed. Open native chat.";
      } catch { status.textContent = "Connection unavailable. Open native chat to check the turn."; }
    };
    const files = document.createElement("span");
    const refresh = document.createElement("button");
    refresh.textContent = "Show session downloads";
    refresh.onclick = async () => {
      try {
        const res = await nativeFetch(`/v1/sessions/${session}/resources/files?limit=100`);
        if (!res.ok) throw Error();
        const data = await res.json();
        files.replaceChildren();
        for (const file of data.data.filter(f => ["report.json", "report.md", "proposal.json"].includes(f.filename))) {
          const link = document.createElement("a");
          link.href = `/v1/sessions/${session}/resources/files/${encodeURIComponent(file.id)}/content`;
          link.textContent = `${file.filename} `; link.download = file.filename;
          files.append(link);
        }
        if (!files.childNodes.length) files.textContent = "No reports yet. Use Refresh from host.";
      } catch { files.textContent = "Downloads unavailable; check the native session."; }
    };
    bar.append(chat, cancel, refresh, status, files);
    document.querySelector(".shell").prepend(bar);
  });
})();
