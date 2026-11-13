# IssacSim4A100

在自己的 NVIDIA A100 上部署 **Isaac Sim 6.2 CUDA 物理仿真、机器人控制与 Mitsuba CUDA 渲染**。

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

脚本会创建或更新仓库 Conda 环境，在该环境中准备 Isaac Sim 运行时、执行 GPU smoke，再启动基础服务和机器人、传感器、ROS 2、Newton 等可选功能。首次运行会询问 NVIDIA 许可确认；服务以本地后台进程运行，日志写入 `logs/`。

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

| 默认地址 | 服务 |
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

“服务”表示已有 HTTP 动作；“脚本”表示提供独立调用与验收方法。实测证据位于 [feature-validation.json](a100/research/feature-validation.json)，扩展清单见 [extension-inventory.json](a100/research/extension-inventory.json)。安装或加载扩展本身不算功能验收。

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

## Isaac Sim 6.2 能力调研与开发边界

调研基线为本分支固定的官方 `v6.2.0` 源码、[6.2 官方文档](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/index.html)和已有 A100 验收记录。运行 wheel 的内部标识是 `6.2.0-rc.14+release.52805.02189a84.gl`，不能把源码 tag、wheel 版本和运行构建号视为同一项。下列“官方能力”覆盖产品范围；部署是否可调用由实时能力查询决定。

| 领域 | 官方能力 | 本 A100 适配的实际范围 |
| --- | --- | --- |
| 场景与数字孪生 | OpenUSD 层、引用、变体、实例、物理材质；仓库、工厂和机器人资产 | 场景生成、USD 输出、随机布局、统一灯光；未提供任意 USD 场景的交互编辑接口 |
| 资产处理 | URDF/MJCF 导入、URDF 导出、CAD/glTF 转换、Asset Transformer、SimReady 验证 | URDF/MJCF 导入与 GLB 转换；Transformer、CAD、URDF 导出尚无 RPC 实现 |
| 刚体与关节 | PhysX 接触、摩擦、约束、articulation、GPU 动力学和克隆 | CUDA 刚体、mesh convex hull、双关节臂、多环境、动力学查询已有验收 |
| Newton | Warp 求解器、MuJoCo Warp、tensor API、执行器、可微分工作流 | XPBD、独立布料、MuJoCo 原生 tensor 验收；可微分训练与其他求解器未验收 |
| 高级物理 | 可变形体、粒子、流体、SDF 碰撞、复杂接触模型 | 仅独立 XPBD 布料通过验收；不能推定其他物理模型均可用 |
| 操作与移动 | 位置/速度/力矩控制、轨迹、IK、RMPflow、cuMotion、Pink、差速/Ackermann/全向控制 | 关节控制、轨迹执行、约束抓取；差速仅返回轮速；规划器和移动导航闭环待集成 |
| 物理传感器 | IMU、接触、力矩、关节状态和物理射线 | 已有官方物理传感器及平面几何扫描；不模拟光学回波 |
| 视觉及非视觉 RTX | RGB、深度、结构光、畸变模型、多相机；LiDAR、Radar、声学 | A100 缺少 RT Core；自定义 CUDA RGB/深度/ID 不等同于这些原生传感器模型 |
| 数据生成 | Replicator 随机化、annotator/writer；MobilityGen、事件/行为、抓取与遥操作数据 | 自定义随机场景、RGB、深度、距离、法向、实例/语义 ID；其余生成框架待集成 |
| ROS 2 与外部软件 | 传感器、TF、时钟、控制、仿真管理；Nav2、MoveIt 2、ros2_control | 自定义 Jazzy 数据桥和关节命令；尚未验收 Nav2/MoveIt/ros2_control 完整闭环 |
| 机器人学习 | Isaac Lab 并行环境、训练与策略评估，独立训练框架 | `rl_step` 提供基础 observation/reward；不是完整 Gymnasium/Isaac Lab 训练环境 |
| 开发与观测 | Python/C++、OmniGraph、扩展、独立 libraries、性能分析、MCP、GUI/流媒体 | Bash/Conda、独立工作进程、HTTP 与 gRPC；GUI、WebRTC、MCP 未接入当前适配 |

