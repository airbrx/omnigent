/**
 * Settings → Admin → Hosts (``/settings/hosts``).
 *
 * Every host across users, with owner, online state, last-known version,
 * configured harnesses, and last-seen. Filters: ``?user=`` (chip from the
 * Members cross-link), ``?status=`` online/offline, ``?version=``. A row links
 * to that host's sessions (``/settings/sessions?host=``). The is_admin gate +
 * page chrome are provided by AdminSettingsSection.
 */

import { useCallback, useEffect, useState } from "react";
import { CheckIcon, CopyIcon, XIcon } from "lucide-react";
import { useNavigate, useSearchParams } from "@/lib/routing";
import {
  type AdminHost,
  type AdminServerInfo,
  getServerInfo,
  listAdminHosts,
} from "@/lib/adminApi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatEpoch } from "@/lib/adminFormat";

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
  const [server, setServer] = useState<AdminServerInfo | null>(null);
  const [selected, setSelected] = useState<AdminHost | null>(null);

  useEffect(() => {
    void (async () => setServer(await getServerInfo()))();
  }, []);

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
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Host</th>
                <th className="px-3 py-2 font-medium">Owner</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Version</th>
                <th className="px-3 py-2 font-medium">OS</th>
                <th className="px-3 py-2 font-medium">Harnesses</th>
                <th className="px-3 py-2 font-medium">Token expires</th>
                <th className="px-3 py-2 font-medium">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {hosts.map((h) => (
                <tr
                  key={h.host_id}
                  data-testid="admin-host-row"
                  className="cursor-pointer border-t border-border hover:bg-muted/40"
                  onClick={() =>
                    navigate(`/settings/sessions?host=${encodeURIComponent(h.host_id)}`)
                  }
                  title="View this host's sessions"
                >
                  <td className="whitespace-nowrap px-3 py-2 align-middle font-medium">{h.name}</td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle text-muted-foreground">
                    {h.owner}
                  </td>
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
                  <td className="whitespace-nowrap px-3 py-2 align-middle tabular-nums">
                    {h.version ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(h);
                        }}
                        className="inline-flex items-center gap-1.5 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                        title="Version details & upgrade"
                      >
                        {h.outdated ? (
                          <span
                            className="size-1.5 shrink-0 rounded-full bg-amber-500"
                            aria-label="update available"
                          />
                        ) : (
                          h.outdated === false && (
                            <span
                              className="size-1.5 shrink-0 rounded-full bg-emerald-500"
                              aria-label="up to date"
                            />
                          )
                        )}
                        {h.version}
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle text-muted-foreground">
                    {h.os ?? <span className="text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 align-middle text-muted-foreground">
                    <div className="max-w-[16rem] truncate" title={formatHarnesses(h.harnesses)}>
                      {formatHarnesses(h.harnesses)}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle">
                    {h.login_token_expires_at ? (
                      <span
                        className={
                          h.login_token_expires_at * 1000 < Date.now()
                            ? "text-destructive"
                            : "text-muted-foreground"
                        }
                        title={
                          h.login_token_expires_at * 1000 < Date.now()
                            ? "Login token expired — host needs re-login"
                            : "When this host's login token expires"
                        }
                      >
                        {formatEpoch(h.login_token_expires_at)}
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 align-middle text-muted-foreground">
                    {formatEpoch(h.last_seen)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <VersionDialog host={selected} server={server} onClose={() => setSelected(null)} />
    </div>
  );
}

/** Version details + upgrade instructions for one host. */
function VersionDialog({
  host,
  server,
  onClose,
}: {
  host: AdminHost | null;
  server: AdminServerInfo | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [host]);
  if (host === null) return null;

  const cmd = server?.install_command ?? null;
  const copy = () => {
    if (!cmd) return;
    void navigator.clipboard?.writeText(cmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Dialog open={host !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{host.name}</DialogTitle>
          <DialogDescription>Installed version and how to upgrade this host.</DialogDescription>
        </DialogHeader>

        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-sm">
          <dt className="text-muted-foreground">Installed</dt>
          <dd className="tabular-nums">{host.version ?? "unknown"}</dd>
          <dt className="text-muted-foreground">Server target</dt>
          <dd className="tabular-nums">{server?.version_label ?? "—"}</dd>
          <dt className="text-muted-foreground">Status</dt>
          <dd>
            {host.outdated === true ? (
              <Badge className="bg-amber-500/15 text-amber-600 hover:bg-amber-500/15">
                Update available
              </Badge>
            ) : host.outdated === false ? (
              <Badge variant="secondary">Up to date</Badge>
            ) : (
              <span className="text-muted-foreground">Unknown</span>
            )}
          </dd>
          {host.os && (
            <>
              <dt className="text-muted-foreground">OS</dt>
              <dd>{host.os}</dd>
            </>
          )}
        </dl>

        {host.outdated !== false && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Run this on <span className="font-medium text-foreground">{host.name}</span> to
              install the version this server runs:
            </p>
            {cmd ? (
              <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-2">
                <code className="flex-1 overflow-x-auto whitespace-nowrap text-xs">{cmd}</code>
                <Button variant="ghost" size="icon" onClick={copy} aria-label="Copy command">
                  {copied ? <CheckIcon className="size-4" /> : <CopyIcon className="size-4" />}
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                The server has no public URL configured (OMNIGENT_DOMAIN), so it can't provide an
                installer command.
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
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
