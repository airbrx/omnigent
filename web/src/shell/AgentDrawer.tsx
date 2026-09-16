// Slide-out roster of every registered agent, opened from the sidebar.
//
// Deliberately a sibling of the new-chat picker rather than a replacement
// for it: NewChatDialog.tsx conflicted in BOTH stages of the v0.11 -> v0.13
// upstream sync, so this feature keeps its footprint there to one small,
// additive effect (reading a one-shot `?agent=` param) rather than any
// deeper rework of the picker itself.

import { useQueryClient } from "@tanstack/react-query";
import { ImageIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { type AvailableAgent, useAvailableAgents } from "@/hooks/useAvailableAgents";
import { agentAvatarColor, agentInitials } from "@/lib/agentInitials";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { cn } from "@/lib/utils";

interface AgentDrawerProps {
  open: boolean;
  onClose: () => void;
  /**
   * Called with the full picked agent. The caller (AppShell) needs the
   * agent's id — not just its name — both to persist the preference
   * (`writeLastAgentId`) and to navigate to `/?agent=<id>`, which is how the
   * pick reaches the new-chat landing screen — so the row hands back the
   * whole object rather than a single field.
   */
  onSelectAgent: (agent: AvailableAgent) => void;
  onOpenWorkspace?: () => void;
}

const AVATAR_CHIP = "size-10 shrink-0 rounded-full";

/**
 * One agent's avatar: the uploaded image, or the deterministic initials chip.
 *
 * Standalone, the avatar URL is same-origin and a plain `<img src>` is the
 * right thing — native streaming, native HTTP caching, no JavaScript in the
 * path. Embedded (`build:embed`), the host proxies the API behind a path
 * prefix and cookie+CSRF auth that a browser's own `<img>` GET cannot carry,
 * so the request either 404s or resolves against the wrong origin and every
 * row silently falls back to initials. Pull the bytes through the host fetcher
 * there and render an object URL instead — the same split SessionImage.tsx
 * makes, for the same reason. Deferred from #16 and closed here.
 */
function AgentAvatar({ name, url }: { name: string; url?: string }) {
  const embedded = Boolean(getOmnigentHostConfig().fetcher);
  // Tracked by URL, not as a bare flag: `useAgentAvatars` cache-busts on
  // `updated_at`, so a re-uploaded avatar must get a fresh attempt rather than
  // inheriting the previous one's failure.
  const [failed, setFailed] = useState<string | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!embedded || !url) return;
    let revoked = false;
    let created: string | null = null;
    authenticatedFetch(url)
      .then((response) => {
        if (!response.ok) throw Error(`avatar unavailable (${response.status})`);
        return response.blob();
      })
      .then((blob) => {
        if (revoked) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      // An avatar that cannot be fetched is a missing picture, not a broken
      // drawer: fall back to the initials chip, which is what a row with no
      // avatar at all already shows.
      .catch(() => setFailed(url));
    return () => {
      revoked = true;
      setObjectUrl(null);
      if (created) URL.revokeObjectURL(created);
    };
  }, [embedded, url]);

  const src = embedded ? objectUrl : url;
  if (url && failed !== url && src) {
    return (
      <img
        src={src}
        alt={name}
        className={cn(AVATAR_CHIP, "object-cover")}
        onError={() => setFailed(url)}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className={cn(
        AVATAR_CHIP,
        "flex items-center justify-center",
        "text-xs font-semibold text-white",
        agentAvatarColor(name),
      )}
    >
      {agentInitials(name)}
    </span>
  );
}

/**
 * Set or clear one agent's picture, from inside the roster that shows it.
 *
 * Deferred from #16: the avatar API has existed since the drawer shipped and
 * nothing could reach it, so every agent that was not Iris sat on initials
 * forever and the endpoints were dead weight.
 *
 * The server validates twice — the declared multipart type, then the actual
 * magic bytes — and caps the size, so this deliberately does not pre-judge a
 * file in the browser. It sends what was picked and reports back exactly what
 * the server said. A guess here would either reject something the server
 * accepts or, worse, print a reason that is not the real one.
 */
function AvatarControls({ name, hasStoredAvatar }: { name: string; hasStoredAvatar: boolean }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState("");
  const [failed, setFailed] = useState(false);
  const endpoint = `/v1/agent-avatars/${encodeURIComponent(name)}`;

  async function announce(response: Response, done: string) {
    if (response.ok) {
      setFailed(false);
      setStatus(done);
      await queryClient.invalidateQueries({ queryKey: ["agent-avatars"] });
      return;
    }
    setFailed(true);
    // The server's own words: "unsupported content type 'image/svg+xml'",
    // "upload does not match a supported image format", the size cap. Naming
    // the reason is the difference between a fixable mistake and a dead button.
    const body = await response.json().catch(() => null);
    const reason = body?.error?.message;
    setStatus(
      typeof reason === "string" && reason
        ? `Not saved: ${reason}`
        : `Not saved: the host returned ${response.status}.`,
    );
  }

  async function send(file: File) {
    setFailed(false);
    setStatus("Uploading…");
    const body = new FormData();
    body.append("file", file);
    try {
      await announce(await authenticatedFetch(endpoint, { method: "PUT", body }), "Picture saved.");
    } catch {
      setFailed(true);
      setStatus("Not saved: the host could not be reached.");
    }
  }

  return (
    <div className="ml-12 flex flex-wrap items-center gap-2 pb-1">
      <input
        ref={input}
        type="file"
        className="hidden"
        accept="image/png,image/jpeg,image/webp,image/gif"
        data-testid={`agent-avatar-file-${name}`}
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Clear the control so re-picking the same file fires onChange again
          // after a failed attempt.
          e.target.value = "";
          if (file) void send(file);
        }}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label={`Change ${name}'s picture`}
        onClick={() => input.current?.click()}
      >
        <ImageIcon className="size-3.5" />
        {hasStoredAvatar ? "Replace picture" : "Add picture"}
      </Button>
      {hasStoredAvatar && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={`Remove ${name}'s picture`}
          onClick={async () => {
            setFailed(false);
            setStatus("Removing…");
            try {
              await announce(
                await authenticatedFetch(endpoint, { method: "DELETE" }),
                "Picture removed.",
              );
            } catch {
              setFailed(true);
              setStatus("Not removed: the host could not be reached.");
            }
          }}
        >
          Remove
        </Button>
      )}
      {status && (
        <span
          role={failed ? "alert" : "status"}
          className={cn("text-xs", failed ? "text-destructive" : "text-muted-foreground")}
        >
          {status}
        </span>
      )}
    </div>
  );
}