依据：[参考架构](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/introduction/reference_architecture.html)、[Newton](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/newton_physics.html)、[物理限制](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/physics_resources.html)、[传感器](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/learn/sensors_and_physics.html)、[ROS 2](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/ros2_tutorials/index.html)、[数据生成](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/synthetic_data_generation/index.html)。6.2 已移除 Cortex；新开发应使用 Experimental Core API，并按对应物理后端验收。

## gRPC 接口：供 DSH 等外部调用方使用

gRPC 服务属于 Isaac Sim 端，调用方仅依赖 [a100sim.v1 协议](a100/a100sim/rpc/v1/simulator.proto)。协议包不依赖 DSH、Kit 或 CUDA。网关在独立 Python 环境运行，连接同一部署中的内部工作进程；所有 Kit/物理操作继续由工作进程的所属线程执行。现有 HTTP 地址为部署内部实现，外部调用方使用一个 gRPC 地址。

```text
外部调用方 → a100sim.v1 gRPC → 有界、持久化任务队列
                                  ├─ PhysX 批处理 / 通用仿真进程
                                  ├─ 机器人、传感器、渲染与 ROS 2 进程
                                  ├─ 预加载渲染进程
                                  └─ 独立 URDF/MJCF、Newton、GLB 转换工作流
```

### 启动

完成上述基础与可选功能部署后，在已激活的 Conda 环境内、`a100/` 目录执行：

```bash
bash serve-grpc.sh install
bash serve-grpc.sh
```

默认监听 `127.0.0.1:50051`。前台启动可用 `Ctrl+C` 停止；它不会启动或停止已有 GPU 工作进程。`deploy.sh` 管理的基础与可选服务按原方式启停。网关环境默认位于 `$A100SIM_ROOT/.venv-grpc`，安装 gRPC/protobuf 不会更改 Kit 的 Python 包。

独立的导入和 Newton 工作流需要在 `.env` 设置 `A100SIM_GRPC_WORKFLOWS=1`，并完成对应运行环境安装。GLB 转换使用资产准备章节中的 `.venv-assets` 环境，或者通过 `A100SIM_ASSET_PYTHON` 指定转换环境的 Python。缺失组件会在能力查询中返回不可用。

跨机器直接监听时，在 `.env` 配置自己的域名/绑定地址、TLS 证书和密钥，并设置 `A100SIM_GRPC_TOKEN`。非本机监听缺少 TLS 或 token 时拒绝启动；RPC、标准健康检查和 reflection 使用相同的 `authorization: Bearer <token>`。调用方验证服务端证书；仓库不内置任何凭证。也可通过自己已有的安全隧道连接本机监听端口。

### 协议与操作覆盖

| RPC | 行为 |
| --- | --- |
| `GetCapabilities` | 返回 API 版本、实时队列状态和每项能力的 `AVAILABLE / UNAVAILABLE / UNSUPPORTED / UNVERIFIED`、范围和原因 |
| `SubmitTask` | 强类型 `oneof` 请求；返回持久化的任务身份，不等待仿真完成 |
| `GetJob / WatchJob` | 查询快照或订阅递增 revision，直到终态；支持从已知 revision 继续观察 |
| `CancelJob` | 取消仍在网关排队的任务；已提交给仿真器的任务返回 `FAILED_PRECONDITION` |
| `RunTasks` | 双向流；同一流按顺序提交，每个响应是对应任务的终态，提供流控 |
| `ListAssets / UploadAsset` | 使用资产 ID 与相对路径；分块上传、SHA-256 校验、禁止覆盖、限制总大小 |
| `DownloadArtifact` | 按任务和 artifact ID 分块下载；可指定偏移继续下载，无需共享服务器磁盘 |

`SubmitTask` 覆盖 19 类现有操作：

