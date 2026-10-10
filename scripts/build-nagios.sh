#!/bin/sh
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
nagios_build_root="$repo_root/.tools/nagios"
nagios_source_root="$nagios_build_root/source"
expected_commit=cfa5684eb50facc3183d5ddb01d853b9eb9cc218
mkdir -p "$nagios_build_root/bin"
if [ ! -d "$nagios_source_root/.git" ]; then
    git clone --depth 1 --branch nagios-4.5.14 https://github.com/NagiosEnterprises/nagioscore.git "$nagios_source_root"
fi
actual_commit=$(git -C "$nagios_source_root" rev-parse HEAD)
if [ "$actual_commit" != "$expected_commit" ]; then
    echo 'Unexpected Nagios source revision; refusing to build.' >&2
    exit 1
fi
if ! git -C "$nagios_source_root" diff --quiet || ! git -C "$nagios_source_root" diff --cached --quiet; then
    echo 'Nagios source has local edits; refusing to overwrite or build altered source.' >&2
    exit 1
fi
if [ -x "$nagios_build_root/bin/nagios" ]; then
    "$nagios_build_root/bin/nagios" --version
    exit 0
fi
cd "$nagios_source_root"
./configure --prefix="$nagios_build_root" --with-nagios-user="$(id -un)" --with-nagios-group="$(id -gn)" --without-httpd-conf
# The pinned source includes this gperf output. Restore its exact trusted content
# with a fresh timestamp instead of regenerating it due to checkout ordering.
git show "$expected_commit:base/wpres-phash.h" > base/wpres-phash.h.new
mv base/wpres-phash.h.new base/wpres-phash.h
make -j2 nagios
cp base/nagios "$nagios_build_root/bin/nagios"
echo "Nagios Core ready: $nagios_build_root/bin/nagios"
# Keep source and its LICENSE here for reproducibility and GPL compliance.