export function AgentDrawer({ open, onClose, onSelectAgent, onOpenWorkspace }: AgentDrawerProps) {
  const { data: agents } = useAvailableAgents();
  const { data: avatars } = useAgentAvatars();
  // Off by default: the roster is for picking an agent, and a Change/Remove
  // pair on every row would put housekeeping in front of that on every open.
  const [editingAvatars, setEditingAvatars] = useState(false);

  const drawerRef = useRef<HTMLElement>(null);

  // Cheap Esc-to-close — not a focus trap, just a keyboard escape hatch for
  // an overlay that otherwise only closes via the scrim or the header button.
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    // Close by name, not "whatever button is first": the header carries a
    // second control now, and landing on it would open with the housekeeping
    // toggle under the cursor instead of the way out.
    drawerRef.current?.querySelector<HTMLButtonElement>('[aria-label="Close"]')?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const controls = drawerRef.current?.querySelectorAll<HTMLElement>("button, a[href]");
        if (!controls?.length) return;
        const first = controls[0],
          last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      {/* z-[55]/z-[56], not the usual z-40/z-50 modal pair: the trigger lives
      inside the sidebar, which on mobile is itself a full-bleed `fixed
      inset-0 z-50` overlay (Sidebar.tsx) — a z-40 scrim would render BEHIND
      it and be untappable, so a tap meant to dismiss this drawer would fall
      through to a conversation row in the sidebar underneath and navigate
      away instead. Staying above the sidebar's z-50 (and below
      ImageLightbox's z-60) keeps tap-outside working on every breakpoint. */}
      <div
        className="fixed inset-0 z-[55] bg-black/40"
        onClick={onClose}
        data-testid="agent-drawer-scrim"
      />
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Agents"
        data-testid="agent-drawer"
        className={cn(
          "fixed inset-y-0 left-0 z-[56] flex w-80 max-w-[85vw] flex-col",
          "border-border border-r bg-card shadow-lg",
        )}
      >
        <header className="flex shrink-0 items-center justify-between border-border border-b px-4 py-2">
          <h2 className="font-medium text-ui">Agents</h2>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-pressed={editingAvatars}
              data-testid="agent-drawer-edit-avatars"
              onClick={() => setEditingAvatars((editing) => !editing)}
            >
              {editingAvatars ? "Done" : "Edit pictures"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              onClick={onClose}
            >
              <XIcon className="size-4" />
            </Button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-2">
          {(agents ?? []).map((agent) => {
            return (
              <div key={agent.id}>
                <button
                  type="button"
                  aria-label={agent.name === "iris" ? "Start chat with Iris" : undefined}
                  data-testid={`agent-drawer-row-${agent.name}`}
                  onClick={() => onSelectAgent(agent)}
                  className="flex w-full items-center gap-3 rounded-md p-2 text-left hover:bg-muted"
                >
                  <AgentAvatar
                    name={agent.name}
                    // Iris ships her own portrait inside her pinned package and
                    // the host serves it; an uploaded avatar still wins, and a
                    // host without the Iris package 404s straight through to
                    // the initials chip. No other agent has a portrait to fall
                    // back to.
                    url={
                      avatars?.[agent.name] ??
                      (agent.name === "iris" ? "/v1/iris/portrait" : undefined)
                    }
                  />
                  <span className="min-w-0">
                    {/* Label from display_name so the drawer reads the same as
                      the picker; the avatar keys on `name`, which is what
                      the server stores. */}
                    <span className="block truncate font-medium text-ui">
                      {agent.display_name || agent.name}
                    </span>
                    {agent.description ? (
                      <span className="block truncate text-muted-foreground text-xs">
                        {agent.description}
                      </span>
                    ) : null}
                  </span>
                </button>
                {agent.name === "iris" && onOpenWorkspace && (
                  <Button
                    variant="ghost"
                    className="ml-12"
                    aria-label="Open Iris workspace"
                    onClick={onOpenWorkspace}
                  >
                    Open workspace
                  </Button>
                )}
                {editingAvatars && (
                  <AvatarControls
                    name={agent.name}
                    hasStoredAvatar={Boolean(avatars?.[agent.name])}
                  />
                )}
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
