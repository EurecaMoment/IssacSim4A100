#!/usr/bin/env bash
set -euo pipefail

code_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
action="${1:-deploy}"
inside_conda=0
if [[ "$action" = --inside-conda ]]; then
    inside_conda=1
    shift
    action="${1:-deploy}"
fi
case "$action" in
    deploy|start|status|stop) ;;
    *) echo "Usage: deploy.sh [deploy|start|status|stop]" >&2; exit 2 ;;
esac

if [[ "$action" != status && "$action" != stop && ! -f "${A100SIM_CONFIG:-$code_root/.env}" && -z "${A100SIM_CONFIG:-}" ]]; then
    cp "$code_root/.env.example" "$code_root/.env"
fi
source "$code_root/config.sh"
if [[ "$action" = deploy && "${A100SIM_DEPLOY_FEATURES:-all}" != all && "${A100SIM_DEPLOY_FEATURES:-all}" != none ]]; then
    echo "A100SIM_DEPLOY_FEATURES supports all or none" >&2
    exit 2
fi

pid_dir="$A100SIM_STATE_ROOT/deploy"
log_dir="$A100SIM_ROOT/logs"
started_services=()
grpc_listen="${A100SIM_GRPC_LISTEN:-127.0.0.1:50051}"
gateway_python="${A100SIM_GRPC_ENV:-$A100SIM_ROOT/.venv-grpc}/bin/python"

process_start_time() {
    local pid="$1" stat rest
    [[ -r "/proc/$pid/stat" ]] || return 1
    IFS= read -r stat < "/proc/$pid/stat"
    rest="${stat##*) }"
    read -r -a fields <<< "$rest"
    [[ -n "${fields[19]:-}" && "${fields[0]}" != Z && "${fields[0]}" != X ]] || return 1
    printf '%s\n' "${fields[19]}"
}

recorded_pid() {
    local name="$1" pid expected current
    [[ -r "$pid_dir/deploy-$name.pid" ]] || return 1
    read -r pid expected < "$pid_dir/deploy-$name.pid" || return 1
    [[ "$pid" =~ ^[0-9]+$ && -n "$expected" ]] || return 1
    current="$(process_start_time "$pid")" || return 1
    [[ "$current" = "$expected" ]] || return 1
    printf '%s\n' "$pid"
}

stop_service() {
    local name="$1" pid attempt
    if ! pid="$(recorded_pid "$name")"; then
        rm -f -- "$pid_dir/deploy-$name.pid"
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null || true
    for attempt in {1..30}; do
        if ! process_start_time "$pid" >/dev/null 2>&1; then
            rm -f -- "$pid_dir/deploy-$name.pid"
            echo "Stopped $name"
            return 0
        fi
        sleep 1
    done
    echo "Service $name did not stop within 30 seconds; inspect $pid" >&2
    return 1
}

if [[ "$action" = status || "$action" = stop ]]; then
    mkdir -p "$pid_dir"
    chmod 700 "$pid_dir"
    if [[ "$action" = stop ]]; then
        for name in grpc render features resident generic; do stop_service "$name"; done
    else
        for name in generic resident render features grpc; do
            if pid="$(recorded_pid "$name")"; then
                echo "$name: running (pid $pid)"
            else
                echo "$name: stopped"
            fi
        done
    fi
    exit 0
fi

if [[ "$inside_conda" = 0 ]]; then
    conda_command="$(command -v conda || true)"
    [[ -n "$conda_command" ]] || { echo "Install Conda before running deploy.sh" >&2; exit 2; }
    conda_base="$(conda info --base)"
    conda_executable="${CONDA_EXE:-$conda_base/bin/conda}"
    if [[ ! -x "$conda_executable" ]]; then
        conda_executable="$(type -P conda || true)"
    fi
    [[ -x "$conda_executable" ]] || { echo "Conda's executable was not found" >&2; exit 2; }
    conda_python="$conda_base/bin/python"
    [[ -x "$conda_python" ]] || { echo "Conda's base Python was not found" >&2; exit 2; }
    environment_name="isaacsim-a100"
    find_environment_prefix() {
        "$conda_executable" env list --json | "$conda_python" -c \
            'import json, pathlib, sys; print(next((p for p in json.load(sys.stdin)["envs"] if pathlib.Path(p).name == sys.argv[1]), ""))' \
            "$environment_name"
    }
    environment_prefix="$(find_environment_prefix)"
    if [[ -n "$environment_prefix" ]]; then
        "$conda_executable" env update --prefix "$environment_prefix" --file "$code_root/environment.yml"
    else
        "$conda_executable" env create --file "$code_root/environment.yml"
        environment_prefix="$(find_environment_prefix)"
    fi
    [[ -x "$environment_prefix/bin/python" ]] || { echo "Conda environment was not created" >&2; exit 2; }
    exec "$conda_executable" run --prefix "$environment_prefix" --no-capture-output /usr/bin/env \
        A100SIM_CONFIG_LOADED=0 A100SIM_PYTHON= \
        A100SIM_DEPLOY_EULA_CONFIRMED="${A100SIM_DEPLOY_EULA_CONFIRMED:-}" \
        /bin/bash "$code_root/deploy.sh" --inside-conda "$action" "$@"
