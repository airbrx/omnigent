#!/usr/bin/env bash
# Build the Omnigent SPA HERE and publish it as a GitHub Release asset.
#
# The native EC2 boxes (omnigent.airbrx.ai, and later the interns box) pull
# this prebuilt tarball with pull-webui.sh instead of running `npm run build`
# on a 2 GB box. Build once on a machine with node, fan the same bytes out to
# every box. The release tag (webui-<sha>) binds the artifact to the exact
# source commit, so a box can be pinned/rolled back by tag.
#
# Requires: node + npm, and an authenticated `gh` (contents:write on the repo).
# Run from anywhere inside the repo.
set -euo pipefail

REPO="${OMNIGENT_RELEASE_REPO:-airbrx/omnigent}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SHA="$(git rev-parse --short HEAD)"
TAG="webui-${SHA}"
ASSET="web-ui-${SHA}.tar.gz"
BUNDLE_DIR="omnigent/server/static/web-ui"

echo ">> Building SPA (ap-web) for ${SHA} ..."
( cd ap-web && npm ci && npm run build )
test -f "${BUNDLE_DIR}/index.html" \
  || { echo "ERROR: ${BUNDLE_DIR}/index.html missing after build" >&2; exit 1; }

echo ">> Packing /tmp/${ASSET} ..."
tar -C omnigent/server/static -czf "/tmp/${ASSET}" web-ui

echo ">> Publishing release ${TAG} -> ${REPO} ..."
if gh release view "${TAG}" --repo "${REPO}" >/dev/null 2>&1; then
  gh release upload "${TAG}" "/tmp/${ASSET}" --repo "${REPO}" --clobber
else
  gh release create "${TAG}" "/tmp/${ASSET}" \
    --repo "${REPO}" \
    --title "web-ui ${SHA}" \
    --notes "Prebuilt Omnigent SPA bundle for native EC2 deploys, built from commit ${SHA}."
fi

echo ">> Done."
echo "   Tag:   ${TAG}"
echo "   Box:   sudo -u ubuntu deploy/omnigent-ec2/native/pull-webui.sh ${TAG}"
