// The account view is the one place a person sees more than one tenant at
// once, so what it renders is a claim about the account. These tests read
// the rendered text — not values handed to stubs — because the packaged
// workspace's node:vm suite passed while a badge rendered a phrase twice
// (Q16), and that blind spot is not being reintroduced here.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import {
  formatAge,
  formatRate,
  IrisAccountView,
  orderedRows,
  type IrisAccount,
} from "./IrisAccountView";

const TENANTS = [
  { tenant_id: "aaaaaaaa-0000-0000-0000-000000000001", name: "Hot", fixture: false },
  { tenant_id: "bbbbbbbb-0000-0000-0000-000000000002", name: "Cold", fixture: true },
  { tenant_id: "cccccccc-0000-0000-0000-000000000003", fixture: false },
];

const ACCOUNT: IrisAccount = {
  generated_at: 1_800_000_000,
  tenants: 3,
  ranked: [
    {
      tenant_id: TENANTS[0].tenant_id,
      name: "Hot",
      hit_rate: 0.6,
      hit_rate_denominator: 1000,
      requests: 1000,
      cache_misses: 400,
      covered_days: 7,
      requested_days: 7,
      captured_at: 1_800_000_000 - 4 * 86400,
      age_seconds: 4 * 86400,
    },
  ],
  quarantined: [
    { tenant_id: TENANTS[1].tenant_id, name: "Cold", reason: "never_collected", detail: null },
    {
      tenant_id: TENANTS[2].tenant_id,
      name: null,
      reason: "incomplete_period",
      detail: null,
      covered_days: 3,
      requested_days: 7,
      captured_at: 1_800_000_000 - 60,
    },
  ],
};

function rowFor(text: string) {
  const row = screen.getAllByRole("row").find((r) => r.textContent?.includes(text));
  if (!row) throw new Error(`no row containing ${text}`);
  return row;
}

it("ranks first, in the server's order, then quarantines with a reason — never a blank or a zero", () => {
  vi.useFakeTimers({ now: 1_800_000_000 * 1000 });
  try {
    render(
      <IrisAccountView tenants={TENANTS} account={ACCOUNT} accountError="" busy={false} openLabel="Open workspace" onOpen={vi.fn()} />,
    );
    const rows = screen.getAllByRole("row").slice(1); // drop the header
    expect(rows.map((r) => r.getAttribute("data-tenant"))).toEqual(TENANTS.map((t) => t.tenant_id));

    const hot = rowFor("Hot");
    expect(hot).toHaveTextContent("60.0%");
    expect(hot).toHaveTextContent("400");
    expect(hot).toHaveTextContent("1,000");
    expect(hot).toHaveTextContent("4 d ago");
    expect(hot).toHaveTextContent("7 / 7 days");

    const cold = rowFor("Cold");
    expect(cold).toHaveTextContent("Never collected");
    expect(cold).not.toHaveTextContent("0%");
    expect(cold).not.toHaveTextContent("—");
    expect(cold).toHaveTextContent("synthetic fixture");

    const partial = rowFor("cccccccc");
    expect(partial).toHaveTextContent("Incomplete period");
    expect(partial).toHaveTextContent("3 / 7 days");
    expect(partial).toHaveTextContent("1 min ago");
  } finally {
    vi.useRealTimers();
  }
});

it("drills in per row through the callback, with the row's own tenant id", () => {
  const onOpen = vi.fn();
  render(
    <IrisAccountView tenants={TENANTS} account={ACCOUNT} accountError="" busy={false} openLabel="Open workspace" onOpen={onOpen} />,
  );
  fireEvent.click(within(rowFor("Cold")).getByRole("button", { name: "Open workspace" }));
  expect(onOpen).toHaveBeenCalledWith(TENANTS[1].tenant_id);
  expect(onOpen).toHaveBeenCalledTimes(1);
});

it("disables every drill-in while one is in flight", () => {
  render(
    <IrisAccountView tenants={TENANTS} account={ACCOUNT} accountError="" busy={true} openLabel="Open workspace" onOpen={vi.fn()} />,
  );
  for (const button of screen.getAllByRole("button", { name: "Open workspace" })) expect(button).toBeDisabled();
});

it("lets nothing from a report body reach the DOM, even if the server sent it", () => {
  const leaky = {
    ...ACCOUNT,
    ranked: [{ ...ACCOUNT.ranked[0], findings: [{ explanation: "LEAKED FINDING" }], evidence: [{ data: "select secret" }] }],
  } as unknown as IrisAccount;
  render(
    <IrisAccountView tenants={TENANTS} account={leaky} accountError="" busy={false} openLabel="Open workspace" onOpen={vi.fn()} />,
  );
  expect(document.body.textContent).not.toContain("LEAKED FINDING");
  expect(document.body.textContent).not.toContain("select secret");
});

it("still offers every tenant for drill-in when the account request failed, and says why", () => {
  render(
    <IrisAccountView
      tenants={TENANTS}
      account={null}
      accountError="The session list could not be read, so the account cannot be shown"
      busy={false}
      openLabel="Open workspace"
      onOpen={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("session list could not be read");
  expect(screen.getAllByRole("button", { name: "Open workspace" })).toHaveLength(3);
  expect(rowFor("Hot")).toHaveTextContent("Not ranked: account unavailable");
  expect(rowFor("Hot")).not.toHaveTextContent("%");
});

it("shows a quarantined row's detail when there is one", () => {
  const account: IrisAccount = {
    ...ACCOUNT,
    ranked: [],
    quarantined: [
      { tenant_id: TENANTS[0].tenant_id, name: "Hot", reason: "metrics_disagree", detail: "more hits than requests", captured_at: 1 },
      { tenant_id: TENANTS[1].tenant_id, name: "Cold", reason: "tenant_mismatch", detail: "the newest capture found for this tenant names a different tenant; it was discarded" },
      { tenant_id: TENANTS[2].tenant_id, name: null, reason: "unreadable_config", detail: "the newest report could not be read (HTTP 502)" },
    ],
  };
  render(
    <IrisAccountView tenants={TENANTS} account={account} accountError="" busy={false} openLabel="Start chat" onOpen={vi.fn()} />,
  );
  expect(rowFor("Hot")).toHaveTextContent("Metrics disagree: more hits than requests");
  expect(rowFor("Cold")).toHaveTextContent("Capture names another tenant");
  expect(rowFor("cccccccc")).toHaveTextContent("Could not be read: the newest report could not be read (HTTP 502)");
  expect(screen.getAllByRole("button", { name: "Start chat" })).toHaveLength(3);
});

it("orders by the account, then appends any tenant the account did not mention", () => {
  const rows = orderedRows(TENANTS, { ...ACCOUNT, quarantined: [ACCOUNT.quarantined[0]] });
  expect(rows.map((r) => r.tenant_id)).toEqual([TENANTS[0].tenant_id, TENANTS[1].tenant_id, TENANTS[2].tenant_id]);
  expect(rows[2].status).toBe("missing");
  expect(orderedRows(TENANTS, null).map((r) => r.status)).toEqual(["missing", "missing", "missing"]);
});

it("formats ages and rates the way the rows expect", () => {
  expect(formatAge(0)).toBe("just now");
  expect(formatAge(59)).toBe("just now");
  expect(formatAge(60)).toBe("1 min ago");
  expect(formatAge(3 * 3600 + 59)).toBe("3 h ago");
  expect(formatAge(2 * 86400 + 3600)).toBe("2 d ago");
  expect(formatRate(0.6)).toBe("60.0%");
  expect(formatRate(0.12345)).toBe("12.3%");
  expect(formatRate(null)).toBe("no traffic");
});
