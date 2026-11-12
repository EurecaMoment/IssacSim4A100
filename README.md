# IssacSim4A100

## RTX 4090 分支

`4090` 分支新增 [rtx4090/](rtx4090/)，复用下述 A100 服务的请求校验、场景构建、CUDA PhysX、
持久化任务队列与客户端，使用**官方原生 RTX / Replicator** 输出 RGB、米制相机深度、语义 ID 和 USD。
目标机器上的现有官方运行时为 **6.0.1**；仓库保留的官方源代码基线仍为 **6.2.0**，
启动器直接使用已有运行时，不会把 6.2 源码扩展混装进 6.0.1。

2026-11-12 已在 `infini` 的物理 1 号 RTX 4090（24 GB、驱动 580.95.05、Ubuntu 22.04）
完成实际验证：CUDA 自由落体、方块/球体地面碰撞、512px RGB-D/语义捕获，以及连续 HTTP
primitive 与 mesh 请求。两个 HTTP 任务均为 `succeeded`，产物包含有效的语义像素；
导出的 USD 位姿与仿真状态一致。验证记录位于 `rtx4090/results/smoke-fixed/acceptance.json`
和 `rtx4090/results/http-acceptance.json`，本机结果不随源码提交。

```bash
cd /mnt/data/jby/IssacSim4A100
cp rtx4090/.env.example rtx4090/.env
# 根据机器情况编辑 ISAAC_SIM_DIR、ISAAC_GPU；默认选择物理 1 号卡。
bash rtx4090/run.sh --smoke --output rtx4090/results/smoke
bash rtx4090/run.sh
```

默认统一接口为 `http://127.0.0.1:18409`，支持 `GET /health`、`POST /simulate`、`GET /jobs/<id>`：

```bash
python3 a100/examples/client.py a100/examples/cube.json \
  --url http://127.0.0.1:18409 --wait-ms 0
# 在服务所在机器验证一次 primitive + mesh 的实际完成产物：
/mnt/data/zhouwei/isaacsim/python.sh rtx4090/validate_http.py
```

只有任务返回 `state=succeeded` 才完成。每个渲染任务写入 `render.png`、`depth_m.npy`、
`semantic_ids.npy`、`semantic_labels.json`、`state.npz`、`simulation.json`、`scene.usdc`。
`depth_m.npy` 是相机平面的轴向距离，单位米；背景可以为无穷大。语义类别使用请求的 body ID。
物理、深度、RGB 和语义图来自同一仿真后场景；捕获时不额外推进物理时间。

Kit/Vulkan 与 PhysX 使用相同的 `ISAAC_GPU` **物理编号**，不要同时设置 `CUDA_VISIBLE_DEVICES`。
启动器记录 GPU UUID；CUDA tensor 设备必须与所选编号相符，否则报错。
4090 支持立方体、球体和准备好的 `mesh.npz`，不支持 A100 自定义 Gaussian 渲染。
沿用的 `render.spp` 在实时 RTX 下控制子帧数，结果明确记录此差异，不等同于 Mitsuba 路径追踪采样数；
`render.seed` 保留在 `request.json` 中，但原生 RTX 的时序采样目前不按该种子控制。
输入光照强度转换为原生 USD 光度单位，因此两个后端的图像并不保证像素一致。
本分支没有为 HTTP 接口新增关节控制、LiDAR、ROS2 或 Isaac Lab；官方运行时保留其原有扩展。

前台运行时使用 Ctrl+C 退出；常驻运行可保存日志：

```bash
mkdir -p rtx4090/state
nohup bash rtx4090/run.sh > rtx4090/state/service.log 2>&1 < /dev/null &
curl --fail http://127.0.0.1:18409/health
# 就绪后的 Python 进程 PID：rtx4090/state/service.pid
kill -TERM "$(cat rtx4090/state/service.pid)"
```

以下为保留的 A100 部署说明。

在 NVIDIA A100 上运行 **Isaac Sim 6.2 GPU PhysX 仿真 + Mitsuba/Dr.Jit CUDA 渲染**。

