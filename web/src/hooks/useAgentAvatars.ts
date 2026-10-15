import { useQuery } from "@tanstack/react-query";

import { authenticatedFetch } from "@/lib/identity";

interface AgentAvatarRow {
  agent_name: string;
  url: string;
  updated_at: number;
}

/**
 * Agent name -> avatar URL for every agent that has one.
 *
 * One request for the whole roster rather than one per row: the drawer
 * renders the full list at once, and N image-metadata requests would be
 * N round trips before the first pixel.
 */
export function useAgentAvatars() {
  return useQuery({
    queryKey: ["agent-avatars"],
    queryFn: async (): Promise<Record<string, string>> => {
      const res = await authenticatedFetch("/v1/agent-avatars");
      if (!res.ok) return {};
      const body = (await res.json()) as { data: AgentAvatarRow[] };
      return Object.fromEntries(body.data.map((a) => [a.agent_name, `${a.url}?v=${a.updated_at}`]));
    },
    staleTime: 60_000,
  });
}
