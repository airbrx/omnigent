#!/usr/bin/env bash
# Pull a prebuilt SPA bundle (published by release-webui.sh) onto a native box
# and atomically swap it into the server's static dir. Run ON the EC2 box.
#
# The repo is public, so the release asset downloads with a plain curl — no
# auth, no `gh` on the box. The swap keeps the previous bundle until the new
# one is validated, so a bad/partial download can't leave a blank UI.
#
#   deploy/omnigent-ec2/native/pull-webui.sh webui-<sha>
#   sudo systemctl restart omnigent-server   # to serve the new bundle
set -euo pipefail

TAG="${1:?usage: pull-webui.sh <tag, e.g. webui-abc1234>}"
REPO="${OMNIGENT_RELEASE_REPO:-airbrx/omnigent}"
DEST="${OMNIGENT_STATIC_DIR:-/opt/omnigent/omnigent/server/static}"
SHA="${TAG#webui-}"
ASSET="web-ui-${SHA}.tar.gz"
URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"

echo ">> Downloading ${URL} ..."
curl -fsSL -o "/tmp/${ASSET}" "${URL}"

echo ">> Unpacking + validating ..."
rm -rf "${DEST}/web-ui.new" "${DEST}/web-ui.old"
mkdir -p "${DEST}/web-ui.new"
tar -C "${DEST}/web-ui.new" -xzf "/tmp/${ASSET}"
# The tarball carries a top-level web-ui/ dir.
CONTENT="${DEST}/web-ui.new/web-ui"
[ -d "${CONTENT}" ] || CONTENT="${DEST}/web-ui.new"
test -f "${CONTENT}/index.html" \
  || { echo "ERROR: index.html missing in bundle ${ASSET}" >&2; exit 1; }

echo ">> Swapping into ${DEST}/web-ui ..."
[ -d "${DEST}/web-ui" ] && mv "${DEST}/web-ui" "${DEST}/web-ui.old"
mv "${CONTENT}" "${DEST}/web-ui"
rm -rf "${DEST}/web-ui.new" "${DEST}/web-ui.old" "/tmp/${ASSET}"
echo ">> In place: ${DEST}/web-ui/index.html"
echo "   Now: sudo systemctl restart omnigent-server"
