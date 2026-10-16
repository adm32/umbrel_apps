#!/usr/bin/env bash
# One-time setup: puts your GitHub username into the app files.
#   ./setup.sh your-github-username
set -euo pipefail
user="${1:-}"
if [[ -z "$user" ]]; then
  echo "Usage: ./setup.sh your-github-username" >&2
  exit 1
fi
user="$(echo "$user" | tr '[:upper:]' '[:lower:]')"  # container registries need lowercase
cd "$(dirname "$0")"
for f in adam-vitals/umbrel-app.yml adam-vitals/docker-compose.yml docker-compose.test.yml; do
  sed -i.bak "s/GITHUB_USER/${user}/g" "$f" && rm -f "$f.bak"
done
echo "Done. Image: ghcr.io/${user}/umbrel-vitals — now commit and push to GitHub."
