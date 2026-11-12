#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
rootfs="$A100SIM_ROOT/rootfs"
workdir="$PWD"
[[ -x "$rootfs/bin/bash" ]] || { echo "Run prepare.sh first: missing Ubuntu rootfs" >&2; exit 2; }
python_prefix="$("$A100SIM_PYTHON" -c 'import sys; print(sys.base_prefix)')"
case "$python_prefix" in
    /|/usr|/usr/local) echo "Activate the Isaac Sim Conda environment or set A100SIM_PYTHON to an independent Python 3.12" >&2; exit 2 ;;
esac
mount --make-rprivate /
mkdir -p "$rootfs/dev" "$rootfs/proc" "$rootfs/sys" "$rootfs/opt/host-nvidia" "$rootfs/usr/share/nvidia"
# Bind paths at their original absolute names so USD references and job paths agree.
# Non-recursive binds avoid importing this namespace's child mounts again.
for directory in "$A100SIM_HOST_HOME" "$A100SIM_ROOT" "$A100SIM_CODE_ROOT" "$A100SIM_NVIDIA_ROOT" "$python_prefix" \
                 "$A100SIM_LOCAL_STATE" "$A100SIM_ASSET_ROOT" "$A100SIM_STATE_ROOT" \
                 "${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"; do
    [[ "$directory" = /* && "$directory" != / ]] || { echo "Expected absolute directory: $directory" >&2; exit 2; }
    mkdir -p "$directory" "$rootfs$directory"
    mount --bind "$directory" "$rootfs$directory"
done
chmod 700 "$A100SIM_LOCAL_STATE"
for directory in /dev /proc /sys; do
    mount --rbind "$directory" "$rootfs$directory"
done
[[ -n "$A100SIM_CUDA_ROOT" && -d "$A100SIM_CUDA_ROOT" ]] || {
    echo "CUDA Toolkit not detected; set A100SIM_CUDA_ROOT to its absolute directory" >&2; exit 2;
}
mkdir -p "$rootfs$A100SIM_CUDA_ROOT"
mount --bind "$A100SIM_CUDA_ROOT" "$rootfs$A100SIM_CUDA_ROOT"
while IFS= read -r library; do
    name="$(basename "$library")"
    touch "$rootfs/opt/host-nvidia/$name"
    mount --bind "$library" "$rootfs/opt/host-nvidia/$name"
done < <(/sbin/ldconfig -p | awk '/libcuda\.so|libnvidia-ptxjitcompiler|libnvidia-nvvm|libnvidia-fatbinaryloader|libnvidia-ml\.so/ && /x86-64/ {print $NF}' | sort -u)
if [[ -n "${A100SIM_CXX_LIB_DIR:-}" ]]; then
    for name in libstdc++.so.6 libgcc_s.so.1; do
        touch "$rootfs/opt/host-nvidia/$name"
        mount --bind "$A100SIM_CXX_LIB_DIR/$name" "$rootfs/opt/host-nvidia/$name"
    done
fi
if [[ -d "$A100SIM_NVIDIA_ROOT/usr/share/nvidia" ]]; then
    mount --bind "$A100SIM_NVIDIA_ROOT/usr/share/nvidia" "$rootfs/usr/share/nvidia"
fi
cp -L /etc/resolv.conf "$rootfs/etc/resolv.conf"
libraries="/opt/host-nvidia:$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu:$A100SIM_CUDA_ROOT/targets/x86_64-linux/lib:/usr/lib/x86_64-linux-gnu"
libraries+="${A100SIM_EXTRA_LD_LIBRARY_PATH:+:$A100SIM_EXTRA_LD_LIBRARY_PATH}"
exec chroot "$rootfs" /usr/bin/env LD_LIBRARY_PATH="$libraries" \
    HOME="$A100SIM_STATE_ROOT/home" /bin/bash -c 'cd "$1"; shift; exec "$@"' bash "$workdir" "$@"
