#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
source "$A100SIM_CODE_ROOT/resolve-gpu.sh"
actual="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader --id="$ISAAC_GPU" | tr -d '[:space:]')"
[[ "$actual" = "$A100SIM_DRIVER_VERSION" ]] || {
    echo "Detected driver differs from A100SIM_DRIVER_VERSION; clear the override or supply matching libraries" >&2; exit 2;
}
destination="$A100SIM_NVIDIA_ROOT/usr/lib/x86_64-linux-gnu"
mkdir -p "$A100SIM_ROOT/downloads" "$destination"

copy_libraries() {
    local source_dir="$1" library name
    for name in libnvoptix libnvidia-rtcore; do
        library="$(find "$source_dir" -maxdepth 4 -name "$name.so.$actual" -type f -print -quit)"
        [[ -n "$library" ]] || continue
        cp -L -- "$library" "$destination/$name.so.$actual"
        ln -sfn "$name.so.$actual" "$destination/$name.so.1"
    done
}

# Prefer libraries already installed by the matching host driver.
host_optix="$(/sbin/ldconfig -p | awk '/libnvoptix\.so\.1/ && /x86-64/ {library=$NF} END {print library}')"
if [[ -n "$host_optix" ]]; then
    copy_libraries "$(dirname -- "$(readlink -f -- "$host_optix")")"
fi
if [[ ! -f "$destination/libnvoptix.so.$actual" || ! -f "$destination/libnvidia-rtcore.so.$actual" ]]; then
    extracted="$A100SIM_ROOT/tools/driver-archive-$actual"
    if [[ -n "${A100SIM_NVIDIA_DEB_URL:-}" ]]; then
        archive="$A100SIM_ROOT/downloads/nvidia-userspace-$actual.deb"
        curl --fail --location --retry 3 --connect-timeout 20 "$A100SIM_NVIDIA_DEB_URL" -o "$archive"
        mkdir -p "$extracted"
        dpkg-deb -x "$archive" "$extracted"
    else
        archive="$A100SIM_ROOT/downloads/NVIDIA-Linux-x86_64-$actual.run"
        if [[ -n "${A100SIM_NVIDIA_RUN_URL:-}" ]]; then
            urls=("$A100SIM_NVIDIA_RUN_URL")
        else
            urls=(
                "https://us.download.nvidia.com/tesla/$actual/NVIDIA-Linux-x86_64-$actual.run"
                "https://us.download.nvidia.com/XFree86/Linux-x86_64/$actual/NVIDIA-Linux-x86_64-$actual.run"
            )
        fi
        downloaded=0
        for url in "${urls[@]}"; do
            if curl --fail --location --retry 3 --connect-timeout 20 "$url" -o "$archive.part"; then
                mv -- "$archive.part" "$archive"
                downloaded=1
                break
            fi
        done
        if [[ "$downloaded" != 1 ]]; then
            rm -f -- "$archive.part"
            echo "Matching NVIDIA driver archive could not be downloaded; configure an official archive mirror" >&2
            exit 2
        fi
        # Extraction only: never invokes the driver installer or loads kernel modules.
        [[ -d "$extracted" ]] || /bin/sh "$archive" --extract-only --target "$extracted"
    fi
    copy_libraries "$extracted"
fi
[[ -f "$destination/libnvoptix.so.$actual" && -f "$destination/libnvidia-rtcore.so.$actual" ]] || {
    echo "Matching OptiX libraries were not found; configure an official archive mirror matching the detected driver" >&2; exit 2;
}
echo "Matching OptiX libraries are ready"
