#!/bin/sh
# Host-only (Linux/WSL gcc) fault-injection regression for firmware/cpu_adapter.c.
# No device, USB/ADB or firmware access. Build artifacts go only to the scratch
# directory: $1, else ${TMPDIR:-/tmp}/xreal-cpu-adapter-faults.
#
#   sh firmware/tests/run_cpu_adapter_faults.sh [scratch-dir]
#
# Optional: CFLAGS (default "-O2 -g -Wall"), e.g. CFLAGS="-O1 -g -fsanitize=address,undefined".
# FI_VERBOSE=1 also echoes adapter traces to stderr.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fw=$(dirname -- "$here")
out=${1:-${TMPDIR:-/tmp}/xreal-cpu-adapter-faults}
cc=${CC:-gcc}
cflags=${CFLAGS:--O2 -g -Wall}
mkdir -p "$out"

# Only the adapter object gets the renamed imports; cpu_adapter.c is unchanged.
# shellcheck disable=SC2086
"$cc" $cflags -c -I"$fw" \
    -Dmalloc=fi_malloc -Dfree=fi_free -Dclock_gettime=fi_clock_gettime \
    -Dopen=fi_open -Dwrite=fi_write -Dclose=fi_close \
    -Dxreal_build_rgb_table_range=fi_build_rgb_table_range \
    "$fw/cpu_adapter.c" -o "$out/cpu_adapter_fi.o"
# shellcheck disable=SC2086
"$cc" $cflags -pthread -I"$fw" "$out/cpu_adapter_fi.o" "$fw/lut_fast.c" "$fw/runtime.c" \
    "$here/hal_stub.c" "$here/cpu_adapter_faults.c" -o "$out/cpu_adapter_faults"
exec "$out/cpu_adapter_faults"
