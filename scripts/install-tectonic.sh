#!/usr/bin/env bash
# Tectonic, for the hosted service's PDFs (docs/assembly.md). Render's build
# runs this (render.yaml): a pinned release, checked against its SHA-256, into
# .tectonic/bin/ in the checkout. Then one sample resume is compiled, so the
# TeX packages every resume uses are in .tectonic/cache/ before anyone builds
# one - the first download takes minutes.
#
# Linux on x86-64 only. Anywhere else, install Tectonic yourself (docs/assembly.md).
set -euo pipefail

VERSION=0.17.0
SHA256=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
  echo "install-tectonic: this installs the Linux x86-64 build - see docs/assembly.md" >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
file="tectonic-${VERSION}-x86_64-unknown-linux-musl.tar.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -sSfL --retry 3 -o "$tmp/$file" \
  "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${VERSION}/$file"
echo "$SHA256  $tmp/$file" | sha256sum -c -
mkdir -p "$root/.tectonic/bin"
tar -xzf "$tmp/$file" -C "$root/.tectonic/bin" tectonic
"$root/.tectonic/bin/tectonic" --version

# A failure here doesn't stop the deploy: PDFs still work, and the first one
# fetches the packages itself.
python -m jobsearch.cli tex-check \
  || echo "install-tectonic: the sample resume didn't compile; the first PDF will fetch the packages" >&2
