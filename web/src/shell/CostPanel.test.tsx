import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostPanel, type CostResponse } from "./CostPanel";

function envelope(overrides: Partial<CostResponse> = {}): CostResponse {
  return {
    window: { start_utc: 0, end_utc: 86_400 },
    basis: { provider_kind: "subscription", metered: false, determinate: true, note: null },
    agent_token_cost: {
      standing: "measured",
      measured_usd: 12.5,
      sessions: 4,
      priced_sessions: 4,
      complete: true,
      coverage_note: null,
      by_agent: [
        {
          agent_id: "a".repeat(32),
          agent_name: "iris",
          agent_name_resolved: true,
          measured_usd: 12.5,
          sessions: 4,
          priced_sessions: 4,
          unpriced_sessions: 0,
          malformed_sessions: 0,
          priced_tokens: 1000,
          unpriced_tokens: 0,
          complete: true,
        },
      ],
    },
    cost_avoided: {
      standing: "modelled",
      modelled_usd: 12.5,
      billed_usd: 0,
      explanation: "What the measured tokens would have cost on metered per-token billing.",
    },
    warehouse: {
      standing: "unavailable",
      available: false,
      rates: {
        known: true,
        basis: "list",
        source: "Vendor list prices.",
        snowflake_per_credit: { standard: 2.0, enterprise: 3.0 },
        databricks_per_dbu: { sql_classic: 0.22, sql_serverless: 0.7 },
        caveat: "List prices, not this tenant's contract rate.",
      },
      quantity: {
        known: false,
        missing: ["warehouse_time_ms", "executions"],
        explanation: "The gateway reports warehouse_time_ms as unavailable, not as zero.",
      },
      explanation: "The missing piece is the measurement, not the price.",
    },
    ...overrides,
  };
}

/**
 * Scope a query to one section.
 *
 * The same dollar figure legitimately appears in two sections at once —
 * on a subscription, avoided cost EQUALS measured cost — so an unscoped
 * text query is ambiguous by design rather than by accident.
 */
function section(title: string): HTMLElement {
  const heading = screen.getByText(title);
  const element = heading.closest("section");
  if (!element) throw new Error(`no section found for "${title}"`);
  return element as HTMLElement;
}

describe("CostPanel", () => {
  it("shows the measured cost with its denominator", () => {
    render(<CostPanel cost={envelope()} />);
    const measured = section("Agent token cost");
    expect(within(measured).getAllByTestId("amount")[0]).toHaveTextContent("$12.50");
    expect(within(measured).getByText("4 of 4 sessions priced")).toBeInTheDocument();
  });

  it("labels each figure with its standing, so measured and modelled never read alike", () => {
    render(<CostPanel cost={envelope()} />);
    expect(screen.getByText("measured")).toBeInTheDocument();
    expect(screen.getByText("modelled")).toBeInTheDocument();
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });

  it("states an unmeasured total in words rather than as $0.00", () => {
    const cost = envelope();
    cost.agent_token_cost.measured_usd = null;
    cost.agent_token_cost.priced_sessions = 0;
    cost.agent_token_cost.complete = false;
    render(<CostPanel cost={cost} />);

    expect(screen.getByText("No session in this window was priced")).toBeInTheDocument();
    // The distinction the panel exists to preserve.
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
    expect(screen.getByText("0 of 4 sessions priced")).toBeInTheDocument();
  });

  it("surfaces the coverage caveat when the figure is partial", () => {
    const cost = envelope();
    cost.agent_token_cost.complete = false;
    cost.agent_token_cost.priced_sessions = 3;
    cost.agent_token_cost.coverage_note =
      "1 of 4 sessions recorded usage that could not be priced, so their cost is unknown, not zero.";
    render(<CostPanel cost={cost} />);

    expect(screen.getByText(/unknown, not zero/)).toBeInTheDocument();
  });

  it("withholds avoided cost when the provider kind is unknown", () => {
    const cost = envelope();
    cost.basis = {
      provider_kind: null,
      metered: null,
      determinate: false,
      note: "Provider kind is unknown, so billed and avoided are withheld rather than guessed.",
    };
    cost.cost_avoided.modelled_usd = null;
    cost.cost_avoided.billed_usd = null;
    render(<CostPanel cost={cost} />);

    expect(
      screen.getByText("Withheld: the provider kind is unknown, so this cannot be split"),
    ).toBeInTheDocument();
    expect(screen.getByText(/withheld rather than guessed/)).toBeInTheDocument();
  });

  it("distinguishes 'not applicable' from 'unknown' when spend really was metered", () => {
    const cost = envelope();
    cost.basis = { provider_kind: "key", metered: true, determinate: true, note: null };
    cost.cost_avoided.modelled_usd = null;
    render(<CostPanel cost={cost} />);

    expect(screen.getByText("Not applicable: this spend was billed per token")).toBeInTheDocument();
  });

  it("shows the known rates and the unmeasured consumption as separate things", () => {
    render(<CostPanel cost={envelope()} />);
    const warehouse = section("Warehouse cost, Airbrx versus direct");

    // The price is solved, and shown.
    expect(within(warehouse).getByText(/Rates: known/)).toBeInTheDocument();
    expect(
      within(warehouse).getByText(/Snowflake enterprise: \$3\.00 per credit/),
    ).toBeInTheDocument();
    expect(
      within(warehouse).getByText("List prices, not this tenant's contract rate."),
    ).toBeInTheDocument();

    // The measurement is not, and that is named as the actual blocker.
    expect(within(warehouse).getByText(/Consumption: not measured/)).toBeInTheDocument();
    expect(within(warehouse).getByText(/not as zero/)).toBeInTheDocument();
    expect(within(warehouse).getByText("warehouse_time_ms")).toBeInTheDocument();
  });

  it("names an unresolved agent instead of rendering a blank row", () => {
    const cost = envelope();
    cost.agent_token_cost.by_agent[0] = {
      ...cost.agent_token_cost.by_agent[0],
      agent_name: null,
      agent_name_resolved: false,
    };
    render(<CostPanel cost={cost} />);

    expect(screen.getByText(/Unresolved agent aaaaaaaa/)).toBeInTheDocument();
  });

  it("renders an unpriced agent row as not priced rather than zero", () => {
    const cost = envelope();
    cost.agent_token_cost.by_agent[0] = {
      ...cost.agent_token_cost.by_agent[0],
      measured_usd: null,
    };
    render(<CostPanel cost={cost} />);

    expect(screen.getByText("not priced")).toBeInTheDocument();
  });

  it("shows a loading state rather than an empty page", () => {
    render(<CostPanel cost={undefined} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
