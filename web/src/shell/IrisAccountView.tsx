import { Button } from "@/components/ui/button";

// The row shapes GET /v1/iris/account returns. They are the whole contract:
// a row is built from these named fields and nothing else, so a key the
// server did not promise (a report body, say) has no way onto the page.
export interface IrisRankedRow {
  tenant_id: string;
  name: string | null;
  hit_rate: number | null;
  hit_rate_denominator: number;
  requests: number;
  cache_misses: number;
  covered_days: number;
  requested_days: number;
  captured_at: number;
  age_seconds: number;
}

export type IrisQuarantineReason =
  | "tenant_mismatch"
  | "unreadable_config"
  | "metrics_disagree"
  | "incomplete_period"
  | "never_collected";

export interface IrisQuarantinedRow {
  tenant_id: string;
  name: string | null;
  reason: IrisQuarantineReason;
  detail: string | null;
  covered_days?: number | null;
  requested_days?: number | null;
  captured_at?: number | null;
}

export interface IrisAccount {
  generated_at: number;
  tenants: number;
  ranked: IrisRankedRow[];
  quarantined: IrisQuarantinedRow[];
}

export interface IrisAccountTenant {
  tenant_id: string;
  name?: string;
  fixture: boolean;
  /** Whether this tenant's execution host is connected right now.
   *
   * `null` — and `undefined`, from a server that predates the field — mean the
   * server could not tell, which is NOT the same as offline. Only `false`
   * disables the row. Rendering unknown as offline would let a missing lookup
   * grey out every tenant and manufacture an outage. */
  host_online?: boolean | null;
}

const REASON_LABEL: Record<IrisQuarantineReason, string> = {
  tenant_mismatch: "Capture names another tenant",
  unreadable_config: "Could not be read",
  metrics_disagree: "Metrics disagree",
  incomplete_period: "Incomplete period",
  never_collected: "Never collected",
};

/**
 * The id shown beside a tenant's name, so two similarly-named tenants stay
 * distinguishable.
 *
 * Truncating is only worth it for an id nobody reads anyway: a UUID costs 36
 * characters to say nothing. A short id is already legible, and cutting it
 * produces a fragment that is worse than useless — `fixture-iris` became
 * `fixture-`, which identifies nothing and looks like a rendering bug.
 */
export function shortId(tenantId: string): string {
  return tenantId.length > 20 ? tenantId.slice(0, 8) : tenantId;
}

