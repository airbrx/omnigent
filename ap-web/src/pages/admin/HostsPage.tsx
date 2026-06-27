/**
 * Admin → Hosts (``/admin/hosts``).
 *
 * Every host across users, with owner, online state, last-known version,
 * configured harnesses, and last-seen. Filters: ``?user=`` (chip from the
 * Users cross-link), ``?status=`` online/offline, ``?version=``. A row links
 * to that host's sessions (``/admin/sessions?host=``).
 */

import { useCallback, useEffect, useState } from "react";
import { XIcon } from "lucide-react";
import { useNavigate, useSearchParams } from "@/lib/routing";
import { type AdminHost, listAdminHosts } from "@/lib/adminApi";
import { Badge } from "@/components/ui/badge";
import { formatEpoch } from "./format";

const STATUSES = [
  { key: "", label: "All" },
  { key: "online", label: "Online" },
  { key: "offline", label: "Offline" },
] as const;

export function HostsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const user = params.get("user") ?? "";
  const status = params.get("status") ?? "";
  const version = params.get("version") ?? "";

  const [hosts, setHosts] = useState<AdminHost[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setHosts(null);
    const rows = await listAdminHosts({
      user: user || undefined,
      status: status || undefined,
      version: version || undefined,
    });
    if (rows === null) {
      setError("Could not load hosts.");
      setHosts([]);
      return;
    }
    setError(null);
    setHosts(rows);
  }, [user, status, version]);

  useEffect(() => {
    void load();
  }, [load]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {STATUSES.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setParam("status", s.key)}
              className={
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors " +
                (status === s.key
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {s.label}
            </button>
          ))}
        </div>
        {user && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <span className="text-muted-foreground">user:</span>
            {user}
            <button
              type="button"
              onClick={() => setParam("user", "")}
              aria-label="Clear user filter"
              className="ml-0.5 rounded-full hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          </Badge>
        )}
        {version && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <span className="text-muted-foreground">version:</span>
            {version}
            <button
              type="button"
              onClick={() => setParam("version", "")}
              aria-label="Clear version filter"
              className="ml-0.5 rounded-full hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          </Badge>
        )}
      </div>

      {error !== null && (
        <div role="alert" className="mb-4 text-sm text-destructive">
          {error}
        </div>
      )}
      {hosts === null && <p className="text-sm text-muted-foreground">Loading…</p>}
      {hosts !== null && hosts.length === 0 && error === null && (
        <p className="text-sm text-muted-foreground">No hosts match.</p>
      )}

      {hosts !== null && hosts.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Host</th>
                <th className="px-3 py-2 font-medium">Owner</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Version</th>
                <th className="px-3 py-2 font-medium">Harnesses</th>
                <th className="px-3 py-2 font-medium">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {hosts.map((h) => (
                <tr
                  key={h.host_id}
                  data-testid="admin-host-row"
                  className="cursor-pointer border-t border-border hover:bg-muted/40"
                  onClick={() => navigate(`/admin/sessions?host=${encodeURIComponent(h.host_id)}`)}
                  title="View this host's sessions"
                >
                  <td className="px-3 py-2 align-middle font-medium">{h.name}</td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">{h.owner}</td>
                  <td className="px-3 py-2 align-middle">
                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <span
                        className={
                          "size-1.5 shrink-0 rounded-full " +
                          (h.online ? "bg-emerald-500" : "bg-muted-foreground/40")
                        }
                      />
                      {h.online ? "online" : "offline"}
                    </span>
                  </td>
                  <td className="px-3 py-2 align-middle tabular-nums text-muted-foreground">
                    {h.version ?? <span className="text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">
                    {formatHarnesses(h.harnesses)}
                  </td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">
                    {formatEpoch(h.last_seen)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Ready harnesses joined ("claude-sdk, codex"), or "—" when none/unknown. */
function formatHarnesses(harnesses: Record<string, boolean | string> | null): string {
  if (!harnesses) return "—";
  const ready = Object.entries(harnesses)
    .filter(([, v]) => v === true)
    .map(([k]) => k);
  return ready.length > 0 ? ready.join(", ") : "—";
}
