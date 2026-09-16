// The cost and savings surface: what the agents cost, what that cost
// avoided, and what this page cannot tell you.
//
// The whole design problem here is that three numbers with three
// different standings — measured, modelled, and not-computable — look
// identical once they are rendered as currency. So none of them is
// rendered as bare currency. A figure that was never measured says so in
// words; it is never a dash and never $0.00, because both of those read
// as "we checked, and it was nothing".

import { useQuery } from "@tanstack/react-query";

import { authenticatedFetch } from "@/lib/identity";
import { cn } from "@/lib/utils";

export interface CostAgentRow {
  agent_id: string | null;
  agent_name: string | null;
  agent_name_resolved: boolean;
  measured_usd: number | null;
  sessions: number;
  priced_sessions: number;
  unpriced_sessions: number;
  malformed_sessions: number;
  priced_tokens: number;
  unpriced_tokens: number;
  complete: boolean;
}

export interface CostResponse {
  window: { start_utc: number; end_utc: number };
  basis: {
    provider_kind: string | null;
    metered: boolean | null;
    determinate: boolean;
    note: string | null;
  };
  agent_token_cost: {
    standing: string;
    measured_usd: number | null;
    sessions: number;
    priced_sessions: number;
    complete: boolean;
    coverage_note: string | null;
    by_agent: CostAgentRow[];
  };
  cost_avoided: {
    standing: string;
    modelled_usd: number | null;
    billed_usd: number | null;
    explanation: string;
  };
  warehouse: {
    standing: string;
    available: boolean;
    requires: string[];
    explanation: string;
  };
}

/** Cost for a window. Returns the whole envelope; the panel needs the caveats. */
export function useCost(startUtc: number, endUtc: number) {
  return useQuery({
    queryKey: ["cost", startUtc, endUtc],
    queryFn: async (): Promise<CostResponse> => {
      const res = await authenticatedFetch(`/v1/cost?start=${startUtc}&end=${endUtc}`);
      if (!res.ok) throw new Error(`cost request failed: ${res.status}`);
      return (await res.json()) as CostResponse;
    },
    staleTime: 60_000,
  });
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

/**
 * A dollar figure, or an explicit statement that there is not one.
 *
 * The `withheld` text is required rather than optional on purpose: every
 * absent number on this page has a specific reason, and a generic
 * placeholder would let one absence stand in for all of them.
 */
function Amount({
  value,
  withheld,
  className,
}: {
  value: number | null;
  withheld: string;
  className?: string;
}) {
  if (value === null) {
    return (
      <span
        className={cn("text-muted-foreground text-sm italic", className)}
        data-testid="withheld"
      >
        {withheld}
      </span>
    );
  }
  return (
    <span className={cn("tabular-nums", className)} data-testid="amount">
      {usd.format(value)}
    </span>
  );
}

function Standing({ children }: { children: string }) {
  return (
    <span className="text-muted-foreground text-[11px] tracking-wide uppercase">{children}</span>
  );
}

function Section({
  title,
  standing,
  children,
}: {
  title: string;
  standing: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-border border-b py-4 last:border-b-0">
      <header className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium">{title}</h3>
        <Standing>{standing}</Standing>
      </header>
      {children}
    </section>
  );
}

export function CostPanel({ cost }: { cost: CostResponse | undefined }) {
  if (!cost) {
    return (
      <div className="text-muted-foreground p-4 text-sm" role="status">
        Loading cost…
      </div>
    );
  }

  const tokens = cost.agent_token_cost;
  const avoided = cost.cost_avoided;

  return (
    <div className="flex flex-col px-4">
      <Section title="Agent token cost" standing="measured">
        <div className="text-2xl">
          <Amount value={tokens.measured_usd} withheld="No session in this window was priced" />
        </div>
        <p className="text-muted-foreground mt-1 text-xs">
          {/* The denominator, always, and in front of the reader rather than
              derivable by them. */}
          {tokens.priced_sessions} of {tokens.sessions}{" "}
          {tokens.sessions === 1 ? "session" : "sessions"} priced
        </p>
        {tokens.coverage_note ? (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-500" role="note">
            {tokens.coverage_note}
          </p>
        ) : null}

        {tokens.by_agent.length > 0 ? (
          <ul className="mt-3 space-y-1">
            {tokens.by_agent.map((row) => (
              <li
                key={row.agent_id ?? "unbound"}
                className="flex items-baseline justify-between gap-3 text-sm"
              >
                <span className={cn(!row.agent_name_resolved && "text-muted-foreground italic")}>
                  {/* A deleted agent is not a rendering bug, so it is named
                      as unresolved rather than left blank. */}
                  {row.agent_name_resolved
                    ? row.agent_name
                    : `Unresolved agent ${(row.agent_id ?? "").slice(0, 8)}`}
                </span>
                <Amount value={row.measured_usd} withheld="not priced" className="text-sm" />
              </li>
            ))}
          </ul>
        ) : null}
      </Section>

      <Section title="Cost avoided by subscription routing" standing="modelled">
        <div className="text-2xl">
          <Amount
            value={avoided.modelled_usd}
            withheld={
              cost.basis.determinate
                ? "Not applicable: this spend was billed per token"
                : "Withheld: the provider kind is unknown, so this cannot be split"
            }
          />
        </div>
        <p className="text-muted-foreground mt-1 text-xs">{avoided.explanation}</p>
        {cost.basis.note ? (
          <p className="text-muted-foreground mt-2 text-xs" role="note">
            {cost.basis.note}
          </p>
        ) : null}
      </Section>

      <Section title="Warehouse cost, Airbrx versus direct" standing="unavailable">
        {/* No number here at all — not even a zero. The panel's job in this
            state is to say what it would need. */}
        <p className="text-sm" role="note">
          {cost.warehouse.explanation}
        </p>
        <ul className="text-muted-foreground mt-2 list-disc pl-5 text-xs">
          {cost.warehouse.requires.map((requirement) => (
            <li key={requirement}>{requirement}</li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