export function formatAge(seconds: number): string {
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

// A null rate is a measured zero-traffic window, not an unknown: the server
// ranked it. Say what it is rather than printing a dash that reads as missing.
export function formatRate(rate: number | null): string {
  return rate === null ? "no traffic" : `${(rate * 100).toFixed(1)}%`;
}

const count = (n: number) => n.toLocaleString("en-US");

export type OrderedRow = IrisAccountTenant &
  (
    | { status: "ranked"; row: IrisRankedRow }
    | { status: "quarantined"; row: IrisQuarantinedRow }
    | { status: "missing" }
  );

/**
 * The account decides the order; the catalog decides the set. Every bound
 * tenant is a row even when the account request failed, so a triage outage
 * never takes the drill-in with it.
 */
export function orderedRows(tenants: IrisAccountTenant[], account: IrisAccount | null): OrderedRow[] {
  const byId = new Map(tenants.map((t) => [t.tenant_id, t]));
  const out: OrderedRow[] = [];
  const placed = new Set<string>();
  if (account) {
    for (const row of account.ranked) {
      const tenant = byId.get(row.tenant_id);
      if (tenant && !placed.has(row.tenant_id)) {
        out.push({ ...tenant, status: "ranked", row });
        placed.add(row.tenant_id);
      }
    }
    for (const row of account.quarantined) {
      const tenant = byId.get(row.tenant_id);
      if (tenant && !placed.has(row.tenant_id)) {
        out.push({ ...tenant, status: "quarantined", row });
        placed.add(row.tenant_id);
      }
    }
  }
  for (const tenant of tenants) if (!placed.has(tenant.tenant_id)) out.push({ ...tenant, status: "missing" });
  return out;
}

interface IrisAccountViewProps {
  tenants: IrisAccountTenant[];
  account: IrisAccount | null;
  accountError: string;
  busy: boolean;
  // The account query is a separate request from the catalog (see
  // IrisWorkspace), so a tenant can be on screen well before its row is
  // known to be ranked or quarantined. Distinguishing "still loading" from
  // "the server didn't mention you" keeps a fast catalog response from
  // reading as a triage verdict.
  loading?: boolean;
  openLabel: string;
  onOpen: (tenantId: string) => void;
}

function coverage(covered: number | null | undefined, requested: number | null | undefined) {
  return covered == null || requested == null ? "" : `${covered} / ${requested} days`;
}

function Standing({
  entry,
  accountError,
  generatedAt,
  loading,
}: {
  entry: OrderedRow;
  accountError: string;
  generatedAt: number | null;
  loading: boolean;
}) {
  if (entry.status === "ranked") {
    const r = entry.row;
    return (
      <>
        <td className="text-right tabular-nums">{formatRate(r.hit_rate)}</td>
        <td className="text-right tabular-nums">{count(r.cache_misses)}</td>
        <td className="text-right tabular-nums">{count(r.requests)}</td>
        <td>{coverage(r.covered_days, r.requested_days)}</td>
        <td>{formatAge(r.age_seconds)}</td>
      </>
    );
  }
  if (entry.status === "quarantined") {
    const r = entry.row;
    const label = REASON_LABEL[r.reason] ?? r.reason;
    return (
      <>
        <td colSpan={3}>{r.detail ? `${label}: ${r.detail}` : label}</td>
        <td>{coverage(r.covered_days, r.requested_days)}</td>
        <td>
          {typeof generatedAt === "number" && typeof r.captured_at === "number"
            ? formatAge(Math.max(0, generatedAt - r.captured_at))
            : ""}
        </td>
      </>
    );
  }
  if (loading) {
    return (
      <td colSpan={5}>
        <span role="status">Ranking…</span>
      </td>
    );
  }
  return (
    <td colSpan={5}>{accountError ? "Not ranked: account unavailable" : "Not ranked: not in the account response"}</td>
  );
}

export function IrisAccountView({ tenants, account, accountError, busy, loading = false, openLabel, onOpen }: IrisAccountViewProps) {
  const rows = orderedRows(tenants, account);
  const generatedAt = account?.generated_at ?? null;
  const stillRanking = loading && account === null;
  return (
    <div className="flex flex-col gap-3">
      {accountError && <p role="alert">{accountError}</p>}
      <table className="w-full text-sm" aria-label="Tenants in this account">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th>Tenant</th>
            <th className="text-right">Hit rate</th>
            <th className="text-right">Cache misses</th>
            <th className="text-right">Requests</th>
            <th>Coverage</th>
            <th>Captured</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((entry) => (
            <tr key={entry.tenant_id} data-tenant={entry.tenant_id} data-status={entry.status} className="border-t">
              <td>
                <div>{entry.name || entry.tenant_id}</div>
                <div className="text-muted-foreground text-xs">
                  {entry.name ? `${shortId(entry.tenant_id)} · ` : ""}
                  {entry.fixture ? "synthetic fixture" : "read-only"}
                  {entry.host_online === false ? " · host offline" : ""}
                </div>
              </td>
              <Standing
                entry={entry}
                accountError={accountError}
                generatedAt={generatedAt}
                loading={stillRanking}
              />
              <td className="text-right">
                <Button
                  size="sm"
                  disabled={busy || entry.host_online === false}
                  aria-label={`${openLabel}: ${entry.name || entry.tenant_id}`}
                  onClick={() => onOpen(entry.tenant_id)}
                >
                  {openLabel}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
