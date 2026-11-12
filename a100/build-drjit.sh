#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/config.sh"
revision=3b96411288b9938889045308654ba37d4ef24b71
source_dir="$A100SIM_ROOT/tools/drjit-core"
build_dir="$A100SIM_ROOT/tools/drjit-core-build"
if [[ ! -d "$source_dir/.git" ]]; then
    git clone https://github.com/mitsuba-renderer/drjit-core.git "$source_dir"
fi
[[ "$(git -C "$source_dir" rev-parse HEAD)" = "$revision" ]] || git -C "$source_dir" checkout "$revision"
git -C "$source_dir" submodule update --init --recursive
patch="$A100SIM_CODE_ROOT/patches/drjit-core-a100.patch"
if git -C "$source_dir" apply --check "$patch"; then
    git -C "$source_dir" apply "$patch"
elif ! git -C "$source_dir" apply --reverse --check "$patch"; then
    echo "Dr.Jit source does not match the pinned A100 patch" >&2; exit 2
fi
cmake -S "$source_dir" -B "$build_dir" -DCMAKE_BUILD_TYPE=Release \
    -DDRJIT_CORE_ENABLE_TESTS=OFF -DDRJIT_CORE_NATIVE_FLAGS=-march=native \
    -DDRJIT_DYNAMIC_CUDA=ON -DDRJIT_DYNAMIC_LLVM=ON \
    -DDRJIT_ENABLE_CUDA=ON -DDRJIT_ENABLE_OPTIX=ON
cmake --build "$build_dir" --parallel "${A100SIM_BUILD_JOBS:-8}"
library="$(find "$build_dir" -name libdrjit-core.so -type f -print -quit)"
[[ -n "$library" ]] || { echo "Built libdrjit-core.so was not found" >&2; exit 2; }
target="$A100SIM_ROOT/render-packages/drjit/libdrjit-core.so"
[[ -f "$target" ]] || { echo "Run prepare.sh before build-drjit.sh" >&2; exit 2; }
[[ -f "$target.original" ]] || cp -p "$target" "$target.original"
cp "$library" "$target"
echo "Patched Dr.Jit CUDA/OptiX core installed for Mitsuba 3.9.1 / Dr.Jit 1.5.0"