| 请求字段 | 实际行为 |
| --- | --- |
| `simulate` | CUDA 刚体、mesh 碰撞、批处理分流、场景输出和可选渲染 |
| `render_asset` | 对配置的预加载 mesh/Gaussian 资产渲染 |
| `snapshot / step / control` | 机器人状态、推进仿真、位置/速度/力矩命令 |
| `dynamics / scan` | 动力学矩阵与作用力、物理射线扫描 |
| `capture` | 同步机器人真实 CUDA 位姿的 RGB 和自定义标注 |
| `gripper / trajectory / differential_drive` | 约束抓取、关节轨迹执行、差速轮速计算 |
| `rl_step / cloth` | 批量基础 observation/reward、独立 Newton 布料 |
| `import_robot / newton / convert_asset` | 独立 URDF/MJCF 导入、Newton 验收工作流、GLB 转换；转换结果返回可用于 mesh 仿真的资产 ID |
| `export_scene / inspect_gains / ros2_status` | 当前机器人 USD 输出、关节与有效惯量发现、ROS 2 桥状态 |

这覆盖本适配现有服务和脚本的功能，**不代表把官方产品所有功能都实现成 RPC**。例如，导入机器人目前输出 USD，不能把任意导入机器人替换进常驻双关节演示世界；`newton` 是固定验收工作流，并非任意 Newton 模型的交互式控制会话。完整训练、规划器、原生 RTX、遥操作、CAD、URDF 导出等会明确报告未接入或未验收。

导入工作流同时返回保留目录结构的 ZIP，包含导出的 USD、工作流生成的依赖和验收报告，可通过 `DownloadArtifact` 获取。

后续功能可在 Isaac Sim 端通过 `SimulatorBackend.register()` 注册受信任操作，配合 `ExtensionRequest` 和能力描述中的参数 schema 扩展。调用方不能上传 Python、选择服务器函数或执行任意命令。稳定的高频能力应新增有类型的 protobuf 消息，并遵守字段编号兼容规则。

### 调用约定

- 每次逻辑操作提供稳定且唯一的 `request_id`。相同 ID 和相同请求返回同一任务；相同 ID 但不同请求返回 `ALREADY_EXISTS`。超时后用原 ID 或任务 ID 查询，不要换 ID 重发控制命令。
- 幂等记录与结果元数据写入本地 SQLite WAL，`synchronous=FULL`；重启将未完成任务标记为 `INTERRUPTED`，不自动重放。默认保留最近最多 4096 个任务，过期后不再保证旧 ID 的去重。该机制不宣称跨进程崩溃的分布式 exactly-once。
- 网关队列默认 64，分发任务默认最多 32；满载返回 `RESOURCE_EXHAUSTED`，不接受新任务。现有工作进程也有独立的有界队列。常驻机器人世界的操作按顺序执行；多个调用方共享这个世界，不提供多租户会话隔离。严格连续控制使用 `RunTasks`，并由调用方管理世界的使用权。
- 单次 RPC 的 deadline/断开连接终止等待，不撤销已接受的仿真。已运行任务不能承诺无副作用取消。停止网关也不会回滚已提交给工作进程的操作。
- 请求使用明确的 protobuf 类型；可变长度结果通过 `Struct` 返回，图像、NPZ、USD 等二进制使用 artifact 流传输。`Struct` 的数值遵循 protobuf double 语义；时间、任务 ID 和文件大小使用协议中的专门字段。上传默认每文件 512 MiB、每块不超过 1 MiB；下载默认每块 256 KiB。
- 位置、长度与深度为米，关节角为弧度；相机 `azimuth/elevation` 为度。四元数标注为 `xyzw`。标注文件进一步说明深度、法向和 ID 的坐标与有效性约定。
- 标准 `grpc.health.v1.Health` 表示网关能够接收调用；GPU 功能是否就绪必须检查 `GetCapabilities`。同一主机上可以通过 `.env` 调整内部端口与 `A100SIM_GRPC_*_URL`，调用方无须知道这些内部地址。

已提交 Python bindings。其他语言可直接根据 `.proto` 生成 SDK；Python 重新生成的方法是：

```bash
# 在独立的网关环境中执行，不在 Kit 环境中安装 protoc 工具。
.venv-grpc/bin/python -m pip install grpcio-tools==1.85.1
.venv-grpc/bin/python -m grpc_tools.protoc -I . \
  --python_out=. --grpc_python_out=. --pyi_out=. a100sim/rpc/v1/simulator.proto
```

