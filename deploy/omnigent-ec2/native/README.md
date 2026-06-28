# Native (non-Docker) deploy — omnigent.airbrx.ai

Runs the Omnigent server straight from the `/opt/omnigent` git checkout via a
`uv`-managed venv, with the React SPA shipped as a prebuilt **GitHub Release**
asset rather than built on the box. This trades the Docker image-rebuild loop
(~14 min on a `t3.small`, needs swap) for:

- **backend change** → `git pull && sudo systemctl restart omnigent-server` (seconds)
- **frontend change** → `release-webui.sh` on a dev machine, then `pull-webui.sh`
  on the box (one Vite build, on your laptop, fanned out to every box)

The box stays lean: no node, no node_modules, no build toolchain — only `uv`
+ the Python venv. The same pattern is meant to be copied to the interns box.

## Layout

| File | Where it runs | Purpose |
|------|---------------|---------|
| `omnigent-server.service` | box (`/etc/systemd/system/`) | systemd unit; serves `127.0.0.1:8001`, `EnvironmentFile=/etc/omnigent/server.env` |
| `release-webui.sh` | dev machine / CI | build SPA → publish `webui-<sha>` GitHub Release |
| `pull-webui.sh` | box | download a `webui-<sha>` asset → atomic-swap into the static dir |

Config + secrets live in `/etc/omnigent/server.env` (DATABASE_URL in the
`postgresql+psycopg://` form the native engine needs — the Docker entrypoint
normalized a bare URL, the native engine does not — plus the OIDC client/cookie
secrets, `OMNIGENT_DOMAIN`, `OMNIGENT_DATA_DIR`, `OMNIGENT_ADMIN_LIST_PATH`).

## First-time setup (box)

```bash
# 1. Own the checkout so git/uv need no sudo, install uv
sudo chown -R ubuntu:ubuntu /opt/omnigent
sudo -u ubuntu bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# 2. Backend venv (editable install + sibling SDK path-deps; psycopg is not
#    a baseline dep — pulled in explicitly, as the Docker image does).
#    OMNIGENT_SKIP_WEB_UI=true is REQUIRED: the package's build hook otherwise
#    tries to `npm run build` the SPA and hard-fails (no node on the box).
#    --no-dev keeps the venv lean; the SPA is shipped via release (step 3).
cd /opt/omnigent
sudo -u ubuntu bash -lc 'export PATH=$HOME/.local/bin:$PATH; OMNIGENT_SKIP_WEB_UI=true uv sync --no-dev'
sudo -u ubuntu bash -lc 'export PATH=$HOME/.local/bin:$PATH; uv pip install --python /opt/omnigent/.venv/bin/python "psycopg[binary]>=3.1,<4"'

# 3. SPA bundle (publish from a dev machine first: ./release-webui.sh)
sudo -u ubuntu deploy/omnigent-ec2/native/pull-webui.sh webui-<sha>

# 4. Service. If a prior native install left a drop-in, remove it first — a
#    leftover override.conf silently wins over this unit's ExecStart.
sudo rm -rf /etc/systemd/system/omnigent-server.service.d
sudo cp deploy/omnigent-ec2/native/omnigent-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omnigent-server
```

## Zero-downtime cutover from Docker

Native binds `:8001` while the Docker container still serves `:8000`, so the
switch is a graceful Caddy reload with no dropped requests:

```bash
sudo systemctl enable --now omnigent-server                 # native up on :8001
curl -fsS localhost:8001/health                             # verify directly
sudo sed -i 's/127.0.0.1:8000/127.0.0.1:8001/' /etc/caddy/Caddyfile
sudo systemctl reload caddy                                 # graceful upstream swap
cd /opt/omnigent/deploy/omnigent-ec2 && sudo docker compose down   # drain Docker
```

Rollback: `docker compose up -d`, point the Caddyfile back to `:8000`,
`systemctl reload caddy`, `systemctl stop omnigent-server`.

## Dev loop

For a force-pushed branch use `git fetch && git reset --hard origin/<branch>`
instead of `git pull`.

```bash
# backend only
ssm> cd /opt/omnigent && git pull && sudo systemctl restart omnigent-server

# frontend (or backend + frontend)
dev> deploy/omnigent-ec2/native/release-webui.sh          # -> webui-<sha>
ssm> cd /opt/omnigent && git pull \
       && deploy/omnigent-ec2/native/pull-webui.sh webui-<sha> \
       && sudo systemctl restart omnigent-server
```

## Gotchas (operations)

- **Never run `uv sync` casually on the box.** `psycopg` is installed
  *outside* the lockfile (step 2), so `uv sync` **prunes it** — the next
  restart then crash-loops on the DB connect (500/502). `uv sync` is only
  for a genuine dependency change, and you must reinstall psycopg right
  after: `uv pip install --python /opt/omnigent/.venv/bin/python
  'psycopg[binary]>=3.1,<4'`. The normal dev loop (`git reset` + restart,
  or `pull-webui` + restart) never touches `uv`, so it's safe.

- **`omnigent --version` lags the deployed code.** The `(sha, built …)`
  string comes from `omnigent/_build_info.py`, which `setup.py` writes
  only at *install/build* time — a `git reset && restart` redeploy does
  **not** refresh it. The authoritative deployed commit is
  `git -C /opt/omnigent rev-parse HEAD`. To make `--version` honest after
  a code redeploy, regenerate the stamp (no reinstall needed):

  ```bash
  sudo -u ubuntu /opt/omnigent/.venv/bin/python - <<'PY'
  import time, subprocess, pathlib
  sha = subprocess.run(["git","-C","/opt/omnigent","rev-parse","HEAD"],
                       capture_output=True, text=True).stdout.strip()
  pathlib.Path("/opt/omnigent/omnigent/_build_info.py").write_text(
      "from __future__ import annotations\n\n"
      f"BUILD_TIME_EPOCH: int = {int(time.time())}\n"
      f"COMMIT_SHA: str = {sha!r}\n")
  PY
  sudo systemctl restart omnigent-server
  ```

## Host auto-upgrade (opt-in)

A host can keep itself in sync with this server's build. Add `--auto-upgrade`
to the host's launch and it will, on each (re)connect, install the server's
build (via `GET /install.sh`) and restart — but only when idle (no live
runner) and only when the builds actually differ. Opt-in: the flag consents
to the connected server installing code on that machine.

```ini
# in the host's systemd user unit (omnigent-host.service):
ExecStart=/bin/bash -lc 'exec ~/.local/bin/omnigent host https://omnigent.airbrx.ai --auto-upgrade'
```

<!-- build bump to validate host auto-upgrade on reconnect -->

<!-- bump 2 -->