本仓库 fork 自 [isaac-sim/IsaacSim](https://github.com/isaac-sim/IsaacSim)，A100 分支基于官方 `v6.2.0` / `release/6.2`（`74a9356b20250dd60337d90b4b4d913c9454d32b`）。官方源代码和历史保留，原版说明见 [README.upstream.md](README.upstream.md)；当前部署的自定义代码、补丁、启动器、验证和资产处理示例集中在 [a100/](a100/)。

**A100 没有 RT Core，不满足官方完整 RTX 渲染器的硬件要求。** 本适配关闭 Kit 原生 RTX，保留 CUDA PhysX，以 Mitsuba/Dr.Jit 的 CUDA/OptiX 路径追踪渲染实时 USD 场景。它是已部署的服务适配，不代表完整 Isaac Sim 的所有功能均已移植到 A100。

![六个 FastTrellis 资产在 A100 上统一布光的示例](a100/examples/scene-preview.jpg)

*实际部署渲染结果；原始几何和大体积资产未随仓库分发。*

## 已验证的环境

下面是 2026-11-12 导出代码时的实际运行环境；其他 GPU、驱动和系统组合需要重新验证。

| 项目 | 当前配置 |
| --- | --- |
| GPU | A100-SXM4-80GB，单服务实例使用一张卡 |
| 宿主系统 | Linux x86_64，Ubuntu 20.04.6 |
| NVIDIA 驱动 | 535.161.08 |
| CUDA Toolkit | 12.2，默认 `/usr/local/cuda-12.2` |
| 隔离运行环境 | Ubuntu 24.04 Noble rootfs；用户命名空间 + bind mount + chroot |
| Python | uv 管理的独立 CPython 3.12 |
| Isaac Sim Python 包 | `6.2.0.0` |
| 已部署运行时的 VERSION | `6.2.0-rc.14+release.52805.02189a84.gl` |
| 渲染 | Mitsuba 3.9.1，Dr.Jit 1.5.0，variant `cuda_ad_rgb` |
| Python 主要依赖 | NumPy 2.3.1，Pillow 12.1.1，aiohttp 3.14.5，orjson 3.11.3 |

Python 包版本、运行时内部构建字符串和仓库源码 tag 是不同的版本信息，上表分别保留。Noble rootfs 提供较新的 glibc / C++ 运行库，不需要升级宿主系统。驱动库从宿主绑定，OptiX 用户态库从匹配版本的 NVIDIA 包中提取。

宿主需要 `bash`、`curl`、`tar`、`git`、`dpkg-deb`、`unshare`、`mount`、`chroot`、C/C++ 编译器、CMake ≥ 3.13 和 `uv`。建议预留至少 50 GB 磁盘空间；大网格场景还需要相应的内存和显存。后台常驻另需可用的 systemd 用户服务。

先检查 GPU 和用户命名空间：

```bash
nvidia-smi
unshare -Ur -m true
```

如果第二条被宿主策略禁止，需要管理员允许用户命名空间，或提供等效的容器运行环境。仓库当前的启动脚本面向原生 Linux，Windows 不直接运行这些服务。

## 功能范围

| 能力 | 当前服务状态 |
| --- | --- |
| CUDA 刚体物理、GPU broadphase | 已实现，强制 GPU 执行 |
| 方块、球体、mesh 刚体 | 已实现；动态 mesh 使用 convex hull 碰撞 |
| 场景输入 | 位姿、尺寸、质量、重力、步长、步数、地面 |
| USD 场景与结果导出 | 已实现，包含仿真后位姿和图像 |
| RGB 渲染 | CUDA/OptiX，mesh 和自定义 Gaussian 路径 |
| 相机和统一布光 | 轨道相机、dome、矩形主光及最多 8 个补光 |
| 多资产组合 | 支持；提供 GLB 转换、带种子的随机陈列和三视角示例 |
| 常驻队列、批量仿真 | 已实现，前端按条件分流到快速路径或通用后端 |
| 官方原生 RTX / Replicator 传感器 | 此 A100 后端不提供 |
| 深度、语义标注、LiDAR | 尚未在现有服务中实现 |
| 机器人关节控制、ROS2、Isaac Lab | 尚未集成到现有服务；需要对应扩展、资产及额外接口 |
| 完整 PBR 纹理网络、软体 | 不在当前适配接口范围内 |

资产视觉目前使用**顶点颜色**。FastTrellis GLB 中的 base-color 纹理可以按原始 UV 采样并烘焙到顶点，保留几何；metallic/roughness 贴图、透明材质和复杂材质网络不会完整还原。没有关节和碰撞定义的普通 mesh，也不会自动成为可控制的机器人。

## 安装

### 1. 获取代码并配置

适配服务不依赖官方示例的大型 LFS 资产，可先跳过这些下载：

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --branch main https://github.com/EurecaMoment/IssacSim4A100.git
cd IssacSim4A100/a100
uv python install 3.12
cp .env.example .env
```

编辑 `.env`：

```bash
ISAAC_GPU=0
A100SIM_CUDA_ROOT=/usr/local/cuda-12.2
A100SIM_DRIVER_VERSION=535.161.08
```

`ISAAC_GPU` 是 `nvidia-smi` 的物理编号或 GPU UUID；原部署使用物理编号 2，新机器应自行选择。`serve.sh` 会解析为 UUID，可用 `A100SIM_EXPECTED_GPU_UUID` 进一步固定卡的身份。隔离后 CUDA 内部显示 `cuda:0`，不代表选择了宿主物理 0 号卡。

Python 默认由 `uv python find 3.12` 查找。如果未正确找到，把 `A100SIM_PYTHON` 设置为用户目录中 uv 管理的 **Python 3.12 绝对路径**。请使用独立 Python，不要直接混用 Conda 中的 Isaac、CUDA 或渲染包。

安装和使用 NVIDIA 运行时须接受 [Isaac Sim additional software and materials license](https://www.nvidia.com/en-us/agreements/enterprise-software/isaac-sim-additional-software-and-materials-license/)。确认接受后，在 `.env` 保留 `OMNI_KIT_ACCEPT_EULA=YES`。

### 2. 准备运行环境

```bash
bash prepare.sh minimal
bash install-libs.sh
bash get-driver-libs.sh
bash build-drjit.sh
```

各步作用：

1. `prepare.sh minimal` 下载 Ubuntu Noble rootfs、固定版本的 Isaac 包和 Python 依赖，并通过 HTTP Range 提取 PhysX 所需的 Kit 扩展依赖。下载记录保存在 `downloads/physics-extensions.json`，未解析的必要扩展会报错。
2. `install-libs.sh` 在 rootfs 中下载并解压系统库，不修改宿主安装的软件包。
3. `get-driver-libs.sh` 只提取匹配驱动版本的 NVIDIA 用户态库，提供 `libnvoptix.so.1`，不安装或更换宿主驱动。默认下载地址已按 535.161.08 配置；其他版本可通过 `A100SIM_NVIDIA_DEB_URL` 指定匹配的官方 Debian 包。
4. `build-drjit.sh` 固定 `drjit-core` 到 `3b96411288b9938889045308654ba37d4ef24b71`，应用 [A100 补丁](a100/patches/drjit-core-a100.patch)，编译并替换渲染环境中的 `libdrjit-core.so`，保留 `.original` 备份。

补丁包含 R535 OptiX JIT 所需的 PTX `copysign` 位运算实现，以及旧 GCC 的 x86_64 Linux 线程指针兼容修改。不要只安装原版 Mitsuba wheel 后漏掉这一步；重新安装 Dr.Jit 后也需要重新应用已编译的库。编译使用 `-march=native`，应在部署机器上完成。

需要完整官方 Kit 扩展缓存时，可将第一步改成 `bash prepare.sh full`，下载量会显著增加。这不会自动启用原生 RTX 或新增 HTTP 功能。

如果已有运行环境，可以在 `.env` 设置 `A100SIM_ROOT=/absolute/runtime/path` 复用 `packages/`、`rootfs/`、`render-packages/` 和 `http-packages/`。代码仍从当前 `a100/` 加载。资产若在运行目录外，要设置 `A100SIM_ASSET_ROOT`；所有提交的资产目录都必须位于该根目录内，且不能通过符号链接越界。

### 3. 做一次 GPU 验证

```bash
bash run-physics-fixed.sh scripts/smoke_gpu.py --output "$PWD/results/smoke"
```

成功时打印 `A100_GPU_SMOKE_SUCCEEDED`，产生 `render.png`、`scene.usdc` 和 `simulation.json`。应看到 `physics_device: cuda:0`，渲染后端为 CUDA，且没有启用 `omni.hydra.rtx`。原部署的 64 方块物理验证也保留在 `physics_smoke.py`。

这些脚本来自已运行的部署；新的安装流程会从网络重建大体积运行时，而不是分发机器镜像。Ubuntu `current` 镜像和包源可能变化，可用 `A100SIM_ROOTFS_URL` 与 `A100SIM_ROOTFS_SHA256` 固定自己的 rootfs 快照。

## 启动与停止

### 前台运行

打开两个终端，在同一个 `a100/` 目录运行：

```bash
# 终端 1：通用物理与渲染后端
bash serve.sh generic
```

```bash
# 终端 2：批量仿真及请求分流前端
bash serve.sh resident
```

| 默认地址 | 用途 |
| --- | --- |
| `http://127.0.0.1:8321` | 统一入口、常驻队列、批量快速路径 |
| `http://127.0.0.1:18323` | 通用 GPU 场景仿真和渲染 |
| `http://127.0.0.1:8320` | 可选：固定资产的 mesh/Gaussian 渲染服务 |

预加载资产服务不是启动核心服务的必要条件。只有需要它时，设置 `.env` 中的 `A100SIM_RENDER_ASSET=/absolute/path/to/prepared/asset`，再运行 `bash serve.sh render`。mesh 需要 `mesh.npz`，Gaussian 需要符合服务约定的 `object.ply`；普通点云 PLY 不能直接替代 Gaussian 数据。

### systemd 后台常驻

```bash
bash install-systemd.sh
bash start.sh
curl --fail http://127.0.0.1:8321/health
curl --fail http://127.0.0.1:18323/health
```

`start.sh` 启用服务并等待就绪；配置了 `A100SIM_RENDER_ASSET` 时才启动第三个服务。健康检查应返回 `status=ready`、`cpu_fallback=false`、`native_rtx=false`。首次初始化和着色器编译可能需要较长时间。

```bash
systemctl --user status 'isaacsim-a100-*'
journalctl --user -u 'isaacsim-a100-*' -f
bash stop.sh
```

用户服务要在退出登录后继续运行，宿主需要为当前用户启用 linger，例如管理员执行 `loginctl enable-linger <username>`。在 Slurm 集群上还应遵守已有 GPU 分配方式，在获得 GPU 的节点和会话内启动；安装脚本不申请调度资源。

新服务名为 `isaacsim-a100-*`，不会自动替换原部署的 `isaacsim-gpu2-*`。旧的 `serve-gpu2.sh`、`start-gpu2.sh` 等入口保留为兼容别名。与已有服务共存测试时，用 `.env` 设置其他端口和 `A100SIM_STATE_ROOT` / `A100SIM_LOCAL_STATE`。

## 调用服务

从 `a100/` 提交带统一光照的方块和球体示例：

```bash
python3 examples/client.py examples/cube.json
```

客户端只提交一次，通过返回的 job ID 查询完成状态。只有 **`state=succeeded`** 才表示完成；排队、运行中或超时都不应通过再次提交同一请求来等待。

统一前端支持长轮询：

```bash
curl --fail -X POST 'http://127.0.0.1:8321/simulate?wait_ms=30000' \
  -H 'Content-Type: application/json' --data-binary @examples/cube.json
curl --fail 'http://127.0.0.1:8321/jobs/<job-id>?wait_ms=30000'
```

直接调用通用后端时使用普通轮询，不带 `wait_ms`：

```bash
python3 examples/client.py examples/cube.json --url http://127.0.0.1:18323 --wait-ms 0
```

请求包含 `bodies`、`steps`、`dt`、`gravity`、`ground` 和可选的 `render`。当前主要限制：1–256 个刚体、1–1200 步、正方形图像 64–1024 像素、1–256 spp，队列容量默认 64。方块使用 `size`，球使用 `radius`，mesh 使用服务端绝对 `asset` 目录；旋转是 `orientation_xyzw`。

`mesh.npz` 必须包含 `vertices`、`normals`、`faces`、`colors`。颜色是 0–1 RGB；存在 `colors_linear` 键时按线性颜色读取，否则由 sRGB 解码。细节见 [physics_service.py](a100/a100sim/physics_service.py) 的 `PhysicsQueue.prepare()`。

结果 JSON 返回服务器上的文件路径，HTTP 接口不承担任意文件下载。通用结果在 `results/physics-service/<id>/`；快速路径结果在 `${A100SIM_LOCAL_STATE}/jobs/<id>/`，默认 `/var/tmp/a100sim-<uid>/jobs/`，保留策略默认 24 小时或 4096 个已完成任务。首次重启发现的未完成通用任务会标为 `interrupted`，不会自动重跑。

## FastTrellis 多资产与统一布光

转换环境与服务分开安装。在服务器上准备自己的 GLB 文件后：

```bash
uv venv --python 3.12 .venv-assets
uv pip install --python .venv-assets/bin/python -r requirements-assets.txt
.venv-assets/bin/python examples/prepare_fasttrellis_assets.py /path/to/glb-or-directory \
  --output "$PWD/assets/fasttrellis" --source-up y --max-size 1.2
python3 examples/make_scene.py assets/fasttrellis/assets.json --output results/gallery --seed 20261112
python3 examples/client.py results/gallery/overview.json
python3 examples/client.py results/gallery/left-angle.json
python3 examples/client.py results/gallery/close-up.json
```

多个输入可以一次传入。转换保持完整网格，将 Y-up 转成 Z-up，按最长边归一化到 1.2 米，采样原始 base-color 纹理并记录原文件 SHA-256。已有 Z-up 资产用 `--source-up z`。

场景生成器按种子打乱顺序、轻微随机化位置和角度，组合展台、背景、暖色主光与冷色补光，并生成三个视角。零重力加一次 CUDA 物理步保留陈列位姿。原部署曾完成六个 FastTrellis 资产、约 565 万三角面的统一场景渲染；实际耗时依几何量、分辨率和 spp 而变。

可选的 [denoise_image.py](a100/examples/denoise_image.py) 使用另行安装的 OIDN 2 库对图像做 CPU 后处理。它不改变物理与光线追踪的 CUDA 执行路径，也不是核心启动依赖。

## 验证与性能

```bash
# Python 3.12；从 a100/ 运行，不需要 GPU
uv run --python 3.12 --no-project python -m unittest discover -s tests -v

# 已启动服务后的端到端检查与并发基准
mkdir -p results/performance
python3 benchmarks/validate_http.py
python3 benchmarks/gpu2_http.py --url http://127.0.0.1:8321 --concurrency 64 --rounds 3 --workload default \
  --report results/performance/benchmark.json
```

`benchmarks/` 的检查需要在可读取服务端结果路径的机器上运行；`validate_http.py` 包含 65 并发的队列压力检查，应在服务空闲时执行。`a100sim/validate_*` 是原部署的研究验证脚本，部分依赖历史基准报告，不属于全新安装的必跑检查。涉及外部资产的测试需要设置 `A100SIM_RENDER_ASSET`。快速路径最多 64 个环境、每环境最多 64 个 0.2 米方块，只有符合地面、相机、尺寸和采样等条件的请求会合批，其余场景分流到通用后端。详细条件见 [resident_service.py](a100/a100sim/resident_service.py)。

此次整理已在现有 A100 运行环境上验证迁移后的 CUDA 仿真、128px 渲染和 USD 导出，并执行独立的队列/路径测试、Shell 语法检查及 systemd 模板验证。全新机器上的完整依赖下载与安装尚未端到端重跑。

历史上曾在特定 64 并发方块负载中完成亚秒级响应；这不适用于任意 mesh 场景，也不是所有请求的延迟保证。基准应区分冷启动、预热、排队、GPU 计算和结果写盘。

## 常见问题

| 现象 | 排查方向 |
| --- | --- |
| glibc / GLIBCXX 版本错误 | 确认通过本仓库的 `run-*-fixed.sh` 启动；先完成 `install-libs.sh`。特殊环境可指定 `A100SIM_CXX_LIB_DIR` |
| 找不到 `libnvoptix.so.1` | 执行 `get-driver-libs.sh`，核对驱动版本与 `A100SIM_NVIDIA_ROOT` |
| OptiX JIT / PTX `copysign` 错误 | 检查 Dr.Jit 固定版本和补丁库，重新执行 `build-drjit.sh` |
| `asset must be ... under ...` | 使用服务端绝对目录，确保位于 `A100SIM_ASSET_ROOT` 内 |
| systemd 用户服务不可用 | 先用两个终端前台启动，检查用户会话和集群限制 |
| 端口冲突 | 调整 `.env` 的三个端口；前端会按配置连接通用后端 |
| 材质像塑料或细节丢失 | 当前顶点颜色渲染不完整复现源 PBR 材质；检查网格顶点密度和 base-color 转换 |

## 源码与许可

`a100/source-manifest.json` 保存整理前部署源码的 SHA-256 和版本来源，便于追溯。源码经过路径、设备、端口、安装与启动配置的迁移整理；它不是整台服务器的镜像。

本仓库保留官方 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md)。新增适配代码按 Apache-2.0 提供。Isaac Sim / Omniverse Kit 运行时、NVIDIA 驱动组件、Mitsuba、Dr.Jit、OIDN 及模型资产分别遵循各自许可。Dr.Jit 补丁基于 [mitsuba-renderer/drjit-core](https://github.com/mitsuba-renderer/drjit-core)；`a100/research/` 中 NVIDIA 源码保留其原版权声明。

大型运行时二进制、rootfs、wheel、模型权重、生成资产、作业结果及本机配置均由 `.gitignore` 排除，按上面的步骤在部署机获取。
