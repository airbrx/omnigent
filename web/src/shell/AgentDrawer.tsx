// Slide-out roster of every registered agent, opened from the sidebar.
//
// Deliberately a sibling of the new-chat picker rather than a replacement
// for it: NewChatDialog.tsx conflicted in BOTH stages of the v0.11 -> v0.13
// upstream sync, so this feature keeps its footprint there to one small,
// additive effect (reading a one-shot `?agent=` param) rather than any
// deeper rework of the picker itself.

import { XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { type AvailableAgent, useAvailableAgents } from "@/hooks/useAvailableAgents";
import { agentAvatarColor, agentInitials } from "@/lib/agentInitials";
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

export function AgentDrawer({ open, onClose, onSelectAgent, onOpenWorkspace }: AgentDrawerProps) {
  const { data: agents } = useAvailableAgents();
  const { data: avatars } = useAgentAvatars();
  // Per-row fallback: an avatar URL that 404s (or, in the build:embed target,
  // resolves against the wrong origin because a plain <img> bypasses the host
  // fetcher — see SessionImage.tsx for the codebase's blob-URL solution,
  // out of scope here) should degrade to the initials chip rather than show
  // a broken-image glyph.
  const [failedAvatars, setFailedAvatars] = useState<Record<string, boolean>>({});

  const drawerRef = useRef<HTMLElement>(null);

  // Cheap Esc-to-close — not a focus trap, just a keyboard escape hatch for
  // an overlay that otherwise only closes via the scrim or the header button.
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    drawerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
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
          <Button type="button" variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
            <XIcon className="size-4" />
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto p-2">
          {(agents ?? []).map((agent) => {
            const url = failedAvatars[agent.name] ? undefined : avatars?.[agent.name];
            return (
              <div key={agent.id}>
                <button
                  type="button"
                  aria-label={agent.name === "iris" ? "Start chat with Iris" : undefined}
                  data-testid={`agent-drawer-row-${agent.name}`}
                  onClick={() => onSelectAgent(agent)}
                  className="flex w-full items-center gap-3 rounded-md p-2 text-left hover:bg-muted"
                >
                  {url ? (
                    <img
                      src={url}
                      alt={agent.name}
                      className="size-10 shrink-0 rounded-full object-cover"
                      onError={() => setFailedAvatars((prev) => ({ ...prev, [agent.name]: true }))}
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className={cn(
                        "flex size-10 shrink-0 items-center justify-center rounded-full",
                        "text-xs font-semibold text-white",
                        agentAvatarColor(agent.name),
                      )}
                    >
                      {agentInitials(agent.name)}
                    </span>
                  )}
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
                  <Button variant="ghost" className="ml-12" onClick={onOpenWorkspace}>
                    Open workspace
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}
