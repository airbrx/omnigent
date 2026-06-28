/**
 * Admin → Sessions (``/admin/sessions``).
 *
 * Sessions across all users. Filters come from the URL so the Users/Hosts
 * cross-links are linkable: ``?user=`` (a user's sessions), ``?host=`` (a
 * host's sessions), ``?q=`` (title search). Rows open the normal chat view.
 */

import { useCallback, useEffect, useState } from "react";
import { XIcon } from "lucide-react";
import { useNavigate, useSearchParams } from "@/lib/routing";
import { type AdminSession, listSessions } from "@/lib/adminApi";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { formatEpoch, formatTokens, formatUsd } from "./format";

export function SessionsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const user = params.get("user") ?? "";
  const host = params.get("host") ?? "";
  const q = params.get("q") ?? "";

  const [sessions, setSessions] = useState<AdminSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState(q);

  const load = useCallback(async () => {
    setSessions(null);
    const rows = await listSessions({
      user: user || undefined,
      host: host || undefined,
      q: q || undefined,
    });
    if (rows === null) {
      setError("Could not load sessions.");
      setSessions([]);
      return;
    }
    setError(null);
    setSessions(rows);
  }, [user, host, q]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => setSearch(q), [q]);

  const clearParam = (key: string) => {
    const next = new URLSearchParams(params);
    next.delete(key);
    setParams(next, { replace: true });
  };
  const submitSearch = () => {
    const next = new URLSearchParams(params);
    if (search.trim()) next.set("q", search.trim());
    else next.delete("q");
    setParams(next, { replace: true });
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitSearch()}
          onBlur={submitSearch}
          placeholder="Search titles…"
          className="h-8 max-w-xs text-sm"
          aria-label="Search sessions by title"
        />
        {user && <FilterChip label="user" value={user} onClear={() => clearParam("user")} />}
        {host && <FilterChip label="host" value={host} onClear={() => clearParam("host")} />}
      </div>

      {error !== null && (
        <div role="alert" className="mb-4 text-sm text-destructive">
          {error}
        </div>
      )}
      {sessions === null && <p className="text-sm text-muted-foreground">Loading…</p>}
      {sessions !== null && sessions.length === 0 && error === null && (
        <p className="text-sm text-muted-foreground">No sessions match.</p>
      )}

      {sessions !== null && sessions.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Owner</th>
                <th className="px-3 py-2 font-medium">Host</th>
                <th className="px-3 py-2 font-medium">Updated</th>
                <th className="px-3 py-2 text-right font-medium">Tokens</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr
                  key={s.id}
                  data-testid="admin-session-row"
                  className="cursor-pointer border-t border-border hover:bg-muted/40"
                  onClick={() => navigate(`/c/${s.id}`)}
                >
                  <td className="px-3 py-2 align-middle font-medium">
                    <div className="max-w-[24rem] truncate" title={s.title ?? undefined}>
                      {s.title ?? <span className="text-muted-foreground">Untitled</span>}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle text-muted-foreground">
                    {s.owner ?? <span className="text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">
                    {s.host ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className={
                            "size-1.5 shrink-0 rounded-full " +
                            (s.host_online ? "bg-emerald-500" : "bg-muted-foreground/40")
                          }
                          aria-label={s.host_online ? "online" : "offline"}
                        />
                        {s.host}
                      </span>
                    ) : (
                      <span className="text-xs">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">
                    {formatEpoch(s.updated_at)}
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums text-muted-foreground">
                    {formatTokens(s.total_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums font-medium">
                    {formatUsd(s.cost_usd)}
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

function FilterChip({
  label,
  value,
  onClear,
}: {
  label: string;
  value: string;
  onClear: () => void;
}) {
  return (
    <Badge variant="secondary" className="gap-1 font-normal">
      <span className="text-muted-foreground">{label}:</span>
      {value}
      <button
        type="button"
        onClick={onClear}
        aria-label={`Clear ${label} filter`}
        className="ml-0.5 rounded-full hover:text-foreground"
      >
        <XIcon className="size-3" />
      </button>
    </Badge>
  );
}
