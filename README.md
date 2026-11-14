# IssacSim4A100

在自己的 NVIDIA A100 上部署 **Isaac Sim 6.2.0 CUDA 物理仿真、机器人控制与 Mitsuba CUDA 渲染**。

本仓库 fork 自 [isaac-sim/IsaacSim](https://github.com/isaac-sim/IsaacSim)。适配代码维护在 [`A100` 分支](https://github.com/EurecaMoment/IssacSim4A100/tree/A100)，官方源码基线为 `v6.2.0` / `release/6.2`（`74a9356b20250dd60337d90b4b4d913c9454d32b`）。官方源代码与历史保留，原版说明见 [README.upstream.md](README.upstream.md)；新增部署代码位于 [a100/](a100/)。

**A100 没有 RT Core，不满足官方完整 RTX 应用的硬件要求。** 本方案关闭 Kit 原生 RTX，使用 CUDA PhysX / Newton 做仿真，以 Mitsuba/Dr.Jit CUDA/OptiX 渲染 USD 场景。自定义图像标注、物理射线扫描和 ROS 数据桥的范围见下表。

![多资产随机陈列与统一布光](a100/examples/scene-preview.jpg)

*实际渲染示例；资产文件需要自行准备。*

## 部署前准备

| 条件 | 要求 |
| --- | --- |
| 平台 | 原生 Linux x86_64；Windows 不直接运行这些启动器 |
| GPU | NVIDIA A100；每个服务实例选择一张可用设备，多卡可运行独立实例 |
| 驱动 | 已正确安装 NVIDIA Linux 驱动，`nvidia-smi` 能访问分配给自己的 A100 |
| CUDA Toolkit | 安装与驱动兼容的 Toolkit，提供 `nvcc`；自动从 `CUDA_HOME`、`CUDA_PATH`、`nvcc` 或 `/usr/local/cuda` 检测 |
| Python | 仓库提供的 Conda 环境，使用 CPython 3.12 和环境内的 `uv` |
| 用户权限 | 支持无特权 user/mount namespace：`unshare -Ur -m true` 成功 |
| 磁盘 | 至少预留 50 GB，另加资产、完整扩展缓存与结果所需空间 |
| 构建工具 | C/C++ 编译器、CMake ≥ 3.13、Git、curl、tar、dpkg-deb、util-linux、binutils |
| 网络 | 可访问 NVIDIA PyPI、PyPI、Canonical rootfs、GitHub；驱动库下载支持配置镜像 |

启动器创建 Ubuntu 24.04 rootfs，为 Python 扩展提供统一的 glibc / C++ 运行库，并绑定宿主驱动与 Toolkit。rootfs 不安装宿主内核驱动。无需把宿主系统固定成某个 Ubuntu 版本，但必须具备上述工具、内核权限和驱动。

Debian / Ubuntu 可由管理员安装基础工具：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git tar dpkg-dev util-linux \
  build-essential cmake binutils
```

其他发行版使用相应包管理器安装等效工具。按 [Conda 官方说明](https://docs.conda.io/projects/conda/en/stable/user-guide/install/index.html)安装 Conda；Python、`uv`、CMake 和 Ninja 由仓库的 `environment.yml` 提供。C/C++ 编译器使用宿主的 `build-essential`。按 [NVIDIA CUDA 安装指南](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)准备驱动和 Toolkit。`nvidia-smi` 显示的 CUDA 版本表示驱动支持的 API 上限，不表示已经安装相同版本的 Toolkit。

本适配的运行包固定为 Isaac Sim `6.2.0.0`、Warp `1.17.0`、Mitsuba `3.9.1`、Dr.Jit `1.5.0`；可选 Newton `1.6.1`、MuJoCo / MuJoCo Warp `3.12.0`。Isaac Python 包版本、运行时内部构建号与源码 tag 是三种信息，诊断时应分别记录。硬件和系统组合的兼容性仍须在部署后验收。

## 从零安装

以下命令在能访问 A100 的普通 Linux Shell 中执行；GPU 驱动和 Toolkit 仍需预先安装。

### 一键部署

安装 Conda、NVIDIA 驱动和 CUDA Toolkit，并确认 Linux 允许无特权 user/mount namespace 后，运行：

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --branch A100 https://github.com/EurecaMoment/IssacSim4A100.git
cd IssacSim4A100/a100
bash deploy.sh
```

脚本会创建或更新仓库 Conda 环境，准备 Isaac Sim 运行时、执行 GPU smoke，再启动基础服务、机器人与物理传感器服务和可选 ROS 2 桥。Newton、机器人导入等工作流通过 gRPC 按需执行；默认开启工作流时，还会安装独立的 GLB/PBR 转换环境。首次运行会询问 NVIDIA 许可确认；工作进程和 gRPC 网关以本地后台进程运行，日志写入 `logs/`。

```bash
cd IssacSim4A100/a100
bash deploy.sh status
bash deploy.sh stop
```

只部署基础仿真服务时运行 `A100SIM_DEPLOY_FEATURES=none bash deploy.sh`。目标机器必须能直接访问 A100。

### 1. 获取代码并创建 Conda 环境

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --branch A100 https://github.com/EurecaMoment/IssacSim4A100.git
cd IssacSim4A100/a100
conda env create -f environment.yml
conda activate isaacsim-a100
cp .env.example .env
```

跳过的官方 LFS 示例资产不是适配服务的必要依赖。

### 2. 配置本地环境

编辑 `.env`。默认配置会检测设备、Python、Toolkit 和驱动，基础部署只需核对 GPU 分配与 NVIDIA 许可：

```bash
OMNI_KIT_ACCEPT_EULA=YES
ISAAC_GPU=auto
A100SIM_PYTHON=
A100SIM_CUDA_ROOT=
A100SIM_DRIVER_VERSION=
```

仅在接受 [Isaac Sim additional software and materials license](https://www.nvidia.com/en-us/agreements/enterprise-software/isaac-sim-additional-software-and-materials-license/)后设置 `OMNI_KIT_ACCEPT_EULA=YES`。

`ISAAC_GPU=auto` 在继承的 `CUDA_VISIBLE_DEVICES` 范围内选择 A100；若没有该环境变量，则从可访问设备中选择。也可填写部署者自己的物理设备编号或完整 UUID。显式配置同样不能越过继承的设备范围。需要固定设备身份时设置 `A100SIM_EXPECTED_GPU_UUID`。单卡隔离后程序内部使用 `cuda:0`，这是进程内逻辑编号。

激活环境后，启动器会自动使用 `$CONDA_PREFIX/bin/python`；无需填写 `A100SIM_PYTHON`。如使用自建环境，可将 `A100SIM_PYTHON` 设置为其中的 Python 3.12 绝对路径。`A100SIM_CUDA_ROOT` 通常自动检测，`A100SIM_DRIVER_VERSION` 留空即可。运行时目录默认就是 `a100/`，可用 `A100SIM_ROOT` 放到其他磁盘，代码与运行数据可以分开。

```bash
bash doctor.sh
```

只有报告 `success: true` 才继续。它会检查 Linux、Conda Python、工具、GPU 可见范围、CUDA Toolkit 与用户命名空间，并报告是否可用条件图节点。用户命名空间被系统策略禁用时，需要管理员启用，或自行提供等效容器环境；隔离运行使用前述 rootfs 路径。

### 3. 安装基础运行环境

```bash
bash prepare.sh minimal
bash install-libs.sh
bash get-driver-libs.sh
bash build-drjit.sh
bash doctor.sh --runtime
```

安装步骤：

1. `prepare.sh minimal` 下载 Ubuntu Noble rootfs、固定版本 Isaac / Python 包，并从 Kit cache 提取物理扩展依赖。下载计划保存在 `downloads/`。需要完整 Kit 扩展缓存时改用 `bash prepare.sh full`，下载量明显增加。
2. `install-libs.sh` 将 Linux 用户态依赖解压到 rootfs。
3. `get-driver-libs.sh` 自动读取宿主驱动版本，优先复用已安装且版本匹配的 OptiX 库；缺少时依次尝试 NVIDIA 官方 Tesla 数据中心驱动与 XFree86 Linux 驱动归档，**只解包** `libnvoptix` / `libnvidia-rtcore`。支持 `A100SIM_NVIDIA_RUN_URL` 或 `A100SIM_NVIDIA_DEB_URL` 镜像覆盖；配置镜像后仅使用指定地址，归档内库必须与检测到的驱动版本匹配。
4. `build-drjit.sh` 从固定提交 `3b96411288b9938889045308654ba37d4ef24b71` 编译 Dr.Jit core，并应用 [兼容补丁](a100/patches/drjit-core-a100.patch)。原库保存为 `.original`；重新安装 Dr.Jit 后需要重新应用。构建使用 `-march=native`，应在部署机器上运行。

rootfs 默认镜像 URL 可通过 `A100SIM_ROOTFS_URL` 覆盖；需要固定快照时同时设置 `A100SIM_ROOTFS_SHA256`。HTTP 代理使用标准 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。离线部署需提前准备相同版本的下载内容；仓库不包含运行时二进制。

### 4. 验证 CUDA 仿真与渲染

```bash
bash run-physics-fixed.sh scripts/smoke_gpu.py --output "$PWD/results/smoke"
```

成功打印 `A100_GPU_SMOKE_SUCCEEDED`，输出 `render.png`、`scene.usdc` 和 `simulation.json`。核对物理设备为 `cuda:0`、渲染后端为 CUDA、没有启用原生 RTX。脚本失败时先排查诊断与日志，再启动服务。

### 5. 启动基础服务

打开两个终端，在同一个 `a100/` 目录分别运行：

```bash
# 终端一：通用仿真与渲染后端
bash serve.sh generic
```

```bash
# 终端二：统一队列与批量仿真入口
bash serve.sh resident
```

所有下列 HTTP 地址均为强制本机监听的内部工作进程；对外入口为 gRPC `127.0.0.1:50051`（远程监听需配置 TLS）。

| 默认地址 | 内部服务 |
| --- | --- |
| `http://127.0.0.1:8321` | 统一入口、批量仿真、请求分流 |
| `http://127.0.0.1:18323` | 通用 CUDA 场景仿真与渲染 |
| `http://127.0.0.1:8320` | 可选：预加载资产渲染 |
| `http://127.0.0.1:8322` | 可选：机器人、传感器、ROS 2、标注、布料 |

```bash
curl --fail http://127.0.0.1:8321/health
curl --fail http://127.0.0.1:18323/health
python3 examples/client.py examples/cube.json
```

等待健康状态变为 `ready`。仿真服务应报告 `cpu_fallback=false`、`native_rtx=false`。首次启动和 JIT 编译会较慢。

预加载资产渲染按需开启：将 `A100SIM_RENDER_ASSET` 设置为自己准备的资产目录，再运行 `bash serve.sh render`。mesh 需要 `mesh.npz`；Gaussian 需要约定格式的 `object.ply`，普通点云 PLY 不等于 Gaussian 资产。

### 6. 可选：安装机器人、传感器、ROS 2 与 Newton

```bash
bash install-features.sh all
# 为本机 GPU 和 Toolkit 构建兼容的 Newton 原生 tensor kernels
bash build-newton-kernels.sh
# 前台运行可选功能服务
bash serve-features.sh
```

可选包安装到 `${A100SIM_FEATURES_ROOT:-$A100SIM_ROOT/features}/packages`。完整 wheel 与 Python 依赖校验 SHA-256；Kit cache 按固定扩展计划提取并检查 ZIP 完整性。

只需要部分能力时，可使用 `robotics`、`ros2`、`assets` 或 `newton` profile 代替 `all`。这些 profile 含机器人公共依赖；没有 ROS 包时设置 `A100SIM_FEATURE_ROS2=0`。没有 Newton 包时服务不会提供 `cloth` 动作。Newton 原生内核构建步骤只对 Newton 工作流需要，构建要求保留克隆仓库中的官方 `source/`。

```bash
curl --fail http://127.0.0.1:8322/health
python3 examples/feature_client.py '{"action":"snapshot"}'
python3 examples/feature_client.py '{"action":"command","targets":[0.6,-0.4],"steps":240}'
python3 examples/feature_client.py '{"action":"capture","size":512,"spp":32}'
python3 examples/feature_client.py '{"action":"cloth","frames":90}'
```

### 7. 可选：配置后台服务

```bash
bash install-systemd.sh
bash start.sh
# 已安装可选功能时
bash start-features.sh
systemctl --user status 'isaacsim-a100-*'
journalctl --user -u 'isaacsim-a100-*' -f
```

`start.sh` 启动并等待基础服务就绪，配置了渲染资产时还会启动预加载渲染。systemd 是可选的后台托管方式；日常单机运行可直接在已激活的 Conda 环境中使用前台 Bash 命令。可选功能使用单独的 `start-features.sh` / `stop-features.sh`：

```bash
bash stop-features.sh
bash stop.sh
```

后台方式需要可用的 systemd 用户会话；退出登录后继续运行还需管理员为部署用户启用 linger。systemd 不会继承当前终端的 Conda 激活状态；使用该可选方式时，在 `.env` 显式填写 `A100SIM_PYTHON` 为该 Conda 环境的绝对 Python 路径。端口、缓存与结果目录全部可通过 `.env` 调整。

## 功能范围

“服务”表示已包装为 gRPC 的工作进程动作；“脚本”表示已包装为 gRPC 的独立工作流（运维也可本机运行）。实测证据位于 [feature-validation.json](a100/research/feature-validation.json)，扩展清单见 [extension-inventory.json](a100/research/extension-inventory.json)。安装或加载扩展本身不算功能验收。

| 能力 | 支持范围 |
| --- | --- |
| CUDA 刚体物理 | 服务；PhysX GPU dynamics、GPU broadphase、TGS，方块 / 球 / mesh |
| mesh 碰撞 | 服务；动态 mesh 使用 convex hull |
| 多资产场景与统一打光 | 服务 / 示例；随机排列、展台、多个视角、mesh / 自定义 Gaussian 渲染 |
| USD 编辑与导出 | 服务 / 脚本；机器人真实 CUDA 位姿写入独立 USD 副本再渲染 |
| RGB、深度、距离、法向 | 服务；Mitsuba CUDA 主射线标注，米制 Z-depth / range、世界坐标法向 |
| 实例 / 语义 ID、有效 mask | 服务；按 USD prim 与显式语义属性映射 |
| 机器人位置 / 速度 / 力矩 | 服务；真实 PhysX articulation；演示支持 1–16 个双关节机械臂 |
| 多环境克隆与动力学 | 服务；GridCloner、Jacobian、质量矩阵、关节作用力 |
| IMU、接触、JointState、Effort | 服务；官方物理传感器 |
| 物理射线扫描 | 服务；RaycastSensor 与 180 束平面距离扫描 |
| 抓取演示 | 服务；碰撞表面查询 + 自定义 PhysX fixed-joint 吸附、抬升、释放 |
| 轨迹与差速控制 | 服务；官方关节轨迹生成并实际执行；差速动作只计算轮速 |
| ROS 2 | 服务；bundled Jazzy / rclpy / FastDDS + 自定义 bridge，5 类发布 topic 和关节命令订阅 |
| URDF / MJCF → USD | 脚本；保留 articulation，提供导入后 CUDA 运动检查 |
| Gain Tuner 核心 | 脚本；关节发现与有效惯量；GUI / 完整自动调参未验收 |
| Newton XPBD | 脚本 / 布料服务；CUDA 刚体、独立布料、USD / NPZ 轨迹 |
| Newton MuJoCo Warp | 脚本；按驱动 API 自动禁用不可用的条件图节点，保留 CUDA 求解 |
| Newton 原生 tensor API | 脚本；本机编译兼容内核后，刚体位姿读写、速度 / 质量 / 惯量读取及 MuJoCo 关节控制已验收 |
| observation / reward step | 服务；`rl_step` 返回基础数据；训练算法、Torch / Isaac Lab 训练栈尚未集成 |

![机器人场景的 RGB、深度、法向与实例标注](a100/examples/features-preview.jpg)

原生 RTX Camera / LiDAR / Radar、Replicator writer、完整 ROS OmniGraph bridge、GUI / WebRTC 视口不在当前适配范围。自定义标注和物理扫描不具有 RTX 传感器的完整光学 / 信号模型。官方 Surface Gripper 插件已加载，但抓住物体的完整路径尚未验收；服务中的吸附演示使用独立约束实现。

IK、RMPflow、cuMotion、Pink、策略回放、PhysX 体积软体、流体 / 粒子、触觉和车辆仍需各自依赖、资产及验收。安装完整扩展缓存不会自动开放这些能力。

## Isaac Sim 6.2.0 逐项能力核查

基线统一为官方源码 **`v6.2.0`**、Python 运行包 **`6.2.0.0`**。所用运行包的内部构建标识为 `6.2.0-rc.14+release.52805.02189a84.gl`；源码标签、分发包版本和构建号分别保留，不能互相替代。以下按 30 类能力逐项核查，附件中旧的 6.0.1 描述不能当作本适配的验收结果。

核查依据是当前分支的官方源码、[6.2 文档](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/index.html)、[A100 历史数值验收](a100/research/feature-validation.json)及现有适配实现。官方扩展目录中的组件存在，只能证明源码/安装内容存在。原生 RTX 需要受支持的 GPU；A100 的 Mitsuba CUDA 替代方案只覆盖本文列出的渲染与标注子集。

| 状态 | 精确定义 |
| --- | --- |
| `AVAILABLE` | 对该条明确界定的功能已有执行实现；不表示所属整个产品领域均已实现 |
| `PARTIAL` | 可执行其中限定的子集；必须阅读 `reason`，例如差速只计算轮速、导入不自动实例化机器人 |
| `UNAVAILABLE` | 适配未接入，或所需工作进程/依赖当前未就绪 |
| `UNSUPPORTED` | 当前硬件或服务边界不支持；例如原生 RTX、远程任意代码执行 |
| `UNVERIFIED` | 源码/依赖中可能具备，但尚无可接受的实现与验收；拒绝执行 |

`Feature.support` 表示实现范围；`Feature.availability` 叠加实时进程状态。例如 PBR 的 `support=PARTIAL`，未启动仿真后端时其 `availability=UNAVAILABLE`。服务启动也不会把尚未验收的规划、训练等功能自动变为可用。实现状态不是实时 A100 性能承诺。

完整目录覆盖 **30 类、138 个子功能**，其中 64 个条目映射到真实任务，剩余条目逐一说明缺失内容。多个条目可共用同一个底层任务，因此不能把条目数理解为 138 个独立求解器或 138 个执行接口。接口查询为 `GetApiCatalog`，执行为 `InvokeFeature` 或强类型 `SubmitTask`；未实现条目会在入队前返回 `UNIMPLEMENTED`。

<details>
<summary>展开全部 30 类、138 个子功能及其 gRPC 映射</summary>

<!-- BEGIN GRPC FEATURE AUDIT -->

#### 1. 三维场景搭建与编辑

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `scene.assemble` | 几何体、多资产、位置/姿态/尺寸与物理沉降 | `PARTIAL` | `simulate` | New independent scene per request; cube/sphere/prepared mesh, 1–256 bodies. No interactive editor. |
| `scene.random_layout` | 可复现的随机场景布局 | `PARTIAL` | `randomize` | Uniform independent body placement; collisions/clearance are not guaranteed. |
| `scene.export` | 保存当前机器人场景 | `PARTIAL` | `export_scene` | Exports the configured shared robot world only. |
| `scene.interactive_editing` | 层级编辑、复制、吸附、对齐、场景合并与交互加载 | `UNAVAILABLE` | — | No arbitrary stage editing/session API. |

#### 2. OpenUSD 场景组织

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `usd.stage_output` | 米制 Z-up USD 场景与变换输出 | `PARTIAL` | `simulate` | Generated simulation stages and poses only. |
| `usd.robot_export` | 机器人状态 USD 副本导出 | `PARTIAL` | `export_scene` | Copies the current CUDA poses into an exported stage. |
| `usd.composition` | 分层、引用、Payload、变体、实例与集合编辑 | `UNAVAILABLE` | — | USD supports these; the adapter has no composition RPC. |
| `usd.package` | 任意 USD 依赖收集与打包 | `UNAVAILABLE` | — | Robot import returns its own dependency ZIP; no general USD packaging workflow. |

#### 3. 模型与工程资产导入导出

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `assets.import_robot` | URDF/MJCF 导入与依赖打包 | `PARTIAL` | `import_robot` | Produces USD and ZIP; physics_check covers bundled fixtures, not arbitrary robot control. |
| `assets.convert_glb` | GLB / FastTrellis 网格与 PBR 转换 | `PARTIAL` | `convert_asset` | Embedded GLB only; vertex-baked linear base color, G roughness and B metallic; inspect warnings. |
| `assets.fbx_obj_gltf` | FBX、OBJ、外部 glTF 转 USD | `UNAVAILABLE` | — | Public conversion currently accepts self-contained GLB only. |
| `assets.cad_onshape_heightmap` | CAD、Onshape 与高度图导入 | `UNAVAILABLE` | — | Converters, credentials and terrain acceptance are not integrated. |
| `assets.urdf_export` | 关节机器人导出 URDF | `UNAVAILABLE` | — | No registered URDF export workflow. |
| `assets.transformer` | Asset Transformer、SimReady 验证 | `UNVERIFIED` | — | Validated import disables transformer postprocessing. |

#### 4. 材质、灯光与逼真渲染

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `rendering.cuda_path_tracing` | A100 CUDA 路径追踪、阴影、反射与全局照明 | `PARTIAL` | `simulate` | Mitsuba/Dr.Jit CUDA rendering via render settings; not native RTX. |
| `rendering.pbr` | PBR 颜色、金属度、粗糙度及贴图烘焙 | `PARTIAL` | `simulate` | Body material overrides and baked mesh channels; arbitrary MDL/texture networks unsupported. |
| `rendering.lighting` | 统一 Dome/矩形主光与补光 | `PARTIAL` | `simulate` | Constant dome, one key and up to eight fill rectangles; no HDR environment maps. |
| `rendering.preloaded` | 预加载网格/高斯视角渲染 | `PARTIAL` | `render_asset` | Operator-configured immutable asset, fixed image resolution and lighting. |
| `rendering.native_rtx` | 原生 RTX 实时/路径追踪与 MDL | `UNSUPPORTED` | — | A100 lacks supported RTX rendering hardware. |
| `rendering.optical_postprocessing` | 玻璃/透明、HDR、曝光、景深与后处理 | `UNAVAILABLE` | — | These material/optical controls are not implemented by this renderer adapter. |
| `rendering.video_viewports` | 多视口、视频编码与原生流媒体 | `UNAVAILABLE` | — | Offline image calls are available; viewport/video sessions are not integrated. |

#### 5. 刚体动力学

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `rigid_body.simulate` | CUDA 重力、质量、刚体运动与堆叠 | `PARTIAL` | `simulate` | Cube/sphere/convex-hull mesh, final pose/velocity output, bounded steps and dt. |
| `rigid_body.step` | 共享机器人世界固定步长推进 | `AVAILABLE` | `step` | Steps the existing world; does not create a new session. |
| `rigid_body.mass_properties` | 密度、质心、惯量与阻尼编辑 | `UNAVAILABLE` | — | Only mass is externally configurable; other properties use generated model defaults. |
| `rigid_body.forces` | 任意刚体初速度、外力与力矩 | `UNAVAILABLE` | — | No generic rigid-body force/velocity command; joint effort control is separate. |

#### 6. 碰撞与接触

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `collision.primitives` | 基础碰撞体与动态网格凸包 | `PARTIAL` | `simulate` | PhysX primitives/convex hull only; no collision approximation selection. |
| `collision.contact` | 接触检测与接触力读取 | `PARTIAL` | `snapshot` | Sensors attached to the configured robot world. |
| `collision.raycast` | 几何射线扫描 | `PARTIAL` | `scan` | Fixed 180-beam planar scan, no optical return model. |
| `collision.sdf_decomposition` | SDF、凸分解与任意静态三角网格 | `UNVERIFIED` | — | Requires an exposed cooking/configuration path and numerical acceptance. |
| `collision.properties_filters` | 摩擦/弹性、CCD、自碰撞与过滤配置 | `UNAVAILABLE` | — | Generated scenes use backend defaults; no per-body collision configuration RPC. |
| `collision.triggers_conveyor` | 触发器、重叠、运动学表面速度与传送带 | `UNAVAILABLE` | — | No public command route for these workflows. |

#### 7. 机器人关节与机构

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `joints.position` | 关节位置控制 | `PARTIAL` | `control` | Configured two-joint arms, up to 16; radians ±2.96. 固定参数：`mode=POSITION`。 |
| `joints.velocity` | 关节速度控制 | `PARTIAL` | `control` | Configured two-joint arms; rad/s ±5. 固定参数：`mode=VELOCITY`。 |
| `joints.effort` | 关节力矩控制 | `PARTIAL` | `control` | Configured two-joint arms; N·m ±20. 固定参数：`mode=EFFORT`。 |
| `joints.state` | 关节位置/速度/Effort 与状态读取 | `PARTIAL` | `snapshot` | Only joints instantiated in the configured world. |
| `joints.dynamics` | Jacobian、质量矩阵和关节作用力 | `PARTIAL` | `dynamics` | Batched tensors of the configured arms. |
| `joints.arbitrary_articulation` | 任意机器人实例、固定/浮动基座与关节限位编辑 | `UNAVAILABLE` | — | Import produces an artifact; it does not instantiate a controllable session. |
| `joints.mimic_closed_loop` | Mimic、闭环、腱及复杂约束 | `UNVERIFIED` | — | Backend/model-specific acceptance and control interfaces required. |

#### 8. 软体、可变形体、粒子与流体

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `deformables.cloth` | 独立 Newton XPBD 布料 | `PARTIAL` | `cloth` | No bidirectional coupling to PhysX or arbitrary cloth setup. |
| `deformables.volume_surface` | 体积/表面可变形体与附着 | `UNVERIFIED` | — | Not enabled or numerically accepted by this adapter. |
| `deformables.particles_fluids` | 粒子、采样、流体与发射器 | `UNVERIFIED` | — | No accepted CUDA particle/fluid workflow. |
| `deformables.gears_tendons` | 腱、齿轮和齿条机构 | `UNVERIFIED` | — | Requires model-specific backend validation. |

#### 9. Newton 实验性物理后端

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `newton.xpbd` | CUDA XPBD 刚体验收 | `PARTIAL` | `newton` | Fixed isolated demo; not arbitrary Newton model/session control. 固定参数：`solver=XPBD`。 |
| `newton.mujoco` | MuJoCo Warp / 原生 tensor 验收 | `PARTIAL` | `newton` | Uses driver API detection and locally built compatible kernels; fixed demo. 固定参数：`solver=MUJOCO_NATIVE`。 |
| `newton.cloth` | Warp XPBD 布料输出 | `PARTIAL` | `cloth` | Independent workflow with USD/NPZ outputs. |
| `newton.general_tensors` | 任意 Newton tensor、模型与可微分求解 | `UNVERIFIED` | — | Tensor checks cover the fixed demo only; general model API unverified. |

#### 10. 不同形态的机器人

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `robot_types.demo_arms` | 双关节机械臂演示与批量控制 | `PARTIAL` | `control` | One to sixteen configured demo arms; no catalog selection. |
| `robot_types.robot_assets` | 导入其他机器人定义为 USD | `PARTIAL` | `import_robot` | Import capability does not certify or control the imported model. |
| `robot_types.industrial_mobile_legged` | 工业臂、移动臂、AMR/叉车、四足与人形实例 | `UNAVAILABLE` | — | Robot assets, model configuration and live execution acceptance are required. |
| `robot_types.hands_suction` | 多指手、原生吸盘与其他末端执行器 | `UNVERIFIED` | — | Constraint-grasp demo is available; native Surface Gripper not accepted. |

#### 11. 机器人装配与调参

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `robot_setup.inspect_gains` | 关节检查与有效惯量 | `PARTIAL` | `inspect_gains` | Gain Tuner discovery/inertia core; no automatic gain optimization. |
| `robot_setup.export` | 当前机器人状态与 USD 检查产物 | `PARTIAL` | `export_scene` | Operator-configured world only. |
| `robot_setup.assembler` | Robot Assembler 与关节/质量/碰撞编辑 | `UNAVAILABLE` | — | No assembly or arbitrary model editing RPC. |
| `robot_setup.tuning_xrdf_grasps` | 自动增益优化、XRDF 和抓取姿态编辑 | `UNAVAILABLE` | — | Tools/configuration are not exposed or accepted. |

#### 12. 运动学、路径规划与轨迹生成

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `motion.trajectory` | 关节轨迹生成与实际执行 | `PARTIAL` | `trajectory` | Two-DOF configured arms only; no obstacle planner. |
| `motion.jacobian` | Jacobian 与动力学读取 | `PARTIAL` | `dynamics` | Access to tensors does not implement an IK solver. |
| `motion.fk_ik` | 通用 FK/IK 与末端位姿控制 | `UNVERIFIED` | — | Robot descriptions, solvers and numerical tests not integrated. |
| `motion.planners` | RMPflow、Lula、cuMotion、Pink 与避障规划 | `UNVERIFIED` | — | Dependency/model-specific planners have no accepted RPC execution route. |
| `motion.task_space` | 任务空间控制、轨迹优化与世界同步 | `UNAVAILABLE` | — | No general task-space/planner state interface. |

#### 13. 机械臂操作与抓取

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `grasping.constraint_grasp` | 约束吸附、抬升、释放与抓取循环 | `PARTIAL` | `gripper` | Custom PhysX fixed joint; not a native suction/contact seal model. |
| `grasping.native_gripper` | 原生 Surface Gripper 与多指夹爪 | `UNVERIFIED` | — | Extension load is not grasp acceptance. |
| `grasping.pick_place` | 任意物体拾放、堆叠、码垛与分拣任务 | `UNAVAILABLE` | — | Task setup, robot/asset controls and success metrics are not integrated. |
| `grasping.grasp_sampling` | 抓取候选采样、编辑、成功率评测 | `UNAVAILABLE` | — | No general grasp generation/evaluation RPC. |

#### 14. 移动机器人与导航

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `navigation.differential_drive` | 差速底盘到轮速转换 | `PARTIAL` | `differential_drive` | Returns wheel velocities only; no chassis is spawned or advanced. |
| `navigation.geometry_scan` | 障碍物平面距离扫描 | `PARTIAL` | `scan` | Existing demo scene; no navigation map/session. |
| `navigation.base_controllers` | 全向/Ackermann 动力学、里程计与航点跟随 | `UNAVAILABLE` | — | No mobile-robot dynamics or closed-loop controller RPC. |
| `navigation.maps_terrain` | 占据地图、高度图、地形与多机器人导航 | `UNAVAILABLE` | — | Map building/terrain workflows not integrated. |
| `navigation.nav2_slam` | Nav2、SLAM、定位与目标导航 | `UNVERIFIED` | — | External stacks and complete loop need separate acceptance. |

#### 15. RGB 相机、镜头与成像

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `camera.scene_camera` | 自定义场景 RGB 与环绕相机 | `PARTIAL` | `simulate` | Square 64–1024 image, orbit camera, size/spp/seed; fixed pinhole optics. |
| `camera.robot_capture` | 同步机器人实际位姿的 RGB 与标注 | `PARTIAL` | `capture` | Configured perspective camera; not an RTX Camera sensor. |
| `camera.preloaded` | 固定资产相机视角 | `PARTIAL` | `render_asset` | Azimuth/elevation/distance only; fixed resolution. |
| `camera.lens_calibration` | 内参编辑、鱼眼/畸变、光圈与相机标定 | `UNAVAILABLE` | — | Some output metadata exists; general lens control/calibration is not implemented. |
| `camera.batch_timing_isp` | 多相机同步、频率/偏移、多 tick、噪声与 ISP | `UNAVAILABLE` | — | Repeated capture is possible; no scheduled multicamera sensor API. |

#### 16. 深度与结构光相机

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `depth.robot_depth` | 机器人场景 Z-depth 与欧氏 range | `PARTIAL` | `capture` | Custom single-primary-ray arrays in meters, with valid mask. |
| `depth.scene_depth` | 自定义场景深度/距离标注 | `PARTIAL` | `simulate` | Set render.annotations=true; mesh mode only. |
| `depth.structured_light` | 投影图案、序列、标定与结构光恢复 | `UNAVAILABLE` | — | No projector, timing or reconstruction pipeline. |
| `depth.device_noise` | 视差、噪声、异常点与真实设备模型 | `UNAVAILABLE` | — | Ideal geometry depth only; no device signal/noise model. |

#### 17. 激光雷达、毫米波雷达和超声

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `range_sensors.geometry_scan` | 几何碰撞射线测距替代 | `PARTIAL` | `scan` | 180-beam planar ranges; no reflectance, timing or multipath model. |
| `range_sensors.rtx_lidar` | RTX LiDAR 扫描、强度、时序与完整帧 | `UNSUPPORTED` | — | Requires supported native RTX hardware. |
| `range_sensors.rtx_radar` | RTX Radar 传播、距离与径向速度 | `UNSUPPORTED` | — | Requires supported native RTX hardware. |
| `range_sensors.rtx_acoustic` | RTX Acoustic 超声通道与幅值信号 | `UNSUPPORTED` | — | Requires supported native RTX hardware. |
| `range_sensors.nonvisual_materials` | 非视觉材料响应配置 | `UNAVAILABLE` | — | No accepted nonvisual sensor/material route on this A100 adapter. |

#### 18. 接触、IMU 与状态传感器

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `physics_sensors.state` | IMU、接触、JointState 与 Effort | `PARTIAL` | `snapshot` | Official physics sensors on predefined bodies; not arbitrary sensor attachment. |
| `physics_sensors.scan` | Raycast 平面测距 | `PARTIAL` | `scan` | Fixed geometry, layout and 180 beams; miss values are null. |
| `physics_sensors.forces` | 关节动力学和作用力 | `PARTIAL` | `dynamics` | Configured robot tensors only. |
| `physics_sensors.sensor_configuration` | 传感器挂载、射线时间偏移与任意光束布局 | `UNAVAILABLE` | — | No public sensor configuration/session RPC. |
| `physics_sensors.tactile` | 触觉皮肤与 GelSight | `UNVERIFIED` | — | Assets and physics/rendering models need independent acceptance. |

#### 19. 合成数据与自动真值标注

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `synthetic_data.robot_annotations` | RGB、深度/range、法向、实例/语义 ID 与 mask | `PARTIAL` | `capture` | Custom Mitsuba annotation pipeline; not native Replicator. |
| `synthetic_data.scene_annotations` | 用户场景语义标签与自动真值 | `PARTIAL` | `simulate` | Body semantic_label and mesh primary rays; seed/pose metadata. |
| `synthetic_data.boxes_flow_points` | 运动向量、2D/3D 框与标注点云 | `UNAVAILABLE` | — | No registered output producer; depth arrays alone are not these annotations. |
| `synthetic_data.replicator` | Replicator Annotator、Writer、多相机与事件采集 | `UNAVAILABLE` | — | Native render-product writer path is not integrated on this adapter. |

#### 20. 域随机化与数据增强

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `randomization.scene` | 位置、颜色、质量与金属度/粗糙度随机化 | `PARTIAL` | `randomize` | Seeded uniform sampling of explicit bodies; deterministic decoded scene; no clearance checks. |
| `randomization.lighting_camera` | 灯光和相机的显式参数变化 | `PARTIAL` | `simulate` | Caller supplies settings per request; no automatic distribution scheduler. |
| `randomization.physics_textures` | 摩擦、纹理分布、数量/遮挡约束与触发器 | `UNAVAILABLE` | — | These randomization channels/constraints are not integrated. |
| `randomization.infinigen_cosmos` | Infinigen 与 Cosmos 环境/数据增强 | `UNAVAILABLE` | — | External software/models and service workflow required. |

#### 21. 专用抓取与移动数据生成

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `specialized_sdg.grasp_demo` | 抓取循环状态结果 | `PARTIAL` | `gripper` | Demo result only; not Grasping SDG dataset generation. |
| `specialized_sdg.grasping_sdg` | Grasping SDG 候选、阶段与数据集 | `UNAVAILABLE` | — | No configured generator/writer API. |
| `specialized_sdg.mobilitygen` | MobilityGen 轨迹录制、回放与重渲染 | `UNAVAILABLE` | — | No mobile scene/trajectory workflow integrated. |

#### 22. 人物、动画与角色行为

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `characters.animation` | 多人物、骨骼动画、重定向与动画图 | `UNAVAILABLE` | — | No accepted character/animation RPC workflow. |
| `characters.behavior_navigation` | 导航网格、行为树、巡逻与碰撞反应 | `UNAVAILABLE` | — | No runtime character behavior/session interface. |
| `characters.physical_humans` | 人体受力、平衡与动作回放 | `UNVERIFIED` | — | Requires separate character physics and model-specific acceptance. |

#### 23. 事故、事件与长尾数据

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `events.rigid_events` | 刚体堆叠、掉落与倾倒场景 | `PARTIAL` | `simulate` | General rigid-body initial conditions only; no semantic incident generator. |
| `events.incident_generator` | 堆箱、货架、空闲空间、YAML 事件与报告 | `UNAVAILABLE` | — | No incident generation/configuration/reporting route. |
| `events.smoke_fire` | 烟火、洒落与角色响应 | `UNAVAILABLE` | — | No accepted fluid/volumetric effects or event behavior pipeline. |

#### 24. 场景图、文字描述与自然语言辅助

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `language.scene_metadata` | 物体位姿、ID 与语义元数据 | `PARTIAL` | `simulate` | Structured numeric output only; no relation graph or language generation. |
| `language.graphs_captioning` | 空间关系图、描述与 VLM Captioning | `UNAVAILABLE` | — | Requires an integrated scene-graph/model service. |
| `language.natural_language` | 自然语言行为树与 Chat IRO YAML | `UNAVAILABLE` | — | No model endpoint/behavior runtime integrated. |
| `language.camera_placement` | 覆盖率、遮挡、兴趣点与相机自动布置 | `UNAVAILABLE` | — | Orbit camera parameters exist; no placement optimizer/calibration workflow. |

#### 25. 录制、回放、示教与 XR/VR

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `recording_xr.state_export` | 单次仿真状态 NPZ 与 USD 输出 | `PARTIAL` | `simulate` | Final state only; not a temporal episode recorder. |
| `recording_xr.trajectory_trace` | 演示关节轨迹的采样记录 | `PARTIAL` | `trajectory` | Sampled joint positions only; not an HDF5 episode. |
| `recording_xr.episodes_replay` | HDF5 Episode Recorder、示教、回放与离线重渲染 | `UNAVAILABLE` | — | No episode lifecycle/restore/replay RPC. |
| `recording_xr.xr_teleop` | OpenXR、VR 遥操作、Isaac Teleop 与 CloudXR | `UNAVAILABLE` | — | No device/service pipeline; XR entrypoints alone do not provide teleoperation. |

#### 26. ROS 2 与机器人软件生态

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `ros2.status` | 自定义 Jazzy 数据桥状态与计数 | `PARTIAL` | `ros2_status` | Optional bridge; DSH uses gRPC and need not depend on ROS. |
| `ros2.joint_control` | 通过 gRPC 控制同一机器人世界 | `PARTIAL` | `control` | ROS command subscription is optional; two-DOF demo limits apply. |
| `ros2.sensor_data` | 通过 gRPC 获取 JointState/IMU/扫描等桥接源数据 | `PARTIAL` | `snapshot` | Existing data bridge publishes five topic types; no arbitrary ROS graph proxy. |
| `ros2.image_cloud_radar` | ROS 图像、压缩图、深度、点云与雷达发布 | `UNAVAILABLE` | — | Not implemented by the custom physics-only bridge. |
| `ros2.qos_services_launch` | QoS、命名空间、自定义消息、服务与 ROS Launch 配置 | `UNAVAILABLE` | — | Bridge configuration is operator-owned; no runtime ROS administration RPC. |
| `ros2.full_bridge` | 原生 OmniGraph ROS Bridge、Nav2/MoveIt/ros2_control | `UNVERIFIED` | — | Complete external loops require separate acceptance. |

#### 27. 强化学习、模仿学习与策略部署

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `learning.rl_step` | 批量 observation/reward step | `PARTIAL` | `rl_step` | Two-joint demo observations/rewards; terminated is always false; no Gym reset/truncation. |
| `learning.clone_state` | 克隆机械臂世界的批量状态 | `PARTIAL` | `snapshot` | Operator chooses 1–16 arms at worker startup; no runtime environment creation. |
| `learning.reset_training` | 环境 reset、完整训练、任务与奖励配置 | `UNAVAILABLE` | — | No Gymnasium/Isaac Lab environment lifecycle or training API. |
| `learning.policy_imitation` | 策略推理、回放、模仿学习与 Torch 接口 | `UNAVAILABLE` | — | Training/policy dependencies and robot tasks not integrated. |
| `learning.differentiation` | 可微分学习工作流 | `UNVERIFIED` | — | Requires separate solver/gradient acceptance. |

#### 28. 数字孪生、仓储与物流

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `digital_twin.layout` | 多资产布局与场景渲染 | `PARTIAL` | `simulate` | Prepared meshes and primitives; application semantics belong to the caller. |
| `digital_twin.random_layout` | 随机布局与沉降 | `PARTIAL` | `randomize` | No warehouse constraint solver or logistics optimization. |
| `digital_twin.conveyor_forklift` | 传送带、叉车、移动机器人与物流动力学 | `UNAVAILABLE` | — | No accepted general logistics model/controller sessions. |
| `digital_twin.enterprise` | cuOpt、MES、WMS、PLC 与业务调度 | `UNAVAILABLE` | — | External systems have no simulator-side connector in this adapter. |

#### 29. 真实场景重建、3DGS、内容管理与协作

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `reconstruction.gaussian` | CUDA 3DGS 与网格混合渲染 | `PARTIAL` | `simulate` | Prepared Gaussian PLY plus proxy mesh; radiance is baked, not relightable PBR. |
| `reconstruction.preloaded` | 预加载 Gaussian 资产视角 | `PARTIAL` | `render_asset` | Requires object.ply in the operator-configured asset directory. 固定参数：`kind=GAUSSIAN_RENDER`。 |
| `reconstruction.training_nurec` | 3DGS 重建训练、NuRec 转换与工具 | `UNAVAILABLE` | — | No accepted training/NuRec conversion RPC. |
| `reconstruction.content_collaboration` | SimReady 检索、内容库、Nucleus 与 Live Session | `UNAVAILABLE` | — | No content-service credentials or collaboration workflow integrated. |
| `reconstruction.gaussian_physics` | 从 3DGS 自动生成碰撞和交互语义 | `UNAVAILABLE` | — | Gaussian visual radiance does not define reliable physics or object semantics. |

#### 30. 编程、自动化、部署与调试

| `feature_id` | 子功能 | 实现状态 | `SubmitTask` 字段 | 范围、限制与拒绝原因 |
| --- | --- | --- | --- | --- |
| `development.fixed_step` | 固定步长与手动推进 | `PARTIAL` | `step` | One shared robot world; persistent operations serialize. |
| `development.batch` | 独立场景批处理 | `PARTIAL` | `simulate` | Resident path handles eligible cubes; other scenes route to generic CUDA worker. |
| `development.diagnostics` | 状态、物理传感器与动力学诊断 | `PARTIAL` | `snapshot` | Public gRPC health/catalog/jobs and benchmark tools; process administration is local. |
| `development.arbitrary_code` | 远程执行 Python/C++、OmniGraph 或任意脚本 | `UNSUPPORTED` | — | Deliberately not exposed; operators may register trusted named operations. |
| `development.gui_streaming` | GUI、WebRTC、RTSP 与远程桌面 | `UNAVAILABLE` | — | No accepted native viewport/camera streaming path. |
| `development.multi_gpu` | 多 GPU 调度、Docker/云部署编排与 Tracy 会话 | `UNAVAILABLE` | — | Portable single-A100 Bash/Conda deployment; no multi-GPU orchestration RPC. |

<!-- END GRPC FEATURE AUDIT -->

</details>

功能实现的主要源码位置：场景/刚体为 [physics_service.py](a100/a100sim/physics_service.py)，USD/PBR 为 [materials.py](a100/a100sim/materials.py) 与 [usd_renderer.py](a100/a100sim/usd_renderer.py)，机器人/物理传感器为 [robotics.py](a100/a100sim/robotics.py)，抓取为 [grasping.py](a100/a100sim/grasping.py)，自定义真值为 [annotations.py](a100/a100sim/annotations.py)，Newton 为 [newton_cloth.py](a100/a100sim/newton_cloth.py) 与固定验收工作流，目录映射为 [catalog.py](a100/a100sim/rpc/catalog.py)。

官方参考：[参考架构](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/introduction/reference_architecture.html)、[Newton](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/newton_physics.html)、[物理限制](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/physics_resources.html)、[传感器](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/learn/sensors_and_physics.html)、[ROS 2](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/ros2_tutorials/index.html)、[数据生成](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/synthetic_data_generation/index.html)。6.2 已移除 Cortex；新实现应按对应物理后端验证 Experimental Core API。

## gRPC 接口文档

接口包名为 **`a100sim.v1`**，完整服务名为 **`a100sim.v1.Simulator`**，唯一契约为 [simulator.proto](a100/a100sim/rpc/v1/simulator.proto)。DSH 等调用方只需要 gRPC/protobuf 和生成的客户端文件，无需安装 Isaac、CUDA、Mitsuba、ROS，也无需挂载服务端目录。

```text
调用方 → gRPC → 校验、去重、持久化、有界任务队列
                ├─ CUDA PhysX 独立场景 / resident 批处理
                ├─ 共享机器人世界、物理传感器、CUDA 捕获
                ├─ 预加载 mesh / Gaussian 渲染
                └─ 独立 GLB、URDF/MJCF、Newton 工作流
```

适配内部 HTTP 仅作本机工作进程通信，所有监听器与内部 URL 均强制 loopback。`--host 0.0.0.0` 会被拒绝。远程调用、资产上传、结果订阅及文件下载统一使用 gRPC；后文保留的 `curl`/HTTP 示例仅供本机排障。

内部工作进程通过健康信息的 `job_long_poll` 声明等待式查询能力；兼容旧版通用工作进程时使用有间隔的只读查询。已接受的任务不会因协议兼容处理被重新提交。resident 原有等待协议保持兼容；PBR、语义等新字段仍要求更新对应工作进程。

### 启动、停止和认证

完成前面的环境准备后，一键部署默认同时安装并启动 gRPC：

```bash
cd IssacSim4A100/a100
bash deploy.sh
bash deploy.sh status
bash deploy.sh stop
```

已有运行环境时 `bash deploy.sh start` 启动已安装的全部服务。只单独运行网关时：

```bash
bash serve-grpc.sh install
bash serve-grpc.sh
```

网关默认监听 `127.0.0.1:50051`，Python 环境默认 `$A100SIM_ROOT/.venv-grpc`，与 Kit 包隔离。前台退出可按 `Ctrl+C`；单独运行的网关不会管理 GPU 进程生命周期。一键部署通过 Bash 后台进程管理，无需 Slurm 或 systemd。

跨主机监听需在 `.env` 配置部署者自己的地址、证书、私钥和凭证：

```bash
A100SIM_GRPC_LISTEN=0.0.0.0:50051
A100SIM_GRPC_TLS_CERT=/absolute/path/server.pem
A100SIM_GRPC_TLS_KEY=/absolute/path/server-key.pem
A100SIM_GRPC_TOKEN=<your-generated-secret>
# 私有 CA 与通配监听时，本机部署检查需要能验证该证书的地址。
A100SIM_GRPC_TLS_CA=/absolute/path/ca.pem
A100SIM_GRPC_PROBE_TARGET=simulator.example.org:50051
```

非 loopback 监听缺少 TLS 或 token 时拒绝启动。调用方校验证书，并为每个 RPC 传 metadata `authorization: Bearer <token>`；健康检查和 reflection 使用同一认证。令牌通过部署环境注入，不写入源码/提交记录。示例域名与路径应替换为实际部署值。

| 配置 | 默认值 / 行为 |
| --- | --- |
| `A100SIM_GRPC_CAPACITY` | 网关待分发队列 64；允许 1–4096 |
| `A100SIM_GRPC_WORKERS` | 分发上限 32；允许 1–128；共享机器人任务仍串行 |
| `A100SIM_GRPC_ENV` | 独立网关环境目录；空值使用 `$A100SIM_ROOT/.venv-grpc` |
| `A100SIM_GRPC_WORKFLOWS` | `1` 启用已准备的 GLB/URDF/MJCF/Newton 子进程工作流 |
| `A100SIM_ASSET_PYTHON` | GLB 转换 Python；空值使用代码目录的 `.venv-assets/bin/python` |
| `A100SIM_ASSET_ROOT` | 服务端资产目录；调用方只持有 `asset_id` |
| `A100SIM_GRPC_*_URL` | 可覆盖本机 resident/generic/features/render 内部端口；必须为 loopback origin |

### 服务方法与消息

所有普通方法均可经 reflection 查询。没有额外 REST、WebSocket 或客户端直连工作进程要求。

| RPC | 输入 → 输出 | 流类型 | 行为 |
| --- | --- | --- | --- |
| `GetCapabilities` | `Empty → Capabilities` | unary | 返回 API/仿真器版本、任务可用性、队列容量和运行数；后端健康结果最多缓存 1 秒 |
| `GetApiCatalog` | `Empty → ApiCatalog` | unary | 30 类、138 子功能的支持状态、实时就绪性、参数 schema、任务映射、限制和证据 |
| `InvokeFeature` | `FeatureRequest → Job` | unary | 根据 `feature_id` 调用实际任务；和对应 `SubmitTask` 共用校验、去重、队列及结果 |
| `SubmitTask` | `SubmitTaskRequest → Job` | unary | 强类型 `oneof task`；仅接受并返回任务身份，不等待仿真结束 |
| `GetJob` | `JobRequest → Job` | unary | 查询仍保留的任务快照 |
| `WatchJob` | `WatchJobRequest → Job` | 服务端流 | 按 revision 推送更新，终态后结束；断流只结束观察 |
| `CancelJob` | `JobRequest → Job` | unary | 仅取消仍在网关排队的任务；运行中返回 `FAILED_PRECONDITION` |
| `RunTasks` | `SubmitTaskRequest → Job` | 双向流 | 同一流按输入顺序执行，每次返回终态再接收下一任务；每条请求仍需独立 `request_id` |
| `ListAssets` | `Empty → AssetList` | unary | 返回 `asset_id` 与相对文件名列表 |
| `UploadAsset` | `UploadChunk → AssetFile` | 客户端流 | 每条流上传一个不可覆盖文件；校验可选 SHA-256 |
| `DownloadArtifact` | `DownloadRequest → DownloadChunk` | 服务端流 | 仅下载指定 job 的产物；支持 offset 断点续传 |

`GetCapabilities` 的 `native_rtx` 固定为 false。`capabilities[].name` 是任务或已注册插件名，`availability/reason/scope/details` 分别解释就绪性、原因、范围与运行配置。`GetApiCatalog` 的 schema 是 protobuf 字段结构；数值范围与跨字段限制以本文和实际参数校验为准。

| `Feature` 字段 | 说明 |
| --- | --- |
| `id / title` | 稳定的 `feature_id` 与可读功能名称 |
| `support / availability` | 实现范围与当前可用状态；不能只看其中一个 |
| `reason` | 限定范围、尚缺内容或硬件限制 |
| `task_field / request_type` | 对应 `SubmitTask` oneof 字段与 protobuf 参数类型；未实现时为空 |
| `request_schema` | 以 snake_case 字段名描述的 protobuf-JSON 参数结构；拒绝未知字段 |
| `fixed_arguments` | 功能入口固定的参数，例如 `joints.velocity` 固定 `mode=VELOCITY`；调用方冲突值被拒绝 |
| `evidence` | 历史 A100 数值验收或当前实现位置；不是新版本部署实测证明 |

`InvokeFeature` 输入为 `request_id`、`feature_id` 和 `arguments` 对象。`arguments` 是目录中该任务消息的字段，不能再嵌套 `simulate/control`，也不能放服务器路径或 Python 代码。枚举使用协议中的符号，例如 `VELOCITY`。空消息任务传 `{}` 即可。

### 任务参数：20 类强类型操作

`SubmitTaskRequest.request_id` 为 1–128 字符。一次只设置一个 task 字段；`extension` 是另一个受信任插件入口，不计入 20 类内置任务。以下范围是执行边界，省略的 optional 标量使用默认值，显式的 0/false 不会被当成缺省值。

| task 字段 | 参数类型与默认值 | 返回的主要内容 / 限定 |
| --- | --- | --- |
| `simulate` | `SimulateRequest`，详见下一表 | 新场景最终刚体状态、USD、NPZ、可选 RGB/标注；每个任务独立 |
| `randomize` | `RandomizeRequest`，显式 `scene`、`seed=42`、各通道范围 | 先可复现采样再走同一 CUDA 仿真；返回 `randomization.seed/initial_bodies` |
| `render_asset` | `RenderSettings` 的 `kind=MESH_RENDER, spp=8`（1–256）、`seed=42`（0–2147483647）、`azimuth=0`（±36000°）、`elevation=12`（±80°）、`distance=2.15`（0.2–20 米） | 仅预加载资产；分辨率/布光固定，拒绝其他 render 字段及越界值；Gaussian 需已加载对应资产 |
| `snapshot` | `Empty` | `snapshot` 中关节位置/速度、IMU、接触、effort 等配置传感器数据 |
| `step` | `StepRequest.steps=1`，1–1200 | 推进共享世界并返回 snapshot |
| `control` | `ControlRequest`，默认 `POSITION, arm_index=0, steps=120` | 应用目标并步进，返回 snapshot；目标持续有效直到被覆盖 |
| `dynamics` | `Empty` | `dynamics` 中 Jacobian、广义质量矩阵、actuation/projected forces |
| `scan` | `Empty` | 固定 180 束平面几何距离扫描和 snapshot；未命中为 null，不是 RTX LiDAR |
| `capture` | `CaptureRequest.size=512`（64–1024），`spp=32`（1–256） | 当前机器人 CUDA 位姿的 RGB、深度/range、法向、实例/语义与 mask |
| `gripper` | `GripperRequest.operation=status`；可选 `status/close/lift/open/cycle` | 自定义 fixed-joint 吸附与状态；不是原生 Surface Gripper 验收 |
| `trajectory` | `TrajectoryRequest.points` 2–128 个二关节位置；`arm_index=0`；`max_velocity=1.0`（0.05–3），`max_acceleration=2.0`（0.1–10） | 官方轨迹生成并实际执行，返回 duration、采样位置与 waypoints；仿真时长最多 60 秒 |
| `differential_drive` | `linear_velocity=0.5, angular_velocity=0.5`（各 ±5）；`wheel_radius=0.1`（0.01–2），`wheel_base=0.4`（0.01–5） | 仅返回左右轮 rad/s；不会创建或移动底盘 |
| `rl_step` | `ControlRequest`，默认步数 1，其他同 control | observation `[q1,q2,v1,v2]`、reward `-sum(q²)`，terminated 恒 false；没有 reset/truncated/训练 |
| `cloth` | `ClothRequest.frames=90`，1–300 | 独立 CUDA Newton XPBD 布料，USD/NPZ 与约束验收；不和 PhysX 双向耦合 |
| `import_robot` | `ImportRobotRequest.asset_id/relative_path`；可选 `physics_check=false` | URDF 或 XML/MJCF → USD、报告、依赖 ZIP；运动检查仅适用于提供的 fixtures |
| `newton` | `NewtonRequest.solver=XPBD` 或 `MUJOCO_NATIVE`；native 可设 `steps` 120–1200、`iterations` 1–100、`line_search_iterations` 1–50 | 固定、隔离的 CUDA 数值验收工作流，不是任意模型交互式控制；XPBD 不接受这些 native 调参 |
| `convert_asset` | `ConvertAssetRequest.asset_id/relative_path, source_up=y, max_size=1.2`（0–100 内），`strict_materials=false` | 自包含 GLB → 新 `asset_id`、mesh.npz、材质通道/限制报告；只接受 y/z 上轴 |
| `export_scene` | `Empty` | 当前共享机器人世界的 USD 快照产物 |
| `inspect_gains` | `Empty` | 关节发现与有效惯量；没有自动增益优化或 GUI |
| `ros2_status` | `Empty` | 可选自定义 ROS 2 桥状态、domain 与发布/命令计数；不是任意 ROS graph 代理 |

`ControlRequest.targets` 为 `repeated JointTargets`，每行 `values` 恰好两个值。一行控制 `arm_index`；恰好 N 行控制全部 N 个配置机械臂。位置 ±2.96 rad、速度 ±5 rad/s、力矩 ±20 N·m。只接受当前演示世界中的二关节机械臂，不会因导入 URDF 而扩展可控关节数。

`TrajectoryRequest.points` 每项同样是 `{"values":[q1,q2]}`；关节位置限幅同 control。共享世界中的 control、step、capture、trajectory 等由拥有 Kit 的线程串行执行。多个调用方共用该世界，没有多租户隔离。开启可选 ROS 桥后，世界空闲时也会按配置持续推进。

### 场景、PBR、相机和灯光字段

| 消息/字段 | 默认值、单位与范围 |
| --- | --- |
| `SimulateRequest.bodies` | 1–256 个 `Body`；省略时为 64 个演示方块；显式空列表在 protobuf 中等同省略 |
| `steps / dt / gravity / ground` | 240 步（1–1200）；1/120 秒（0.0001–0.02）；9.81 m/s²（0–100）；true |
| `route` | `AUTO` 优先 resident 快速路径，否则转 generic；`GENERIC` 强制独立场景后端 |
| `Body.id / shape` | 唯一非空 ID，省略时采用索引；`CUBE/SPHERE/MESH`，默认 CUBE |
| `position / orientation_xyzw` | 默认 `[0,0,2]` 米（各 ±10000）；默认 `[0,0,0,1]`，必须非零，输入分量 ±1 并自动归一化 |
| `size / radius / mass` | 方块 `[0.2,0.2,0.2]` 米（每边 0.001–100）；球 0.1 米（0.001–100）；质量 1 kg（0.0001–100000） |
| `asset_id` | 仅 MESH 使用；资产内需 `mesh.npz`，动态碰撞为凸包；普通可视网格不自动获得 articulation |
| `color` | primitive 默认线性 RGB `[0.35,0.5,0.7]`，各 0–1；mesh 采用其顶点颜色 |
| `material` | `PbrMaterial.base_color` 三个线性 RGB 值、optional `metallic/roughness`，均 0–1；只覆盖明确提供的通道 |
| `semantic_label` | 可选 1–128 字符；写入 USD prim，供实例/语义标注读取；未标注为 semantic ID 0 |
| `RenderSettings.kind` | `MESH_RENDER` 或 `GAUSSIAN_RENDER`；Gaussian 场景需匹配 proxy mesh 与 `object.ply` |
| `size / spp / seed` | 256 正方形像素（64–1024）；8 spp（1–256）；42（0–2147483647） |
| `azimuth / elevation / distance` | 默认 35°（±36000）、20°（±80）、5 米（0.1–1000） |
| `target / gaussian_gain` | 默认 `[0,0,0.5]` 米；高斯辐射倍率默认 1（0–10）；改变倍率不等于 PBR 重打光 |
| `annotations` | 默认 false；true 输出 RGB 之外的理想几何真值；仅 MESH_RENDER 可用 |
| `lighting` | `Lighting.dome/key/fills`；最多 8 个矩形补光，省略时使用示例默认打光 |
| `Light.color` | 三通道色值，各 0–10；光源强度与曝光独立于表面 PBR |
| `Light.intensity/exposure/width/height` | 有界数值 0–10000；width/height 为米；dome 为常量环境光，key/fills 为矩形光 |
| `Light.origin/target` | 三维位置，米；用于矩形光朝向；无 HDR 环境贴图和任意灯光类型适配 |

PBR 网格 NPZ 新增可选 `metallic` 和 `roughness`，每个顶点一个 float32、范围 0–1；`colors_linear` 为单个布尔值，true 表示 `colors` 已在线性空间，false 或省略时从 sRGB 解码。未携带通道的旧资产继续使用 metallic=0、roughness=0.85。`material.base_color` 替换整个物体的烘焙颜色，未提供的通道保持源资产值。GPU 快速批处理不能表示这些材质/语义/标注时，会转通用 CUDA 后端，不能静默丢弃。

GLB PBR 转换按 glTF metallic-roughness 约定：base-color 纹理先从 sRGB 解码到线性，再双线性采样并乘 baseColorFactor；roughness 为贴图 G 通道乘 roughnessFactor，metallic 为 B 通道乘 metallicFactor，后两者不做 sRGB 解码。结果在原 UV 顶点处烘焙，三角形内部插值，因此细纹理精度受原网格密度限制。法线、AO、发光、透明/遮罩、扩展材质、非 repeat wrap 和 UV 变换不完整支持，报告 `material_warnings` 会列出；`strict_materials=true` 时拒绝带这些未支持输入的资产。自包含 GLB 可上传；外部资源 URI 被拒绝。

USD 使用标准 `UsdPreviewSurface` 及顶点 primvar reader 传递上述通道；任意 MDL 和其他纹理网络不会自动转换。3DGS 保存的是烘焙辐射，不能保证与 PBR 网格一样响应新灯光；高斯外观也不提供可靠碰撞或语义。

### 随机化与真值输出

`RandomizeRequest.scene` 必须显式包含 1–256 个 bodies。`seed` 默认 42，显式 0 有效。所有启用通道独立均匀采样：

| 参数 | 行为 |
| --- | --- |
| `position_min / position_max` | 都提供且均为三维向量，每轴下界 ≤ 上界，各 ±10000 米 |
| `randomize_color` | true 时采样线性 RGB，并写入 `material.base_color`；mesh 同样有效 |
| `metallic_range / roughness_range` | 可选 `[min,max]`，0–1；逐物体采样对应 PBR 通道 |
| `mass_range` | 可选 `[min,max]` kg，0.0001–100000 |

相同输入与 seed 产生相同初始采样；物理仿真仍受后端数值特性影响。该接口不保证无碰撞布置、不采样任意分布、不自动更改物体数量或构建导航地图。结果 `randomization.initial_bodies` 便于复核初始条件。

`capture` 和 `simulate.render.annotations=true` 的 NPZ 包含 `depth_m`（相机前向 Z）、`range_m`（欧氏距离）、`normal_world`、`instance_id`、`semantic_id`、`valid`。未命中数值为 0，必须结合 mask。使用每像素中心单射线，无运动模糊；实例/类别映射在 labels JSON。它们是自定义 Mitsuba CUDA 几何真值，不包含结构光、镜头噪声、雷达回波或 Replicator writer 语义。

### 资产与产物传输

`UploadAsset` 第一块填写 `relative_path`，可填写现有 `asset_id` 和全文件 `sha256`；后续块省略元数据或重复相同值。每块 ≤1 MiB、每文件默认 ≤512 MiB。未指定 ID 时服务端生成；多个依赖文件可用相同 ID 分别上传。文件原子发布、不可覆盖；失败不会发布半个文件。路径使用相对 POSIX 路径，拒绝 `..`、绝对路径与符号链接越界。

上传 GLB 后提交 `convert_asset`，取得转换返回的新 `asset_id`，再放入 `Body.asset_id`。上传 URDF/MJCF 与依赖后提交 `import_robot`，输出是 USD/依赖 ZIP，不会自动加载进常驻控制世界。所需转换 Python 和 Linux 运行环境由服务端准备。

`Job.artifacts` 提供 `artifact_id/name/size_bytes/media_type`，`result` 中产物引用采用 `artifact://<id>`。下载必须提供对应 `job_id + artifact_id`；不能用它读取其他任务或任意文件。`offset` 默认 0，可续传；`chunk_bytes` 默认 256 KiB、最大 1 MiB。每个返回块有绝对文件 offset。调用方检查连续 offset 和最终长度；产物保留期也受内部工作进程策略约束，过期会返回错误。

### 任务状态、幂等与错误

`Job` 包含 `job_id/request_id/operation/state/revision/result/failure/artifacts/timings/created_at_unix`。任务状态为：

```text
QUEUED → RUNNING → SUCCEEDED / FAILED
   └→ CANCELLED
重启或停止时尚未完成 → INTERRUPTED
```

同一逻辑操作必须复用同一个 `request_id`。相同 ID 和相同 canonical task 返回原任务；修改 task 返回 `ALREADY_EXISTS`。`InvokeFeature` 与对应强类型 `SubmitTask` 使用同一去重记录。若提交超时，应以原 ID 重试或查询原 job；更换 ID 可能重复推进仿真。去重记录写入 SQLite WAL、`synchronous=FULL`；最多保留 4096 个任务，淘汰后旧 ID 不再保证去重。重启标记 `INTERRUPTED`，不会自动重放，也不承诺分布式 exactly-once。

`WatchJob.after_revision` 可从已知版本继续观察，服务端只发送更高 revision；没有更新时等待，已观察终态时流结束。客户端 deadline、断连、取消订阅均不撤销已接受任务。运行中不能安全取消；停止服务不回滚已经发生的物理动作。需要严格连续的控制顺序时，使用一个 `RunTasks` 流并由调用方管理共享世界的使用权。

| 错误码 | 含义与处理 |
| --- | --- |
| `INVALID_ARGUMENT` | 字段、类型、枚举、非有限数、范围或固定参数冲突；修正后作为新逻辑操作提交 |
| `UNAUTHENTICATED` | 缺失或无效 bearer credential |
| `NOT_FOUND` | 功能 ID、任务、资产或产物不存在/已过期 |
| `ALREADY_EXISTS` | 相同 request_id 对应不同任务，或上传试图覆盖已有文件 |
| `RESOURCE_EXHAUSTED` | 有界队列/保留容量/上传额度不足；未接受的请求可退避重试 |
| `UNAVAILABLE` | 必需工作进程、依赖或 ROS 桥未就绪；先查能力与部署日志 |
| `UNIMPLEMENTED` | 已知功能尚未接入/未验收/硬件不支持，或未注册插件；不会创建假成功任务 |
| `FAILED_PRECONDITION` | 试图取消已经运行的工作等不满足前置条件的操作 |
| `DEADLINE_EXCEEDED` | RPC 等待或后端执行超时；不等于物理操作已取消 |
| `INTERNAL / PERMISSION_DENIED / ABORTED` | 执行失败、产物越界或任务中断；通过 job 与服务端日志判断 |

接收入队前的错误由 RPC status 返回。入队后的执行错误放在终态 `Job.failure.code/message`，此时 `WatchJob` 可以正常结束但 job 为 FAILED。必须同时检查 RPC 和 job 状态。`Struct` 使用 double 数值语义；文件大小、revision、时间戳等专用协议字段按定义读取，不能把大整数塞入 Struct 来保证精确性。

`Timings` 单位均为秒：`gateway_queue_seconds` 为网关等待，`submit_seconds` 为内部接收开销，`backend_wait_seconds` 包含后端排队/执行/观察，`upstream_queue_seconds/upstream_execution_seconds` 为工作进程报告，`total_seconds` 为网关入队到完成。各字段可能重叠，不能简单相加；未提供的字段为 0。

### Python / grpcurl 调用示例

已提交 Python bindings；其他语言根据同一个 proto 生成 SDK。网关依赖只安装到独立环境；客户端最小依赖为 grpcio、protobuf 和 `a100sim/rpc/v1`。重新生成绑定：

```bash
.venv-grpc/bin/python -m pip install grpcio-tools==1.85.1
.venv-grpc/bin/python -m grpc_tools.protoc -I . \
  --python_out=. --grpc_python_out=. --pyi_out=. a100sim/rpc/v1/simulator.proto
```

在 `a100/` 目录用 [grpc_client.py](a100/examples/grpc_client.py) 查询、上传和下载；远程 TLS 连接在命令的子命令前添加 `--target simulator.example.org:50051 --ca /path/to/ca.pem`：

```bash
.venv-grpc/bin/python examples/grpc_client.py catalog
.venv-grpc/bin/python examples/grpc_client.py capabilities
.venv-grpc/bin/python examples/grpc_client.py upload /path/to/asset.glb
```

下面是查询能力、提交 PBR 场景、订阅结果和下载图片的完整强类型示例：

```python
import asyncio
import os
import uuid
from pathlib import Path

import grpc
from google.protobuf.empty_pb2 import Empty
from a100sim.rpc.v1 import simulator_pb2 as pb
from a100sim.rpc.v1 import simulator_pb2_grpc as rpc

async def main():
    token = os.environ.get("A100SIM_GRPC_TOKEN")
    metadata = (("authorization", "Bearer " + token),) if token else ()
    async with grpc.aio.insecure_channel("127.0.0.1:50051") as channel:
        client = rpc.SimulatorStub(channel)
        catalog = await client.GetApiCatalog(Empty(), metadata=metadata, timeout=10)
        feature = next(f for d in catalog.domains for f in d.features if f.id == "rendering.pbr")
        if feature.availability not in (pb.AVAILABLE, pb.PARTIAL):
            raise RuntimeError(feature.reason)
        request_id = uuid.uuid4().hex  # 保存这个 ID，超时重试必须复用。
        print("request_id:", request_id, flush=True)
        accepted = await client.SubmitTask(pb.SubmitTaskRequest(
            request_id=request_id,
            simulate=pb.SimulateRequest(
                bodies=[pb.Body(id="metal-box", position=[0, 0, 1], size=[0.3]*3,
                                material=pb.PbrMaterial(base_color=[0.6, 0.25, 0.08], metallic=0.8, roughness=0.25),
                                semantic_label="box")],
                steps=240, render=pb.RenderSettings(size=512, spp=32, annotations=True))),
            metadata=metadata, timeout=10)
        final = accepted
        async for update in client.WatchJob(pb.WatchJobRequest(job_id=accepted.job_id), metadata=metadata, timeout=600):
            final = update
        if final.state != pb.SUCCEEDED:
            raise RuntimeError(f"{final.failure.code}: {final.failure.message}")
        for artifact in final.artifacts:
            if artifact.name == "render.png":
                with Path("render.png").open("wb") as output:
                    async for chunk in client.DownloadArtifact(
                        pb.DownloadRequest(job_id=final.job_id, artifact_id=artifact.artifact_id),
                        metadata=metadata, timeout=120):
                        output.write(chunk.data)

asyncio.run(main())
```

目录调用的等价示例：

```python
job = await client.InvokeFeature(pb.FeatureRequest(
    request_id="persist-your-own-logical-operation-id",
    feature_id="joints.velocity",
    arguments={"targets": [{"values": [0.2, -0.1]}], "steps": 24}), metadata=metadata, timeout=10)
# 查询和产物下载方式与 SubmitTask 相同。
```

有 grpcurl 时，本机无 token 示例：

```bash
grpcurl -plaintext -d '{}' 127.0.0.1:50051 a100sim.v1.Simulator/GetApiCatalog
grpcurl -plaintext -d '{"request_id":"inspect-once","feature_id":"joints.state","arguments":{}}' \
  127.0.0.1:50051 a100sim.v1.Simulator/InvokeFeature
```

远程 TLS 去掉 `-plaintext`，按需传 `-cacert`，加 `-H "authorization: Bearer $A100SIM_GRPC_TOKEN"`。标准 `grpc.health.v1.Health` 只表示网关能接收调用；GPU 功能是否启动必须查询目录/能力。

### 扩展规则

新增稳定功能应扩充有类型的 protobuf 消息，保留旧字段编号和语义。运维安装的 Python 插件可通过 `SimulatorBackend.register()` 注册具名受信任操作，再经 `SubmitTask.extension` 使用；`GetCapabilities` 返回其 `parameter_schema` 和范围。调用方不能上传代码、选择服务器函数或执行任意命令。目录中的 UNAVAILABLE/UNVERIFIED 条目必须实现并通过对应数值验收后才能开放，不通过通用 extension 名称绕过。

### 功能验收与性能测试

以下都在 `a100/` 执行。先运行无需 GPU 的传输/合同测试；额外 PBR 数值/格式测试需要在另建的测试环境安装 `requirements-assets.txt`、`usd-core` 和 Mitsuba，缺失时会明确 skip，不能当作这些测试通过。

```bash
.venv-grpc/bin/python -m unittest discover -s tests -v

# 传输测试包含真实 HTTP/2 gRPC、内部 loopback HTTP 和 SQLite，后端为测试 fixture。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/benchmark_grpc.py \
  --loopback-fixture --workloads capabilities catalog simulate \
  --requests 100 --rounds 2 --concurrency 1 8 32 --report results/grpc-transport.json

# 已启动真正的 GPU 服务后执行：会推进/控制共享世界并生成测试产物。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/validate_grpc.py \
  --target 127.0.0.1:50051 --report results/grpc-acceptance.json

# 真实 GPU 刚体/渲染基准；机器人可另选 snapshot、step、capture。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/benchmark_grpc.py \
  --target 127.0.0.1:50051 --workloads simulate render \
  --requests 50 --rounds 3 --concurrency 1 8 32 --report results/grpc-gpu.json
```

`validate_grpc.py` 检查 6.2.0 的 30 类目录、CUDA 落地位置、PBR 场景与语义标注、随机化、机械臂控制、传感器、抓取、轨迹、布料、导入、Newton 和 GLB 转换，并逐项记录缺失依赖。`--skip-workflows` 明确跳过独立子进程。TLS 参数为 `--ca /path/to/ca.pem`，token 从环境读取。

已提交 [接口传输基准](a100/research/grpc-benchmark.json)；它明确标记 `simulator_execution_verified=false`，只衡量调用、排队、持久化与网络成本，不能用作 A100 物理/渲染吞吐。完整目录的静态结构仅构建一次，每次复制并叠加实时状态；不会缓存仿真或渲染结果。GPU 数值和性能只有部署端实际验收后才能报告成功；历史验收不能替代新增接口的现场验证。

另有 [gRPC → A100 实测报告](a100/research/grpc-a100-benchmark.json)：临时运行本次网关，连接已有 CUDA 工作进程，完成 720 次测量调用，其中 360 次为实际仿真/仿真加渲染，全部成功。resident 与 generic 的方块从 2 米落到约 0.10 米，预加载网格渲染返回 `cuda_ad_rgb`，均确认没有 CPU 回退。以下为共享 A100、每组 30 次调用、重复 2 轮的观测范围：

| 工作负载 | 并发 | 完成任务/秒 | 端到端 p95 |
| --- | --- | --- | --- |
| 16 方块、120 步 | 1 | 3.89–3.93 | 267.7–269.0 ms |
| 同上 | 8 | 20.95–21.09 | 369.4–371.8 ms |
| 同上 | 32 | 39.60–39.69 | 748.0–752.6 ms |
| 同样仿真 + 256×256、4 spp RGB | 1 | 3.41–3.50 | 296.9–328.4 ms |
| 同上 | 8 | 17.96–18.14 | 430.7–476.9 ms |
| 同上 | 32 | 35.47–36.59 | 806.4–843.8 ms |

该次实测没有替换已有工作进程，且验证账号无法读取它们的产物目录，因此不包含产物收集/下载成本；不能视为当前分支的完整部署验收。新增 PBR 覆盖、随机化材质、自定义场景标注尚未完成 A100 实测；机器人工作进程未运行，控制、传感器、ROS 2、布料和独立工作流也未纳入这次测量。文件上传/下载已通过本地真实 gRPC 集成测试。上述结果只代表明确列出的负载与测试条件。

## Newton 驱动兼容机制

兼容逻辑由**实际 CUDA driver API 与本机 GPU 架构**决定。

1. MuJoCo Warp 默认会创建条件图节点，该 API 需要 CUDA driver API ≥ 12.4。`create_app()` 在 MuJoCo model 创建后、首次计算前按检测结果设置 `model.opt.graph_conditional`。较旧驱动走库已有的普通 kernel launch 路径，物理求解仍在 CUDA。外层 `NewtonConfig.use_cuda_graph` 是另一个开关，单独关闭它不能处理内部条件图节点。
2. 官方 Newton tensor wheel 的 GPU 二进制可能缺少 A100 原生 cubin，进而依赖驱动不支持的 PTX JIT。`build-newton-kernels.sh` 使用仓库原始 CUDA 算法、本地 `nvcc` 和检测到的 compute capability 构建原生机器码；核对全部导出 launch wrapper 的 ABI，并记录 plugin / 编译库的 SHA-256。启动时只加载与已安装 plugin 匹配的构建；包更新后需重新构建。

兼容库独立存放在 `features/tools/newton-kernels`，官方 plugin 不被替换。构建只调整独立库的日志输出，并隐藏设备 stub / runtime 导出符号。没有修改 PTX 版本字符串，也不通过 CPU 回退掩盖错误。

```bash
# Kit XPBD 刚体落地验证，直接核对 CUDA Warp state
bash run-newton.sh examples/newton_xpbd.py --output "$PWD/results/newton-xpbd"
# 原生 tensor 读写 + MuJoCo 实际关节控制验收
bash run-newton.sh examples/newton_native.py --output "$PWD/results/newton-native"
# 独立布料；常驻机器人的物理引擎仍为 PhysX
bash run-features.sh -m a100sim.newton_cloth --output "$PWD/results/cloth" --frames 90
```

`.env` 中 `A100SIM_NEWTON_CONDITIONAL_GRAPHS=auto|on|off` 控制条件图策略；`on` 在不支持的驱动上会明确报错。`A100SIM_NEWTON_NATIVE_KERNELS=auto|on|off` 控制兼容内核加载：`auto` 使用已有且匹配的本机构建，`on` 要求构建存在，`off` 使用原 wheel。

MuJoCo 验收示例将迭代预算显式设为 20 / 8，可用 `--iterations` / `--line-search-iterations` 调整；复杂接触场景应重新确定收敛精度。示例关闭外层 CUDA graph。普通 launch 路径可能比条件图更慢，验收报告记录实测耗时。

XPBD 的 body poses 可直接变化，而某些 generalized joint state 不随 solver 自动更新；不能把 XPBD 的未更新 `joint_q` 当成实际关节位置。原生关节跟踪验收使用 MuJoCo。独立布料与 PhysX 机器人尚无跨求解器双向耦合。其他 tensor API、软体和复杂接触需分别验证。

Kit fast shutdown 在异常后也可能返回退出码 0，必须检查示例 JSON 的 `success` 及数值。报告失败不能算验收成功。

## 本机排障：机器人、标注与可选 ROS 2

```bash
python3 examples/feature_client.py '{"action":"command","mode":"velocity","targets":[0.25,-0.15],"steps":30}'
python3 examples/feature_client.py '{"action":"command","mode":"effort","targets":[0.4,0],"steps":24}'
# 恢复位置控制
python3 examples/feature_client.py '{"action":"command","targets":[0.6,-0.4],"steps":240}'
python3 examples/feature_client.py '{"action":"dynamics"}'
python3 examples/feature_client.py '{"action":"scan"}'
python3 examples/feature_client.py '{"action":"gripper","operation":"cycle"}'
python3 examples/feature_client.py '{"action":"trajectory","points":[[0.6,-0.4],[0.3,-0.2],[0.1,0.2]]}'
```

HTTP 为 `POST /robotics`、`GET /jobs/<id>`、`GET /health`。队列任务必须等到 `state=succeeded`。动作在拥有 Kit 的同一线程串行执行。单个 `[q1,q2]` 控制 `arm_index` 指定的机械臂；N×2 数组控制全部 N 个。位置 / 速度 / 力矩单位为 rad、rad/s、N·m，边界为 ±2.96、±5、±20；控制会持续到被后续命令覆盖。开启 ROS 时空闲场景仍持续步进。

`capture` 从 CUDA articulation / rigid-body tensors 读取真实位姿，导出独立 USD 副本并渲染。NPZ 包含 `depth_m`、`range_m`、`normal_world`、`instance_id`、`semantic_id`、`valid`；深度为相机前向 Z，range 为欧氏距离。未命中为 0，应结合 valid mask。语义读取 prim 或祖先上的 `a100sim:semanticLabel` 字符串。标注使用像素中心单射线，支持 mesh，Gaussian 标注会拒绝请求。

ROS 默认 `ROS_DOMAIN_ID=80`、`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`。使用 wheel 自带 Jazzy 库，不需要修改宿主 ROS。独立实例应选择自己的 domain；跨主机通信还需配置网络与发现，现有验收只覆盖本地收发。

| Topic | 消息 |
| --- | --- |
| `/a100/joint_states` | `sensor_msgs/msg/JointState` |
| `/a100/imu` | `sensor_msgs/msg/Imu` |
| `/a100/scan` | `sensor_msgs/msg/LaserScan` |
| `/clock` | `rosgraph_msgs/msg/Clock` |
| `/tf` | `tf2_msgs/msg/TFMessage` |
| `/a100/joint_commands` | 订阅 `JointState`；每条命令控制同一机械臂的两关节 |

有 ROS CLI 时，可使用与服务相同的 domain：

```bash
ROS_DOMAIN_ID=80 ros2 topic pub --once /a100/joint_commands sensor_msgs/msg/JointState \
  '{name: [arm0/joint1, arm0/joint2], position: [0.45, -0.25]}'
```

## URDF / MJCF 导入

```bash
bash run-assets.sh examples/import_robot.py examples/robots/two_link.urdf \
  --output "$PWD/results/urdf" --physics-check
bash run-assets.sh examples/import_robot.py examples/robots/two_link.xml \
  --output "$PWD/results/mjcf" --physics-check
bash run-features.sh research/inspect_features.py --output "$PWD/results/inspection.json"
```

`--physics-check` 只面向提供的 fixture。转换器关闭 asset transformer，URDF 还关闭多物理后处理。自己的机器人需检查 mesh 路径、单位、碰撞、质量 / 惯量、关节限位和 drive，并保留 USD 输出目录中的子层 / 引用。

## FastTrellis 资产与统一布光

将自己的 GLB 放到可访问目录，安装独立资产转换环境：

```bash
uv venv --python 3.12 .venv-assets
uv pip install --python .venv-assets/bin/python -r requirements-assets.txt
.venv-assets/bin/python examples/prepare_fasttrellis_assets.py /path/to/your/glb-or-directory \
  --output "$PWD/assets/fasttrellis" --source-up y --max-size 1.2
python3 examples/make_scene.py assets/fasttrellis/assets.json --output results/gallery --seed 42
python3 examples/client.py results/gallery/overview.json
python3 examples/client.py results/gallery/left-angle.json
python3 examples/client.py results/gallery/close-up.json
```

转换保留完整网格，将 Y-up 转为 Z-up、按最长边归一化，按原 UV 将 base color、metallic 和 roughness 烘焙到顶点通道。已有 Z-up 资产改用 `--source-up z`。随机场景包含展台、背景、暖主光与冷补光，生成三个视角。纹理精度受顶点密度限制；透明、法线、AO、发光和复杂 MDL 网络见转换报告中的限制。普通 mesh 不会自动获得机器人关节和物理定义。远程调用请用前文的 gRPC 上传 → `convert_asset` → `randomize/simulate` 流程。

所有服务端资产路径必须位于 `A100SIM_ASSET_ROOT` 中，默认 `$A100SIM_ROOT/assets`；符号链接不能越界。自定义 `mesh.npz` 需要 `vertices`、`normals`、`faces`、`colors`，可附加 `colors_linear`、每顶点 `metallic/roughness`。可选 OIDN CPU 降噪见 [denoise_image.py](a100/examples/denoise_image.py)。

## 本机内部工作进程排障与验收

基础 `POST /simulate` 请求包含 `bodies`、`steps`、`dt`、`gravity`、`ground` 和可选 `render`。服务支持 1–256 个刚体、1–1200 步、64–1024 像素的正方形图像及 1–256 spp。方块用 `size`，球用 `radius`，mesh 用服务端 `asset` 目录；旋转用 `orientation_xyzw`。

```bash
curl --fail -X POST 'http://127.0.0.1:8321/simulate?wait_ms=30000' \
  -H 'Content-Type: application/json' --data-binary @examples/cube.json
curl --fail 'http://127.0.0.1:8321/jobs/<job-id>?wait_ms=30000'
# 直接调用通用后端时关闭长轮询
python3 examples/client.py examples/cube.json --url http://127.0.0.1:18323 --wait-ms 0
```

只提交一次，通过返回的 job ID 查询完成状态。HTTP 202、排队、运行中或等待超时不代表成功。结果 JSON 提供服务端文件路径；接口不负责任意文件下载。通用结果位于 `$A100SIM_ROOT/results/physics-service`，批量结果位于 `$A100SIM_LOCAL_STATE/jobs`。保留策略默认 24 小时或 4096 个完成任务。重启后未完成任务标记为 `interrupted`。

```bash
# 无 GPU 的接口边界与配置测试
python -m unittest discover -s tests -v
# 启动服务后的功能验收；需能够读取服务端结果文件
mkdir -p results/performance
python3 benchmarks/validate_features.py --report results/performance/features.json
# 队列压力与并发基准：在服务空闲时按需执行
python3 benchmarks/validate_http.py
python3 benchmarks/benchmark_http.py --url http://127.0.0.1:8321 \
  --concurrency 64 --rounds 3 --workload default --report results/performance/benchmark.json
```

批量快速路径最多 64 个环境，每环境最多 64 个固定尺寸方块；不符合条件的请求分流通用后端。性能应区分首次编译、预热、排队、GPU 计算和写盘；方块基准不能代表任意 mesh 场景。`a100sim/validate_*` 中的研究脚本可能需要显式输入已有的基准报告，不属于从零安装的必跑步骤。

公开验收报告保留匿名数值和功能范围，不包含部署主机、用户、设备身份或调度任务。兼容修复与启动器在 A100 运行环境上进行实际验证；完整网络下载流程尚未在空白主机从头到尾重建，部署者应执行上述诊断与 smoke / 功能检查。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到 A100 或分配范围内无匹配设备 | 核对 `CUDA_VISIBLE_DEVICES`、本机设备列表与 `ISAAC_GPU` |
| 找不到 Toolkit / Python | 激活 `isaacsim-a100` 环境；非标准安装时设置 `A100SIM_CUDA_ROOT` / `A100SIM_PYTHON` |
| glibc / GLIBCXX 错误 | 完成 `install-libs.sh`，通过仓库启动器运行；必要时配置 `A100SIM_CXX_LIB_DIR` |
| 找不到 OptiX 库 | 执行 `get-driver-libs.sh`；核对归档版本与实际驱动是否匹配 |
| OptiX PTX / copysign 错误 | 检查固定 Dr.Jit 版本与补丁，重新运行 `build-drjit.sh` |
| 条件图节点需要较新驱动 | 使用仓库 `create_app()` 与 `auto` 策略；检查是否绕过兼容层 |
| Newton tensor PTX toolchain 错误 | 构建兼容 kernels；核对使用本仓库 `run-newton.sh` / `create_app()`，更新包后重建 |
| 编译时 ABI 不匹配 | 使用当前分支对应的源码与固定包；检查 Toolkit 的 CUDA C++ 头文件版本 |
| systemd 用户服务不可用 | 先用前台终端启动，核对用户会话与资源管理策略 |
| 端口冲突 | 修改 `.env` 中对应端口；基础入口按配置连接后端 |
| 资产路径被拒绝 | 使用服务端绝对路径，确保位于配置的资产根目录中 |

## 源码、许可与参考

[source-manifest.json](a100/source-manifest.json) 记录适配源码的相对路径、SHA-256 与官方基线。大型运行时、rootfs、wheel、权重、资产、结果及 `.env` 均由 `.gitignore` 排除。

保留官方 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICE.md](THIRD_PARTY_NOTICE.md)。新增适配代码使用 Apache-2.0；Isaac Sim / Kit 运行时、驱动组件、Mitsuba、Dr.Jit、OIDN 和资产分别遵循各自许可。兼容构建使用仓库中的 NVIDIA CUDA 源码并保留版权；Dr.Jit 补丁基于 [drjit-core](https://github.com/mitsuba-renderer/drjit-core)。

参考：[Isaac Sim 6.2 硬件要求](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html)、[release notes](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/overview/release_notes.html)、[Newton 后端](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/newton_physics.html)、[Physics RaycastSensor](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/sensors/isaacsim_sensors_physics_raycast.html)。本方案不改变 NVIDIA 对完整 RTX 应用的官方硬件支持范围。
