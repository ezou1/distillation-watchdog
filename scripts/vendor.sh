#!/usr/bin/env bash
# Download the pinned front-end libraries into static/vendor/.
# Run this script one time at build time. The pages do not use a CDN at run time.
set -euo pipefail
cd "$(dirname "$0")/.."
CHART_VERSION="4.4.6"
CYTOSCAPE_VERSION="3.30.4"
mkdir -p static/vendor
curl -fsSL "https://cdn.jsdelivr.net/npm/chart.js@${CHART_VERSION}/dist/chart.umd.min.js" -o static/vendor/chart.umd.min.js
curl -fsSL "https://cdn.jsdelivr.net/npm/cytoscape@${CYTOSCAPE_VERSION}/dist/cytoscape.min.js" -o static/vendor/cytoscape.min.js
echo "Vendor files are in static/vendor/."
