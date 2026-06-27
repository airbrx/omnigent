/**
 * Admin → Users (``/admin/users``).
 *
 * Every real user with an admin/member badge and a usage rollup. The
 * "Owned" and "Hosts" counts are links that carry a ``?user=`` filter into
 * the Sessions and Hosts views.
 */

import { useCallback, useEffect, useState } from "react";
import { RefreshCwIcon } from "lucide-react";
import { Link } from "@/lib/routing";
import { getCurrentUserId } from "@/lib/identity";
import { type AdminUser, listAllUsers } from "@/lib/adminApi";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { formatHosts, formatTokens, formatUsd } from "./format";

export function UsersPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [hidden, setHidden] = useState(0);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const meId = getCurrentUserId();

  const refresh = useCallback(async () => {
    const result = await listAllUsers();
    if (result === null) {
      setError("Could not load users. You may not have admin permission.");
      setUsers([]);
      setHidden(0);
      return;
    }
    setError(null);
    setUsers(result.users);
    setHidden(result.hidden);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const needle = filter.trim().toLowerCase();
  const shown = (users ?? []).filter((u) => !needle || u.user_id.toLowerCase().includes(needle));

  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by email…"
          className="h-8 max-w-xs text-sm"
          aria-label="Filter users by email"
        />
        <div className="ml-auto flex items-center gap-3">
          {hidden > 0 && (
            <span
              className="text-xs text-muted-foreground"
              title="Accounts that own no session and only hold an invite grant are hidden."
            >
              {hidden} invite-only {hidden === 1 ? "account" : "accounts"} hidden
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={() => void refresh()}>
            <RefreshCwIcon /> Refresh
          </Button>
        </div>
      </div>

      {error !== null && (
        <div
          role="alert"
          className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {users !== null && shown.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">User</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 text-right font-medium">Owned</th>
                <th className="px-3 py-2 text-right font-medium">Hosts</th>
                <th className="px-3 py-2 text-right font-medium">Tokens</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.user_id} data-testid="admin-user-row" className="border-t border-border">
                  <td className="px-3 py-2 align-middle">
                    <span className="font-medium">{u.user_id}</span>
                    {u.user_id === meId && (
                      <span className="ml-2 text-xs text-muted-foreground">(you)</span>
                    )}
                  </td>
                  <td className="px-3 py-2 align-middle">
                    {u.is_admin ? <Badge>Admin</Badge> : <Badge variant="secondary">Member</Badge>}
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums">
                    <Link
                      to={`/admin/sessions?user=${encodeURIComponent(u.user_id)}`}
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                      title="View this user's sessions"
                    >
                      {u.session_count}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums">
                    <Link
                      to={`/admin/hosts?user=${encodeURIComponent(u.user_id)}`}
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                      title="View this user's hosts"
                    >
                      {formatHosts(u.host_count, u.online_host_count)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums text-muted-foreground">
                    {formatTokens(u.total_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums font-medium">
                    {formatUsd(u.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {users !== null && shown.length === 0 && error === null && (
        <p className="text-sm text-muted-foreground">
          {needle ? "No users match that filter." : "No users yet."}
        </p>
      )}
    </div>
  );
}
