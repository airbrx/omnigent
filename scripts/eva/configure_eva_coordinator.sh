#!/bin/bash
# Give omnigent.airbrx.ai its Eva bindings, then restart the coordinator.
#
# The Eva counterpart of ~/crew/configure-iris-coordinator.sh, and deliberately
# the same shape so an operator who has run that one already knows this one.
#
# Why this is a separate step, unchanged from the Iris reason: the coordinator
# reads OMNIGENT_EVA_CONFIG from /etc/omnigent/server.env, which holds
# DATABASE_URL and the OIDC/cookie secrets and is therefore not in the repo and
# not written by CD. Reaching it needs AWS credentials, so this is the one part
# of the rollout an agent cannot do.
#
#   aws sso login --profile airbrx-prod      # interactive, once, YOURS
#   bash scripts/eva/configure_eva_coordinator.sh
#
# Idempotent: re-running replaces the binding file and leaves one env line.
# To undo: delete the OMNIGENT_EVA_CONFIG line and restart omnigent-server.
# Eva then disappears from the drawer and nothing else changes; the registration
# in cli.py is gated on bindings() being non-empty.
#
# It does NOT touch /etc/omnigent/iris-host.json or the OMNIGENT_IRIS_CONFIG
# line. Separate file, separate variable, on purpose: a mistake in one must not
# be able to take the other down.
set -euo pipefail

PROFILE="${AWS_PROFILE:-airbrx-prod}"
INSTANCE_ID=i-02eb2f52439574844
REGION=us-east-1

# The bindings. NOT secret: who may open Eva, where the outreach app is, and a
# *reference* to the MCP bearer token. The token itself is resolved at the point
# of use and never appears here.
#
# `users` is the authorization decision in this file. These accounts, and no
# others, may open Eva. Change it deliberately.
#
# `base_url` is the outreach app as the EXECUTION HOST named by `host_id` sees
# it. Eva's turns run on that host and her MCP client connects from there, so
# 127.0.0.1:8000 means the app on that Mac, which is the whole point: no DNS,
# no certificate, no container. The coordinator never dials this URL.
#
# PULL MAIN BEFORE RUNNING THIS. The binding shape changed on 2026-09-22 and a
# stale copy of this script wrote a binding the deployed code refused, which
# crash-looped the coordinator until the rollback below was applied.
#
# `host_id` is the execution host that runs the outreach app and holds the
# token. Check /v1/hosts before you bind. A wrong host puts Eva in the drawer
# with every call failing, which looks like an auth problem and is not one.
# On 2026-09-24 that is the Air's bridge host, not the mini
# (882128953d2a4e178ddbd48d70b298a1): nothing serves the app on the mini and
# its keychain holds no eva-outreach-token.
read -r -d '' BINDINGS <<'JSON' || true
[
  {
    "label": "live",
    "users": ["aerickson@airbrx.com"],
    "base_url": "http://127.0.0.1:8000",
    "host_id": "f501802f3f0c4d22a8b1c64763cef4e1",
    "token_ref": "keychain:eva-outreach-token",
    "fixture": false
  }
]
JSON

REMOTE=$(cat <<EOS
set -eu
install -d -m 755 -o ubuntu -g ubuntu /etc/omnigent
cat > /etc/omnigent/eva.json <<'BIND'
${BINDINGS}
BIND
chown ubuntu:ubuntu /etc/omnigent/eva.json
chmod 640 /etc/omnigent/eva.json
# These two NON-SECRET placeholders must exist on the coordinator. It expands
# the bundle's ${VAR} references in more than one place, and a missing variable
# is fatal at each: at startup during registration, and again on every
# POST /v1/sessions for this agent.
#
# PR #68 fixed the startup half and its body said the placeholders could then be
# removed. They were, and the coordinator came up clean, and then every new Eva
# chat returned 400 "Unresolved environment variable '${OUTREACH_MCP_TOKEN}' in
# config key 'Authorization'". They went back. Do not remove them again until
# the session-create path also validates without expanding, and until a test
# creates a session for an agent whose bundle names an unset variable.
#
# They are safe because the stored artifact is the raw bundle and runners expand
# against their own environment, so these values never reach one. A runner that
# somehow did receive them would fail its calls with 401 rather than send
# something wrong, which is the right direction to fail in.
sed -i '/^OUTREACH_MCP_URL=/d;/^OUTREACH_MCP_TOKEN=/d' /etc/omnigent/server.env
echo 'OUTREACH_MCP_URL=http://127.0.0.1:8000/mcp/' >> /etc/omnigent/server.env
echo 'OUTREACH_MCP_TOKEN=placeholder-for-bundle-validation-only-runners-expand-their-own' >> /etc/omnigent/server.env
# One line, replaced rather than appended, so re-running cannot stack duplicates.
sed -i '/^OMNIGENT_EVA_CONFIG=/d' /etc/omnigent/server.env
echo 'OMNIGENT_EVA_CONFIG=/etc/omnigent/eva.json' >> /etc/omnigent/server.env
systemctl restart omnigent-server
# Up to 40 seconds, not 3. The server takes longer than that to answer, and a
# fixed sleep reported Failed on a start that had in fact succeeded, which sent
# an operator looking for a fault that was not there.
for _ in $(seq 1 20); do
  if systemctl is-active --quiet omnigent-server \
     && curl -fsS --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo active
    exit 0
  fi
  sleep 2
done
echo "omnigent-server did not come up within 40s; journalctl -u omnigent-server -n 50" >&2
systemctl is-active omnigent-server
EOS
)

CID=$(aws ssm send-command --profile "$PROFILE" --region "$REGION" \
  --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --comment "configure Eva bindings" \
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
echo "Now check Eva is registered and her app is actually carrying CRM data:"
echo '  TOKEN=$(python -c "from omnigent.cli_auth import load_token; print(load_token(\"https://omnigent.airbrx.ai\"))")'
echo '  curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva'
echo '  curl -sS -H "Authorization: Bearer $TOKEN" https://omnigent.airbrx.ai/v1/eva/readiness'
echo
echo "For this loopback binding /v1/eva/readiness answers null, because the"
echo "coordinator cannot dial the execution host's 127.0.0.1. Check on the host:"
echo '  curl -sS http://127.0.0.1:8000/readyz | python3 -m json.tool'
echo "leads.total must be > 0 and leads.fixture must be 0. Otherwise Eva is live"
echo "in front of no data or invented data, and should be unbound again."
