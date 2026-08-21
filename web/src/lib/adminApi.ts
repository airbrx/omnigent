/**
 * Client for the admin discovery API (``/v1/admin/*``).
 *
 * Powers the OIDC/SSO admin surface — a user list and an admin's view
 * of any user's sessions — where the accounts-mode Members page never
 * mounts. Every endpoint is gated on the caller's ``is_admin`` flag by
 * the server; these helpers resolve to ``null`` on any non-2xx so the
 * UI can render a friendly "no access / unreachable" state instead of
 * throwing.
 */

import { authenticatedFetch } from "./identity";

/** A user row from ``GET /v1/admin/users`` (with a usage rollup). */
export interface AdminUser {
  user_id: string;
  is_admin: boolean;
  cost_usd: number;
  total_tokens: number;
  session_count: number;
  /** Hosts this user owns (all registered). */
  host_count: number;
  /** The live subset of ``host_count``. */
  online_host_count: number;
}

/** Response of ``GET /v1/admin/users``. */
export interface AdminUserList {
  users: AdminUser[];
  /** Count of invite-only phantom accounts filtered out of ``users``. */
  hidden: number;
}

/** A session row from ``GET /v1/admin/users/{id}/sessions``. */
export interface AdminSession {
  id: string;
  title: string | null;
  created_at: number;
  updated_at: number;
  cost_usd: number;
  total_tokens: number;
  /** This user's role on the session: "owner" | "manage" | "edit" | "read". */
  role: string | null;
  /** The session's owner (the LEVEL_OWNER grantee), or null if none. */
  owner: string | null;
  /** Whether this user is the session's owner. */
  is_owner: boolean;
  /** Friendly name of the host the session is bound to (raw id if the host
   * was deleted, null if unbound). */
  host: string | null;
  /** Whether that host is currently live. */
  host_online: boolean;
}

/** Aggregate usage across a user's sessions. */
export interface UsageTotals {
  cost_usd: number;
  total_tokens: number;
  session_count: number;
}

/** Response of ``GET /v1/admin/users/{id}/sessions``. */
export interface AdminUserSessions {
  sessions: AdminSession[];
  totals: UsageTotals;
}

/**
 * GET /v1/admin/users — list every real user + the hidden-phantom count
 * (admin only).
 *
 * :returns: ``{users, hidden}``, or ``null`` on error / forbidden.
 */
export async function listAllUsers(): Promise<AdminUserList | null> {
  try {
    const res = await authenticatedFetch("/v1/admin/users");
    if (!res.ok) return null;
    const data = (await res.json()) as { users: AdminUser[]; hidden?: number };
    return { users: data.users, hidden: data.hidden ?? 0 };
  } catch {
    return null;
  }
}

/**
 * GET /v1/admin/users/{id}/sessions — list a user's sessions + usage totals
 * (admin only).
 *
 * :param userId: The user whose sessions to list.
 * :returns: The sessions + totals, or ``null`` on error / forbidden.
 */
export async function listUserSessions(userId: string): Promise<AdminUserSessions | null> {
  try {
    const res = await authenticatedFetch(`/v1/admin/users/${encodeURIComponent(userId)}/sessions`);
    if (!res.ok) return null;
    return (await res.json()) as AdminUserSessions;
  } catch {
    return null;
  }
}

/** A host row from ``GET /v1/admin/hosts``. */
export interface AdminHost {
  host_id: string;
  name: string;
  owner: string;
  online: boolean;
  /** Last-known version the host reported (null if never reported). */
  version: string | null;
  /** OS + arch the host reported, e.g. "Darwin 23.5.0 (arm64)" (null if not). */
  os: string | null;
  /** Whether the host's build differs from the server's (null if no version reported). */
  outdated: boolean | null;
  /** Unix epoch when the host's login token expires (null if none reported). */
  login_token_expires_at: number | null;
  /** Per-harness readiness map, or null if the host never reported it. */
  harnesses: Record<string, boolean | string> | null;
  /** Unix epoch seconds the host was last seen. */
  last_seen: number;
  created_at: number;
}

/** Server build info for the admin header + the host-upgrade popup. */
export interface AdminServerInfo {
  version: string;
  /** Git commit the running build was stamped from (null in a source checkout). */
  commit: string | null;
  /** Unix epoch seconds the build was stamped (null when unknown). */
  built_at: number | null;
  /** Build-identity label hosts are compared against, e.g. "0.3.0.dev0 (c983f9b0)". */
  version_label: string;
  /** One-liner that upgrades a host to this server's version (null if no domain). */
  install_command: string | null;
}

// Type aliases (not interfaces) so they satisfy buildQuery's
// `Record<string, string | undefined>` param — interfaces are open to
// declaration merging and so lack an implicit index signature.

// The explicit index signature keeps these assignable to buildQuery's
// Record<string, string | undefined> — interfaces (unlike object-literal type
// aliases) have no implicit one. Every named field is an optional string, so it
// stays compatible with the signature.

/** Filters for the global sessions listing. */
export interface SessionFilters {
  user?: string;
  host?: string;
  q?: string;
  [key: string]: string | undefined;
}

/** Filters for the hosts listing. */
export interface HostFilters {
  user?: string;
  status?: string;
  version?: string;
  [key: string]: string | undefined;
}

function buildQuery(params: Record<string, string | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/**
 * GET /v1/admin/sessions — sessions across users, with optional filters
 * (admin only).
 *
 * :returns: The session rows, or ``null`` on error / forbidden.
 */
export async function listSessions(filters: SessionFilters = {}): Promise<AdminSession[] | null> {
  try {
    const res = await authenticatedFetch(`/v1/admin/sessions${buildQuery(filters)}`);
    if (!res.ok) return null;
    return ((await res.json()) as { sessions: AdminSession[] }).sessions;
  } catch {
    return null;
  }
}

/**
 * GET /v1/admin/hosts — all hosts, with optional filters (admin only).
 *
 * :returns: The host rows, or ``null`` on error / forbidden.
 */
export async function listAdminHosts(filters: HostFilters = {}): Promise<AdminHost[] | null> {
  try {
    const res = await authenticatedFetch(`/v1/admin/hosts${buildQuery(filters)}`);
    if (!res.ok) return null;
    return ((await res.json()) as { hosts: AdminHost[] }).hosts;
  } catch {
    return null;
  }
}

/**
 * GET /v1/admin/server — server version/build info (admin only).
 *
 * :returns: The server info, or ``null`` on error / forbidden.
 */
export async function getServerInfo(): Promise<AdminServerInfo | null> {
  try {
    const res = await authenticatedFetch("/v1/admin/server");
    if (!res.ok) return null;
    return (await res.json()) as AdminServerInfo;
  } catch {
    return null;
  }
}
