// Slide-out roster of every registered agent, opened from the sidebar.
//
// Deliberately a sibling of the new-chat picker rather than a replacement
// for it: NewChatDialog.tsx conflicted in BOTH stages of the v0.11 -> v0.13
// upstream sync, so this feature keeps its footprint in upstream-owned
// files to a trigger button and one mount point (Task 6).

import { XIcon } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { agentAvatarColor, agentInitials } from "@/lib/agentInitials";
import { cn } from "@/lib/utils";

interface AgentDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Called with the agent's NAME — the key avatars are stored under. */
  onSelectAgent: (agentName: string) => void;
}

export function AgentDrawer({ open, onClose, onSelectAgent }: AgentDrawerProps) {
  const { data: agents } = useAvailableAgents();
  const { data: avatars } = useAgentAvatars();

  // Cheap Esc-to-close — not a focus trap, just a keyboard escape hatch for
  // an overlay that otherwise only closes via the scrim or the header button.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/40"
        onClick={onClose}
        data-testid="agent-drawer-scrim"
      />
      <aside
        data-testid="agent-drawer"
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-80 max-w-[85vw] flex-col",
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
            const url = avatars?.[agent.name];
            return (
              <button
                key={agent.id}
                type="button"
                data-testid={`agent-drawer-row-${agent.name}`}
                onClick={() => onSelectAgent(agent.name)}
                className="flex w-full items-center gap-3 rounded-md p-2 text-left hover:bg-muted"
              >
                {url ? (
                  <img
                    src={url}
                    alt={agent.name}
                    className="size-10 shrink-0 rounded-full object-cover"
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
                      the picker; the avatar and onSelectAgent both key on
                      `name`, which is what the server stores. */}
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
            );
          })}
        </div>
      </aside>
    </>
  );
}