fi

[[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]] || {
    echo "The deployment command must run inside a Conda environment" >&2; exit 2;
}
python_version="$("$CONDA_PREFIX/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$python_version" = 3.12 ]] || { echo "The Conda environment must provide Python 3.12" >&2; exit 2; }
command -v uv >/dev/null 2>&1 || { echo "The Conda environment must provide uv" >&2; exit 2; }

if [[ -z "${A100SIM_DEPLOY_EULA_CONFIRMED:-}" && "${OMNI_KIT_ACCEPT_EULA:-}" != YES ]]; then
    read -r -p "Confirm you have accepted NVIDIA's Isaac Sim additional software terms (type YES): " answer
    [[ "$answer" = YES ]] || { echo "NVIDIA terms were not accepted" >&2; exit 2; }
fi
export A100SIM_DEPLOY_EULA_CONFIRMED=YES
export OMNI_KIT_ACCEPT_EULA=YES

mkdir -p "$pid_dir" "$log_dir"
chmod 700 "$pid_dir"

wait_for_service() {
    local name="$1" url="$2" timeout="$3"
    local probe=("$A100SIM_PYTHON" "$code_root/scripts/wait_ready.py" --timeout "$timeout" "$url")
    if [[ "$name" = grpc ]]; then
        probe=("$gateway_python" "$code_root/scripts/wait_ready.py" --grpc --timeout "$timeout" "$url")
    fi
    if ! "${probe[@]}"; then
        echo "Service $name failed to become ready. Log: $log_dir/deploy-$name.log" >&2
        tail -n 60 "$log_dir/deploy-$name.log" >&2 || true
        stop_service "$name" || true
        return 1
    fi
}

start_service() {
    local name="$1" url="$2" timeout="$3"
    shift 3
    local pid start
    if pid="$(recorded_pid "$name")"; then
        echo "$name is already running (pid $pid)"
        wait_for_service "$name" "$url" "$timeout"
        return
    fi
    rm -f -- "$pid_dir/deploy-$name.pid"
    local launcher="$code_root/serve.sh"
    if [[ "$name" = grpc ]]; then launcher="$code_root/serve-grpc.sh"; fi
    nohup /bin/bash "$launcher" "$@" >> "$log_dir/deploy-$name.log" 2>&1 </dev/null &
    pid=$!
    for _ in {1..20}; do
        if start="$(process_start_time "$pid")"; then break; fi
        sleep 0.1
    done
    [[ -n "${start:-}" ]] || { echo "Could not record $name process" >&2; return 1; }
    printf '%s %s\n' "$pid" "$start" > "$pid_dir/deploy-$name.pid"
    started_services+=("$name")
    echo "Starting $name (pid $pid); log: $log_dir/deploy-$name.log"
    wait_for_service "$name" "$url" "$timeout"
    recorded_pid "$name" >/dev/null || {
        echo "$name service exited during startup; check its log for a port conflict or launch error" >&2
        return 1
    }
}

ensure_available_port() {
    local name="$1" port="$2"
    recorded_pid "$name" && return 0
    if "$A100SIM_PYTHON" -c \
        'import socket,sys; s=socket.socket(); s.settimeout(.3); result=s.connect_ex(("127.0.0.1", int(sys.argv[1]))); s.close(); sys.exit(0 if result == 0 else 1)' \
        "$port"; then
        echo "Port $port for $name is already in use. Stop its owner or choose another port in .env" >&2
        return 1
    fi
}

