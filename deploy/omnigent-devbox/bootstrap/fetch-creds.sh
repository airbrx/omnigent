#!/bin/bash
# Materialize dev-box credentials from Secrets Manager into an EnvironmentFile
# the omnigent host user-unit reads. Runs at boot and on demand.
#
# Fails loudly (per repo convention): a secret that EXISTS but cannot be read
# aborts. A secret that is simply not configured yet is reported and skipped,
# because gh/aws creds are optional while the Claude token is not.
set -euo pipefail

DEV_USER=michael
REGION=us-east-1
ENV_DIR="/home/${DEV_USER}/.config/omnigent"
ENV_FILE="${ENV_DIR}/host.env"
TMP_FILE="${ENV_FILE}.tmp"

install -d -o "${DEV_USER}" -g "${DEV_USER}" -m 700 "${ENV_DIR}"
umask 077
: > "${TMP_FILE}"

# fetch <secret-id> -> prints the secret string, or returns 1 if absent.
# Any failure OTHER than "not found" aborts the script.
fetch() {
  local secret_id="$1" out rc
  set +e
  out=$(aws secretsmanager get-secret-value --region "${REGION}" \
        --secret-id "${secret_id}" --query SecretString --output text 2>&1)
  rc=$?
  set -e
  if [ $rc -eq 0 ]; then
    printf '%s' "${out}"
    return 0
  fi
  if printf '%s' "${out}" | grep -q 'ResourceNotFoundException'; then
    return 1
  fi
  echo "FATAL: could not read secret '${secret_id}': ${out}" >&2
  exit 1
}

emit() { # emit <ENV_NAME> <secret-id> <required:yes|no>
  local env_name="$1" secret_id="$2" required="$3" value
  if value=$(fetch "${secret_id}"); then
    printf '%s=%s\n' "${env_name}" "${value}" >> "${TMP_FILE}"
    echo "  ok: ${env_name} <- ${secret_id}"
  elif [ "${required}" = "yes" ]; then
    echo "FATAL: required secret '${secret_id}' does not exist." >&2
    echo "       Create it with:" >&2
    echo "       aws secretsmanager create-secret --name ${secret_id} --secret-string '<value>'" >&2
    exit 1
  else
    echo "  skip: ${secret_id} not configured (optional)"
  fi
}

emit CLAUDE_CODE_OAUTH_TOKEN omnigent/devbox/claude-oauth-token yes
emit GIT_TOKEN              omnigent/devbox/github-token        no

mv "${TMP_FILE}" "${ENV_FILE}"
chown "${DEV_USER}:${DEV_USER}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
echo "wrote ${ENV_FILE}"
