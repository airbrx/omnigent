/**
 * Shared chrome for the admin-only settings sections that are plain tables
 * (Sessions / Hosts): the ``is_admin`` gate plus a scrolling page with a
 * title. Members / Policies / Sharing own their own gate; these two are
 * simple enough that the gate lives here to keep them focused on content.
 *
 * Client gating is UX only — every ``/v1/admin/*`` route is enforced
 * server-side by an ``is_admin`` check, so a non-admin who reaches the URL
 * directly still gets 403s from the API.
 */

import { type ReactNode, useEffect, useState } from "react";
import { getCurrentIsAdmin, resolveIdentity } from "@/lib/identity";
import { PageScroll } from "@/components/PageScroll";

export function AdminSettingsSection({ title, children }: { title: string; children: ReactNode }) {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      await resolveIdentity();
      if (alive) setIsAdmin(getCurrentIsAdmin());
    })();
    return () => {
      alive = false;
    };
  }, []);

  // min-h-full so the AppShell outlet governs height — a child view, not a
  // full-page replacement.
  if (isAdmin === null) {
    return (
      <div className="flex min-h-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  // Non-admin: hard stop. The server would also 403 — this is just UX so the
  // section doesn't render a broken table.
  if (isAdmin === false) {
    return (
      <div className="mx-auto w-full max-w-2xl px-6 py-12">
        <h1 className="mb-2 text-2xl font-semibold">{title}</h1>
        <p className="text-sm text-muted-foreground">You don't have admin access.</p>
      </div>
    );
  }

  // Wide column: these are dense multi-column tables (host/owner/status/
  // version/os…), not prose — the default max-w-3xl squeezes them into a
  // horizontal scroll while the page sits half-empty.
  return (
    <PageScroll contentClassName="px-6" maxWidthClassName="max-w-[1400px]">
      <h1 className="mb-6 text-2xl font-semibold">{title}</h1>
      {children}
    </PageScroll>
  );
}
