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
# `base_url` must be the outreach app as the COORDINATOR can reach it. A
# localhost URL here binds Eva to a machine that is not the one serving her.
read -r -d '' BINDINGS <<'JSON' || true
[
  {
    "label": "live",
    "users": ["aerickson@airbrx.com"],
    "base_url": "http://127.0.0.1:8000",
    "host_id": "882128953d2a4e178ddbd48d70b298a1",
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
# One line, replaced rather than appended, so re-running cannot stack duplicates.
sed -i '/^OMNIGENT_EVA_CONFIG=/d' /etc/omnigent/server.env
echo 'OMNIGENT_EVA_CONFIG=/etc/omnigent/eva.json' >> /etc/omnigent/server.env
systemctl restart omnigent-server
sleep 3
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
echo "carries_crm_data must be true. If it is null or false, Eva is live in front"
echo "of seed data and should be unbound again until the sync works."
