/**
 * Admin layout: the shared chrome for the three admin views
 * (``/admin/users``, ``/admin/sessions``, ``/admin/hosts``).
 *
 * Owns the is_admin gate (so each page doesn't repeat it), the tab bar,
 * and the server-version line in the header. Child pages render through
 * the ``<Outlet/>``. Gating is client-side UX only — every ``/v1/admin/*``
 * route is enforced server-side by an is_admin check.
 */

import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "@/lib/routing";
import { getCurrentIsAdmin, resolveIdentity } from "@/lib/identity";
import { type AdminServerInfo, getServerInfo } from "@/lib/adminApi";
import { PageScroll } from "@/components/PageScroll";

const TABS = [
  { to: "/admin/users", label: "Users", seg: "users" },
  { to: "/admin/sessions", label: "Sessions", seg: "sessions" },
  { to: "/admin/hosts", label: "Hosts", seg: "hosts" },
] as const;

export function AdminLayout() {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [server, setServer] = useState<AdminServerInfo | null>(null);
  const location = useLocation();
  const activeSeg = location.pathname.split("/").filter(Boolean).at(-1) ?? "users";

  useEffect(() => {
    void (async () => {
      await resolveIdentity();
      const admin = getCurrentIsAdmin();
      setIsAdmin(admin);
      if (admin) setServer(await getServerInfo());
    })();
  }, []);

  if (isAdmin === null) {
    return (
      <div className="flex min-h-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (isAdmin === false) {
    return (
      <div className="mx-auto w-full max-w-2xl px-6 py-12">
        <h1 className="mb-2 text-2xl font-semibold">Admin</h1>
        <p className="text-sm text-muted-foreground">You don't have admin access.</p>
      </div>
    );
  }

  return (
    <PageScroll contentClassName="px-6">
      <div className="mb-4 flex items-baseline justify-between gap-4 pt-1">
        <h1 className="text-2xl font-semibold">Admin</h1>
        {server !== null && (
          <span className="truncate text-xs text-muted-foreground" title="Running server build">
            server {formatServer(server)}
          </span>
        )}
      </div>

      <nav className="mb-6 flex gap-1 border-b border-border">
        {TABS.map((t) => (
          <Link
            key={t.seg}
            to={t.to}
            className={
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors " +
              (activeSeg === t.seg
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground")
            }
          >
            {t.label}
          </Link>
        ))}
      </nav>

      <Outlet />
    </PageScroll>
  );
}

/** "0.3.0.dev0 (6fdc4b8c, built 6/27/2026)" — commit/date omitted when unknown. */
function formatServer(s: AdminServerInfo): string {
  const parts: string[] = [];
  if (s.commit) parts.push(s.commit.slice(0, 8));
  if (s.built_at) parts.push(`built ${new Date(s.built_at * 1000).toLocaleDateString()}`);
  return parts.length > 0 ? `${s.version} (${parts.join(", ")})` : s.version;
}
