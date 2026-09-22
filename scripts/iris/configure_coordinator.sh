#!/bin/bash
# Give omnigent.airbrx.ai its Iris tenant bindings, then restart the coordinator.
#
# Why this is a separate step: the coordinator reads OMNIGENT_IRIS_CONFIG from
# /etc/omnigent/server.env, which holds DATABASE_URL and the OIDC/cookie secrets
# and is therefore not in the repo and not written by CD. Reaching it needs AWS
# credentials, so this is the one part of the rollout an agent cannot do.
#
#   aws sso login --profile airbrx-prod      # interactive, once
#   bash scripts/iris/configure_coordinator.sh
#
# Idempotent: re-running replaces the binding file and leaves one env line.
# To undo: delete the OMNIGENT_IRIS_CONFIG line and restart omnigent-server.
set -euo pipefail

PROFILE="${AWS_PROFILE:-airbrx-prod}"
INSTANCE_ID="${OMNIGENT_COORDINATOR_INSTANCE:-i-02eb2f52439574844}"
REGION="${AWS_REGION:-us-east-1}"

# The bindings. NOT secret — a tenant id, the execution host's id, an operator
# workspace path, the users allowed to open it, and a *reference* to the PAT.
# The PAT itself stays on the execution host and is resolved there, per
# invocation. Nothing below is a credential.
#
# `users` is the authorization decision in this file: these accounts, and no
# others, may open that tenant's Iris workspace. Change it deliberately.
#
# `name` is what the picker shows instead of the UUID. ORDER MATTERS: a
# coordinator running code older than airbrx/omnigent#46 refuses unknown binding
# keys with ValueError, so writing this file before that code ships makes
# bindings() throw and Iris disappear from the drawer entirely. Deploy first.
#
# host_id 448499c76820453d84eadc5b7107ed8b is the **MacBook Air**.
#
# It was the Mac mini (882128953d2a4e178ddbd48d70b298a1) until 2026-09-21, on
# the reasoning that the mini is always on while the Air only answers when the
# laptop is awake. That reasoning still holds and the cost is real: hosted Iris
# is now intermittent by construction. It was overridden deliberately — the
# mini is out of scope for this build, and a binding to a machine nobody is
# maintaining is not availability, it is a 400 nobody is watching for. That is
# exactly what happened: the mini slept, and every hosted Iris session failed
# at creation with "host 'Abrams-Mac-mini.local' is offline" while the
# coordinator looked healthy.
#
# The Air is prepared identically and needs no further setup: its plist already
# carries OMNIGENT_INSTALL_EXTRAS=iris and OMNIGENT_IRIS_CONFIG, and
# ~/.omnigent/iris-host.json already declares both bindings against the Air's
# own host id with the same workspace paths used here. Only this file's
# host_id had to change.
#
# Bind ONE host per tenant and mode. The verifier requires exactly one match,
# so two bindings for the same tenant would make the selection ambiguous
# rather than redundant.
#
# UNVERIFIED until a live turn runs here: whether the Air's launchd agent can
# resolve keychain:iris-selected-tenant. The mini needed OMNIGENT_DISABLE_KEYRING=1
# because its host had no login-keychain access; load_secret() returns
# keyring's answer as-is and only falls back to the 0600 file backend when the
# keyring call *raises*, so a reachable-but-empty keychain fails closed. The
# fixture tenant is unaffected — it resolves no PAT at all. Run
# `scripts/iris/verify_host.py --live` after this script to settle it.
read -r -d '' BINDINGS <<'JSON' || true
[
  {
    "tenant_id": "f65d9135-0ba3-4c58-8768-c48a1334041d",
    "name": "Airbrx Databricks Production",
    "host_id": "448499c76820453d84eadc5b7107ed8b",
    "workspace": "/Users/abramerickson/.omnigent/iris-workspaces/live",
    "users": ["aerickson@airbrx.com"],
    "pat_ref": "keychain:iris-selected-tenant",
    "fixture": false
  },
  {
    "tenant_id": "fixture-iris",
    "name": "Synthetic fixture — no live data",
    "host_id": "448499c76820453d84eadc5b7107ed8b",
    "workspace": "/Users/abramerickson/.omnigent/iris-workspaces/fixture",
    "users": ["aerickson@airbrx.com"],
    "pat_ref": "",
    "fixture": true
  }
]
JSON

REMOTE=$(cat <<EOS
set -eu
install -d -m 755 -o ubuntu -g ubuntu /etc/omnigent
cat > /etc/omnigent/iris-host.json <<'BIND'
${BINDINGS}
BIND
chown ubuntu:ubuntu /etc/omnigent/iris-host.json
chmod 640 /etc/omnigent/iris-host.json
# One line, replaced rather than appended, so re-running cannot stack duplicates.
sed -i '/^OMNIGENT_IRIS_CONFIG=/d' /etc/omnigent/server.env
echo 'OMNIGENT_IRIS_CONFIG=/etc/omnigent/iris-host.json' >> /etc/omnigent/server.env
systemctl restart omnigent-server
sleep 3
systemctl is-active omnigent-server
EOS
)

CID=$(aws ssm send-command --profile "$PROFILE" --region "$REGION" \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --comment "configure Iris tenant bindings" \
  --parameters "$(jq -n --arg s "$REMOTE" '{commands: ($s | split("\n"))}')" \
  --query Command.CommandId --output text)
echo "SSM command: $CID"

until [ "$(aws ssm get-command-invocation --profile "$PROFILE" --region "$REGION" \
          --command-id "$CID" --instance-id "$INSTANCE_ID" \
          --query Status --output text 2>/dev/null || echo Pending)" \
       != "Pending" ] && \
      [ "$(aws ssm get-command-invocation --profile "$PROFILE" --region "$REGION" \
          --command-id "$CID" --instance-id "$INSTANCE_ID" \
          --query Status --output text)" != "InProgress" ]; do sleep 3; done

aws ssm get-command-invocation --profile "$PROFILE" --region "$REGION" \
  --command-id "$CID" --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}' --output json

echo
echo "Now check the catalog is populated (should list two bindings, not []):"
echo '  TOKEN=$(python -c "from omnigent.cli_auth import load_token; print(load_token(\"https://omnigent.airbrx.ai\"))")'
echo '  curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/iris'