### 功能验收与性能测试

以下命令在 `a100/` 目录执行。测试和基准属于仿真器端工具，不包含 DSH 业务逻辑：

```bash
# 无 GPU：真实 HTTP/2 RPC、去重、队列、取消、重启、上传下载、内部 HTTP 转发。
.venv-grpc/bin/python -m unittest discover -s tests -v

# 无 GPU：传输基准；真实 gRPC + HTTP + SQLite，后端是明确标记的测试 fixture。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/benchmark_grpc.py \
  --loopback-fixture --requests 200 --rounds 3 --concurrency 1 8 32 \
  --report results/grpc-transport.json

# 已启动 GPU 服务：通过 gRPC 验收当前可用操作并下载、校验产物。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/validate_grpc.py \
  --target 127.0.0.1:50051 --report results/grpc-acceptance.json

# 真正的 GPU 刚体吞吐与渲染基准；机器人状态/单步/捕获可单独选择。
PYTHONPATH="$PWD" .venv-grpc/bin/python benchmarks/benchmark_grpc.py \
  --target 127.0.0.1:50051 --workloads simulate render \
  --requests 50 --rounds 3 --concurrency 1 8 32 --report results/grpc-gpu.json
```

远程 TLS 服务增加 `--ca /path/to/ca.pem`，通过环境变量传入 token。`validate_grpc.py` 会逐项记录未启动/未安装的功能；`--skip-workflows` 可明确跳过需要新建进程的验收。验收会实际推进和控制共享机器人世界、创建测试资产并生成产物，适合在开发实例空闲时执行。

基准区分提交延迟、端到端延迟、网关/上游排队、上游执行时间，并记录并发度、预热、重复轮次、完成吞吐、p50/p95/p99 和错误数。`simulator_execution_verified=false` 表示没有确认 CUDA 仿真执行，不能把 fixture 结果当作 A100 性能。单个有状态世界的控制吞吐也不能替代独立环境批量仿真吞吐。

已提交的 [接口基准报告](a100/research/grpc-benchmark.json) 来自 loopback 测试，包含持久化与真实网络协议开销，报告明确标记 `simulator_execution_verified=false`。新增 RPC 的 A100 功能与性能应通过上述部署端命令验收；原有 CUDA 功能记录 `feature-validation.json` 不能替代新接口验收。

该报告共计 3600 次测量调用，全部成功。以下为每组 3 轮、每轮 200 次调用的中位数；具体平台与所有分位数见 JSON。这里的 `simulate` 后端是测试 fixture，**没有执行物理或渲染**，数据不能用作 GPU 吞吐指标。

| 测试链路 | 并发 | 完成数/秒 | 端到端 p95 |
| --- | --- | --- | --- |
| 能力查询 | 1 | 325.2 | 5.41 ms |
| 能力查询 | 8 | 481.6 | 20.89 ms |
| 能力查询 | 32 | 484.3 | 76.50 ms |
| 任务提交、持久化、内部 HTTP、结果订阅 | 1 | 47.0 | 29.95 ms |
| 同上 | 8 | 60.0 | 191.26 ms |
| 同上 | 32 | 59.5 | 661.93 ms |

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

## 机器人、标注与 ROS 2 用法

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

转换保留完整网格，将 Y-up 转为 Z-up、按最长边归一化，按原 UV 将 base-color 纹理采样到顶点颜色。已有 Z-up 资产改用 `--source-up z`。随机场景包含展台、背景、暖主光与冷补光，生成三个视角。metallic / roughness、透明材质和复杂 MDL / PBR 网络尚未完整还原；普通 mesh 不会自动获得机器人关节和物理定义。

所有服务端资产路径必须位于 `A100SIM_ASSET_ROOT` 中，默认 `$A100SIM_ROOT/assets`；符号链接不能越界。自定义 `mesh.npz` 需要 `vertices`、`normals`、`faces`、`colors`。可选 OIDN CPU 降噪见 [denoise_image.py](a100/examples/denoise_image.py)。

## 接口与验收

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