preflight_ports() {
    ensure_available_port generic "$A100SIM_GENERIC_PORT"
    ensure_available_port resident "$A100SIM_RESIDENT_PORT"
    ensure_available_port grpc "${grpc_listen##*:}"
    if [[ -n "${A100SIM_RENDER_ASSET:-}" ]]; then
        ensure_available_port render "$A100SIM_RENDER_PORT"
    fi
    features_root="${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}"
    if [[ ( "$action" != deploy || "${A100SIM_DEPLOY_FEATURES:-all}" = all ) && \
          ( "$action" = deploy || -d "$features_root/packages/isaacsim/exts/isaacsim.sensors.experimental.physics" ) ]]; then
        ensure_available_port features "${A100SIM_FEATURE_PORT:-8322}"
    fi
}

deploy_runtime() {
    if bash "$code_root/doctor.sh" --runtime >/dev/null 2>&1; then
        echo "Base runtime is already prepared"
    else
        bash "$code_root/doctor.sh"
        bash "$code_root/prepare.sh" minimal
        bash "$code_root/install-libs.sh"
        bash "$code_root/get-driver-libs.sh"
        bash "$code_root/build-drjit.sh"
        bash "$code_root/doctor.sh" --runtime
    fi
    bash "$code_root/run-physics-fixed.sh" "$code_root/scripts/smoke_gpu.py" \
        --output "$A100SIM_ROOT/results/deploy-smoke"

    if pid="$(recorded_pid features)"; then stop_service features; fi
    if [[ "${A100SIM_DEPLOY_FEATURES:-all}" = all ]]; then
        bash "$code_root/install-features.sh" all
        bash "$code_root/build-newton-kernels.sh"
    fi
    if recorded_pid grpc >/dev/null; then stop_service grpc; fi
    bash "$code_root/serve-grpc.sh" install
    if [[ "${A100SIM_GRPC_WORKFLOWS:-0}" = 1 ]]; then
        local asset_python="${A100SIM_ASSET_PYTHON:-$code_root/.venv-assets/bin/python}"
        if [[ -z "${A100SIM_ASSET_PYTHON:-}" ]]; then
            "$A100SIM_PYTHON" -m venv "$code_root/.venv-assets"
        fi
        [[ -x "$asset_python" ]] || { echo "Configured asset conversion Python was not found" >&2; return 1; }
        uv pip install --python "$asset_python" -r "$code_root/requirements-assets.txt"
    fi
}

start_services() {
    bash "$code_root/doctor.sh" --runtime
    start_service generic "http://127.0.0.1:$A100SIM_GENERIC_PORT" 240 generic
    start_service resident "http://127.0.0.1:$A100SIM_RESIDENT_PORT" 240 resident
    if [[ -n "${A100SIM_RENDER_ASSET:-}" ]]; then
        start_service render "http://127.0.0.1:$A100SIM_RENDER_PORT" 240 render
    fi
    if [[ ( "$action" != deploy || "${A100SIM_DEPLOY_FEATURES:-all}" = all ) && \
          -d "$features_root/packages/isaacsim/exts/isaacsim.sensors.experimental.physics" ]]; then
        local features_url="http://127.0.0.1:${A100SIM_FEATURE_PORT:-8322}"
        local pid start
        if pid="$(recorded_pid features)"; then
            echo "features is already running (pid $pid)"
            wait_for_service features "$features_url" 360
        else
            rm -f -- "$pid_dir/deploy-features.pid"
            nohup /bin/bash "$code_root/serve-features.sh" >> "$log_dir/deploy-features.log" 2>&1 </dev/null &
            pid="$!"
            for _ in {1..20}; do
                if start="$(process_start_time "$pid")"; then break; fi
                sleep 0.1
            done
            [[ -n "${start:-}" ]] || { echo "Could not record features process" >&2; return 1; }
            printf '%s %s\n' "$pid" "$start" > "$pid_dir/deploy-features.pid"
            started_services+=(features)
            echo "Starting features (pid $pid); log: $log_dir/deploy-features.log"
            wait_for_service features "$features_url" 360
            recorded_pid features >/dev/null || {
                echo "features service exited during startup; check $log_dir/deploy-features.log" >&2
                return 1
            }
        fi
    fi
    start_service grpc "$grpc_listen" 60
}

cleanup_failed_start() {
    local exit_code=$? name
    if [[ "$exit_code" != 0 ]]; then
        for name in "${started_services[@]}"; do stop_service "$name" || true; done
    fi
    return "$exit_code"
}
trap cleanup_failed_start EXIT

case "$action" in
    deploy)
        preflight_ports
        deploy_runtime
        start_services
        ;;
    start)
        preflight_ports
        start_services
        ;;
    status|stop) ;;
esac
