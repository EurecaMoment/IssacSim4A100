"""Audit the 30 product domains against implemented, discoverable simulator operations.

Support describes implementation scope. Availability additionally requires live
workers. Historical CUDA acceptance and local contract tests are distinct evidence.
This catalog never advertises extension presence as numerical acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import Any

import grpc
from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError

from a100sim.rpc.errors import ServiceError
from a100sim.rpc.v1 import simulator_pb2 as pb

SIMULATOR_VERSION = "6.2.0.0"
API_VERSION = "a100sim.v1"
ACCEPTANCE = "research/feature-validation.json (historical A100 acceptance; not live gRPC acceptance)"
VALIDATION_SCOPE = (
    "Isaac Sim v6.2.0 source and 6.2.0.0 packages; historical A100 numerical acceptance where cited. "
    "PBR, generalized annotations and randomization have local contract/asset tests only until live acceptance. "
    "Worker readiness is not a guarantee for every parameter or asset. UNVERIFIED features cannot be invoked."
)


@dataclass(frozen=True)
class FeatureSpec:
    """One addressable feature with a bounded implementation or an explicit gap."""

    name: str
    title: str
    support: int
    task: str = ""
    reason: str = ""
    fixed: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainSpec:
    """One domain from the user's product feature inventory, in its original order."""

    name: str
    title: str
    features: tuple[FeatureSpec, ...]


def implemented(
    name: str,
    title: str,
    task: str,
    reason: str,
    *,
    partial: bool = True,
    fixed: dict[str, Any] | None = None,
    historical: bool = True,
) -> FeatureSpec:
    """Declare a real task mapping and its narrower implementation boundary."""
    return FeatureSpec(
        name,
        title,
        pb.PARTIAL if partial else pb.AVAILABLE,
        task,
        reason,
        fixed or {},
        ((ACCEPTANCE,) if historical else ()) + (f"SubmitTask.{task}",),
    )


def gap(name: str, title: str, reason: str, state: int = pb.UNAVAILABLE) -> FeatureSpec:
    """Declare a feature which must fail before simulation queue admission."""
    return FeatureSpec(name, title, state, reason=reason, evidence=("v6.2.0 source audit; no adapter execution route",))


DOMAINS = (
    DomainSpec(
        "scene",
        "三维场景搭建与编辑",
        (
            implemented(
                "assemble",
                "几何体、多资产、位置/姿态/尺寸与物理沉降",
                "simulate",
                "New independent scene per request; cube/sphere/prepared mesh, 1–256 bodies. No interactive editor.",
            ),
            implemented(
                "random_layout",
                "可复现的随机场景布局",
                "randomize",
                "Uniform independent body placement; collisions/clearance are not guaranteed.",
                historical=False,
            ),
            implemented(
                "export", "保存当前机器人场景", "export_scene", "Exports the configured shared robot world only."
            ),
            gap(
                "interactive_editing",
                "层级编辑、复制、吸附、对齐、场景合并与交互加载",
                "No arbitrary stage editing/session API.",
            ),
        ),
    ),
    DomainSpec(
        "usd",
        "OpenUSD 场景组织",
        (
            implemented(
                "stage_output",
                "米制 Z-up USD 场景与变换输出",
                "simulate",
                "Generated simulation stages and poses only.",
            ),
            implemented(
                "robot_export",
                "机器人状态 USD 副本导出",
                "export_scene",
                "Copies the current CUDA poses into an exported stage.",
            ),
            gap(
                "composition",
                "分层、引用、Payload、变体、实例与集合编辑",
                "USD supports these; the adapter has no composition RPC.",
            ),
            gap(
                "package",
                "任意 USD 依赖收集与打包",
                "Robot import returns its own dependency ZIP; no general USD packaging workflow.",
            ),
        ),
    ),
    DomainSpec(
        "assets",
        "模型与工程资产导入导出",
        (
            implemented(
                "import_robot",
                "URDF/MJCF 导入与依赖打包",
                "import_robot",
                "Produces USD and ZIP; physics_check covers bundled fixtures, not arbitrary robot control.",
            ),
            implemented(
                "convert_glb",
                "GLB / FastTrellis 网格与 PBR 转换",
                "convert_asset",
                "Embedded GLB only; vertex-baked linear base color, G roughness and B metallic; inspect warnings.",
                historical=False,
            ),
            gap(
                "fbx_obj_gltf",
                "FBX、OBJ、外部 glTF 转 USD",
                "Public conversion currently accepts self-contained GLB only.",
            ),
            gap(
                "cad_onshape_heightmap",
                "CAD、Onshape 与高度图导入",
                "Converters, credentials and terrain acceptance are not integrated.",
            ),
            gap("urdf_export", "关节机器人导出 URDF", "No registered URDF export workflow."),
            gap(
                "transformer",
                "Asset Transformer、SimReady 验证",
                "Validated import disables transformer postprocessing.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "rendering",
        "材质、灯光与逼真渲染",
        (
            implemented(
                "cuda_path_tracing",
                "A100 CUDA 路径追踪、阴影、反射与全局照明",
                "simulate",
                "Mitsuba/Dr.Jit CUDA rendering via render settings; not native RTX.",
            ),
            implemented(
                "pbr",
                "PBR 颜色、金属度、粗糙度及贴图烘焙",
                "simulate",
                "Body material overrides and baked mesh channels; arbitrary MDL/texture networks unsupported.",
                historical=False,
            ),
            implemented(
                "lighting",
                "统一 Dome/矩形主光与补光",
                "simulate",
                "Constant dome, one key and up to eight fill rectangles; no HDR environment maps.",
            ),
            implemented(
                "preloaded",
                "预加载网格/高斯视角渲染",
                "render_asset",
                "Operator-configured immutable asset, fixed image resolution and lighting.",
            ),
            gap(
                "native_rtx",
                "原生 RTX 实时/路径追踪与 MDL",
                "A100 lacks supported RTX rendering hardware.",
                pb.UNSUPPORTED,
            ),
            gap(
                "optical_postprocessing",
                "玻璃/透明、HDR、曝光、景深与后处理",
                "These material/optical controls are not implemented by this renderer adapter.",
            ),
            gap(
                "video_viewports",
                "多视口、视频编码与原生流媒体",
                "Offline image calls are available; viewport/video sessions are not integrated.",
            ),
        ),
    ),
    DomainSpec(
        "rigid_body",
        "刚体动力学",
        (
            implemented(
                "simulate",
                "CUDA 重力、质量、刚体运动与堆叠",
                "simulate",
                "Cube/sphere/convex-hull mesh, final pose/velocity output, bounded steps and dt.",
            ),
            implemented(
                "step",
                "共享机器人世界固定步长推进",
                "step",
                "Steps the existing world; does not create a new session.",
                partial=False,
            ),
            gap(
                "mass_properties",
                "密度、质心、惯量与阻尼编辑",
                "Only mass is externally configurable; other properties use generated model defaults.",
            ),
            gap(
                "forces",
                "任意刚体初速度、外力与力矩",
                "No generic rigid-body force/velocity command; joint effort control is separate.",
            ),
        ),
    ),
    DomainSpec(
        "collision",
        "碰撞与接触",
        (
            implemented(
                "primitives",
                "基础碰撞体与动态网格凸包",
                "simulate",
                "PhysX primitives/convex hull only; no collision approximation selection.",
            ),
            implemented(
                "contact", "接触检测与接触力读取", "snapshot", "Sensors attached to the configured robot world."
            ),
            implemented("raycast", "几何射线扫描", "scan", "Fixed 180-beam planar scan, no optical return model."),
            gap(
                "sdf_decomposition",
                "SDF、凸分解与任意静态三角网格",
                "Requires an exposed cooking/configuration path and numerical acceptance.",
                pb.UNVERIFIED,
            ),
            gap(
                "properties_filters",
                "摩擦/弹性、CCD、自碰撞与过滤配置",
                "Generated scenes use backend defaults; no per-body collision configuration RPC.",
            ),
            gap(
                "triggers_conveyor",
                "触发器、重叠、运动学表面速度与传送带",
                "No public command route for these workflows.",
            ),
        ),
    ),
    DomainSpec(
        "joints",
        "机器人关节与机构",
        (
            implemented(
                "position",
                "关节位置控制",
                "control",
                "Configured two-joint arms, up to 16; radians ±2.96.",
                fixed={"mode": "POSITION"},
            ),
            implemented(
                "velocity",
                "关节速度控制",
                "control",
                "Configured two-joint arms; rad/s ±5.",
                fixed={"mode": "VELOCITY"},
            ),
            implemented(
                "effort", "关节力矩控制", "control", "Configured two-joint arms; N·m ±20.", fixed={"mode": "EFFORT"}
            ),
            implemented(
                "state",
                "关节位置/速度/Effort 与状态读取",
                "snapshot",
                "Only joints instantiated in the configured world.",
            ),
            implemented(
                "dynamics", "Jacobian、质量矩阵和关节作用力", "dynamics", "Batched tensors of the configured arms."
            ),
            gap(
                "arbitrary_articulation",
                "任意机器人实例、固定/浮动基座与关节限位编辑",
                "Import produces an artifact; it does not instantiate a controllable session.",
            ),
            gap(
                "mimic_closed_loop",
                "Mimic、闭环、腱及复杂约束",
                "Backend/model-specific acceptance and control interfaces required.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "deformables",
        "软体、可变形体、粒子与流体",
        (
            implemented(
                "cloth",
                "独立 Newton XPBD 布料",
                "cloth",
                "No bidirectional coupling to PhysX or arbitrary cloth setup.",
            ),
            gap(
                "volume_surface",
                "体积/表面可变形体与附着",
                "Not enabled or numerically accepted by this adapter.",
                pb.UNVERIFIED,
            ),
            gap(
                "particles_fluids",
                "粒子、采样、流体与发射器",
                "No accepted CUDA particle/fluid workflow.",
                pb.UNVERIFIED,
            ),
            gap("gears_tendons", "腱、齿轮和齿条机构", "Requires model-specific backend validation.", pb.UNVERIFIED),
        ),
    ),
    DomainSpec(
        "newton",
        "Newton 实验性物理后端",
        (
            implemented(
                "xpbd",
                "CUDA XPBD 刚体验收",
                "newton",
                "Fixed isolated demo; not arbitrary Newton model/session control.",
                fixed={"solver": "XPBD"},
            ),
            implemented(
                "mujoco",
                "MuJoCo Warp / 原生 tensor 验收",
                "newton",
                "Uses driver API detection and locally built compatible kernels; fixed demo.",
                fixed={"solver": "MUJOCO_NATIVE"},
            ),
            implemented("cloth", "Warp XPBD 布料输出", "cloth", "Independent workflow with USD/NPZ outputs."),
            gap(
                "general_tensors",
                "任意 Newton tensor、模型与可微分求解",
                "Tensor checks cover the fixed demo only; general model API unverified.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "robot_types",
        "不同形态的机器人",
        (
            implemented(
                "demo_arms",
                "双关节机械臂演示与批量控制",
                "control",
                "One to sixteen configured demo arms; no catalog selection.",
            ),
            implemented(
                "robot_assets",
                "导入其他机器人定义为 USD",
                "import_robot",
                "Import capability does not certify or control the imported model.",
            ),
            gap(
                "industrial_mobile_legged",
                "工业臂、移动臂、AMR/叉车、四足与人形实例",
                "Robot assets, model configuration and live execution acceptance are required.",
            ),
            gap(
                "hands_suction",
                "多指手、原生吸盘与其他末端执行器",
                "Constraint-grasp demo is available; native Surface Gripper not accepted.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "robot_setup",
        "机器人装配与调参",
        (
            implemented(
                "inspect_gains",
                "关节检查与有效惯量",
                "inspect_gains",
                "Gain Tuner discovery/inertia core; no automatic gain optimization.",
            ),
            implemented("export", "当前机器人状态与 USD 检查产物", "export_scene", "Operator-configured world only."),
            gap("assembler", "Robot Assembler 与关节/质量/碰撞编辑", "No assembly or arbitrary model editing RPC."),
            gap(
                "tuning_xrdf_grasps",
                "自动增益优化、XRDF 和抓取姿态编辑",
                "Tools/configuration are not exposed or accepted.",
            ),
        ),
    ),
    DomainSpec(
        "motion",
        "运动学、路径规划与轨迹生成",
        (
            implemented(
                "trajectory",
                "关节轨迹生成与实际执行",
                "trajectory",
                "Two-DOF configured arms only; no obstacle planner.",
            ),
            implemented(
                "jacobian", "Jacobian 与动力学读取", "dynamics", "Access to tensors does not implement an IK solver."
            ),
            gap(
                "fk_ik",
                "通用 FK/IK 与末端位姿控制",
                "Robot descriptions, solvers and numerical tests not integrated.",
                pb.UNVERIFIED,
            ),
            gap(
                "planners",
                "RMPflow、Lula、cuMotion、Pink 与避障规划",
                "Dependency/model-specific planners have no accepted RPC execution route.",
                pb.UNVERIFIED,
            ),
            gap("task_space", "任务空间控制、轨迹优化与世界同步", "No general task-space/planner state interface."),
        ),
    ),
    DomainSpec(
        "grasping",
        "机械臂操作与抓取",
        (
            implemented(
                "constraint_grasp",
                "约束吸附、抬升、释放与抓取循环",
                "gripper",
                "Custom PhysX fixed joint; not a native suction/contact seal model.",
            ),
            gap(
                "native_gripper",
                "原生 Surface Gripper 与多指夹爪",
                "Extension load is not grasp acceptance.",
                pb.UNVERIFIED,
            ),
            gap(
                "pick_place",
                "任意物体拾放、堆叠、码垛与分拣任务",
                "Task setup, robot/asset controls and success metrics are not integrated.",
            ),
            gap("grasp_sampling", "抓取候选采样、编辑、成功率评测", "No general grasp generation/evaluation RPC."),
        ),
    ),
    DomainSpec(
        "navigation",
        "移动机器人与导航",
        (
            implemented(
                "differential_drive",
                "差速底盘到轮速转换",
                "differential_drive",
                "Returns wheel velocities only; no chassis is spawned or advanced.",
            ),
            implemented(
                "geometry_scan", "障碍物平面距离扫描", "scan", "Existing demo scene; no navigation map/session."
            ),
            gap(
                "base_controllers",
                "全向/Ackermann 动力学、里程计与航点跟随",
                "No mobile-robot dynamics or closed-loop controller RPC.",
            ),
            gap(
                "maps_terrain", "占据地图、高度图、地形与多机器人导航", "Map building/terrain workflows not integrated."
            ),
            gap(
                "nav2_slam",
                "Nav2、SLAM、定位与目标导航",
                "External stacks and complete loop need separate acceptance.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "camera",
        "RGB 相机、镜头与成像",
        (
            implemented(
                "scene_camera",
                "自定义场景 RGB 与环绕相机",
                "simulate",
                "Square 64–1024 image, orbit camera, size/spp/seed; fixed pinhole optics.",
            ),
            implemented(
                "robot_capture",
                "同步机器人实际位姿的 RGB 与标注",
                "capture",
                "Configured perspective camera; not an RTX Camera sensor.",
            ),
            implemented(
                "preloaded", "固定资产相机视角", "render_asset", "Azimuth/elevation/distance only; fixed resolution."
            ),
            gap(
                "lens_calibration",
                "内参编辑、鱼眼/畸变、光圈与相机标定",
                "Some output metadata exists; general lens control/calibration is not implemented.",
            ),
            gap(
                "batch_timing_isp",
                "多相机同步、频率/偏移、多 tick、噪声与 ISP",
                "Repeated capture is possible; no scheduled multicamera sensor API.",
            ),
        ),
    ),
    DomainSpec(
        "depth",
        "深度与结构光相机",
        (
            implemented(
                "robot_depth",
                "机器人场景 Z-depth 与欧氏 range",
                "capture",
                "Custom single-primary-ray arrays in meters, with valid mask.",
            ),
            implemented(
                "scene_depth",
                "自定义场景深度/距离标注",
                "simulate",
                "Set render.annotations=true; mesh mode only.",
                historical=False,
            ),
            gap(
                "structured_light",
                "投影图案、序列、标定与结构光恢复",
                "No projector, timing or reconstruction pipeline.",
            ),
            gap(
                "device_noise",
                "视差、噪声、异常点与真实设备模型",
                "Ideal geometry depth only; no device signal/noise model.",
            ),
        ),
    ),
    DomainSpec(
        "range_sensors",
        "激光雷达、毫米波雷达和超声",
        (
            implemented(
                "geometry_scan",
                "几何碰撞射线测距替代",
                "scan",
                "180-beam planar ranges; no reflectance, timing or multipath model.",
            ),
            gap(
                "rtx_lidar",
                "RTX LiDAR 扫描、强度、时序与完整帧",
                "Requires supported native RTX hardware.",
                pb.UNSUPPORTED,
            ),
            gap(
                "rtx_radar", "RTX Radar 传播、距离与径向速度", "Requires supported native RTX hardware.", pb.UNSUPPORTED
            ),
            gap(
                "rtx_acoustic",
                "RTX Acoustic 超声通道与幅值信号",
                "Requires supported native RTX hardware.",
                pb.UNSUPPORTED,
            ),
            gap(
                "nonvisual_materials",
                "非视觉材料响应配置",
                "No accepted nonvisual sensor/material route on this A100 adapter.",
            ),
        ),
    ),
    DomainSpec(
        "physics_sensors",
        "接触、IMU 与状态传感器",
        (
            implemented(
                "state",
                "IMU、接触、JointState 与 Effort",
                "snapshot",
                "Official physics sensors on predefined bodies; not arbitrary sensor attachment.",
            ),
            implemented(
                "scan", "Raycast 平面测距", "scan", "Fixed geometry, layout and 180 beams; miss values are null."
            ),
            implemented("forces", "关节动力学和作用力", "dynamics", "Configured robot tensors only."),
            gap(
                "sensor_configuration",
                "传感器挂载、射线时间偏移与任意光束布局",
                "No public sensor configuration/session RPC.",
            ),
            gap(
                "tactile",
                "触觉皮肤与 GelSight",
                "Assets and physics/rendering models need independent acceptance.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "synthetic_data",
        "合成数据与自动真值标注",
        (
            implemented(
                "robot_annotations",
                "RGB、深度/range、法向、实例/语义 ID 与 mask",
                "capture",
                "Custom Mitsuba annotation pipeline; not native Replicator.",
            ),
            implemented(
                "scene_annotations",
                "用户场景语义标签与自动真值",
                "simulate",
                "Body semantic_label and mesh primary rays; seed/pose metadata.",
                historical=False,
            ),
            gap(
                "boxes_flow_points",
                "运动向量、2D/3D 框与标注点云",
                "No registered output producer; depth arrays alone are not these annotations.",
            ),
            gap(
                "replicator",
                "Replicator Annotator、Writer、多相机与事件采集",
                "Native render-product writer path is not integrated on this adapter.",
            ),
        ),
    ),
    DomainSpec(
        "randomization",
        "域随机化与数据增强",
        (
            implemented(
                "scene",
                "位置、颜色、质量与金属度/粗糙度随机化",
                "randomize",
                "Seeded uniform sampling of explicit bodies; deterministic decoded scene; no clearance checks.",
                historical=False,
            ),
            implemented(
                "lighting_camera",
                "灯光和相机的显式参数变化",
                "simulate",
                "Caller supplies settings per request; no automatic distribution scheduler.",
            ),
            gap(
                "physics_textures",
                "摩擦、纹理分布、数量/遮挡约束与触发器",
                "These randomization channels/constraints are not integrated.",
            ),
            gap(
                "infinigen_cosmos",
                "Infinigen 与 Cosmos 环境/数据增强",
                "External software/models and service workflow required.",
            ),
        ),
    ),
    DomainSpec(
        "specialized_sdg",
        "专用抓取与移动数据生成",
        (
            implemented(
                "grasp_demo", "抓取循环状态结果", "gripper", "Demo result only; not Grasping SDG dataset generation."
            ),
            gap("grasping_sdg", "Grasping SDG 候选、阶段与数据集", "No configured generator/writer API."),
            gap("mobilitygen", "MobilityGen 轨迹录制、回放与重渲染", "No mobile scene/trajectory workflow integrated."),
        ),
    ),
    DomainSpec(
        "characters",
        "人物、动画与角色行为",
        (
            gap("animation", "多人物、骨骼动画、重定向与动画图", "No accepted character/animation RPC workflow."),
            gap(
                "behavior_navigation",
                "导航网格、行为树、巡逻与碰撞反应",
                "No runtime character behavior/session interface.",
            ),
            gap(
                "physical_humans",
                "人体受力、平衡与动作回放",
                "Requires separate character physics and model-specific acceptance.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "events",
        "事故、事件与长尾数据",
        (
            implemented(
                "rigid_events",
                "刚体堆叠、掉落与倾倒场景",
                "simulate",
                "General rigid-body initial conditions only; no semantic incident generator.",
            ),
            gap(
                "incident_generator",
                "堆箱、货架、空闲空间、YAML 事件与报告",
                "No incident generation/configuration/reporting route.",
            ),
            gap(
                "smoke_fire", "烟火、洒落与角色响应", "No accepted fluid/volumetric effects or event behavior pipeline."
            ),
        ),
    ),
    DomainSpec(
        "language",
        "场景图、文字描述与自然语言辅助",
        (
            implemented(
                "scene_metadata",
                "物体位姿、ID 与语义元数据",
                "simulate",
                "Structured numeric output only; no relation graph or language generation.",
            ),
            gap(
                "graphs_captioning",
                "空间关系图、描述与 VLM Captioning",
                "Requires an integrated scene-graph/model service.",
            ),
            gap("natural_language", "自然语言行为树与 Chat IRO YAML", "No model endpoint/behavior runtime integrated."),
            gap(
                "camera_placement",
                "覆盖率、遮挡、兴趣点与相机自动布置",
                "Orbit camera parameters exist; no placement optimizer/calibration workflow.",
            ),
        ),
    ),
    DomainSpec(
        "recording_xr",
        "录制、回放、示教与 XR/VR",
        (
            implemented(
                "state_export",
                "单次仿真状态 NPZ 与 USD 输出",
                "simulate",
                "Final state only; not a temporal episode recorder.",
            ),
            implemented(
                "trajectory_trace",
                "演示关节轨迹的采样记录",
                "trajectory",
                "Sampled joint positions only; not an HDF5 episode.",
            ),
            gap(
                "episodes_replay",
                "HDF5 Episode Recorder、示教、回放与离线重渲染",
                "No episode lifecycle/restore/replay RPC.",
            ),
            gap(
                "xr_teleop",
                "OpenXR、VR 遥操作、Isaac Teleop 与 CloudXR",
                "No device/service pipeline; XR entrypoints alone do not provide teleoperation.",
            ),
        ),
    ),
    DomainSpec(
        "ros2",
        "ROS 2 与机器人软件生态",
        (
            implemented(
                "status",
                "自定义 Jazzy 数据桥状态与计数",
                "ros2_status",
                "Optional bridge; DSH uses gRPC and need not depend on ROS.",
            ),
            implemented(
                "joint_control",
                "通过 gRPC 控制同一机器人世界",
                "control",
                "ROS command subscription is optional; two-DOF demo limits apply.",
            ),
            implemented(
                "sensor_data",
                "通过 gRPC 获取 JointState/IMU/扫描等桥接源数据",
                "snapshot",
                "Existing data bridge publishes five topic types; no arbitrary ROS graph proxy.",
            ),
            gap(
                "image_cloud_radar",
                "ROS 图像、压缩图、深度、点云与雷达发布",
                "Not implemented by the custom physics-only bridge.",
            ),
            gap(
                "qos_services_launch",
                "QoS、命名空间、自定义消息、服务与 ROS Launch 配置",
                "Bridge configuration is operator-owned; no runtime ROS administration RPC.",
            ),
            gap(
                "full_bridge",
                "原生 OmniGraph ROS Bridge、Nav2/MoveIt/ros2_control",
                "Complete external loops require separate acceptance.",
                pb.UNVERIFIED,
            ),
        ),
    ),
    DomainSpec(
        "learning",
        "强化学习、模仿学习与策略部署",
        (
            implemented(
                "rl_step",
                "批量 observation/reward step",
                "rl_step",
                "Two-joint demo observations/rewards; terminated is always false; no Gym reset/truncation.",
            ),
            implemented(
                "clone_state",
                "克隆机械臂世界的批量状态",
                "snapshot",
                "Operator chooses 1–16 arms at worker startup; no runtime environment creation.",
            ),
            gap(
                "reset_training",
                "环境 reset、完整训练、任务与奖励配置",
                "No Gymnasium/Isaac Lab environment lifecycle or training API.",
            ),
            gap(
                "policy_imitation",
                "策略推理、回放、模仿学习与 Torch 接口",
                "Training/policy dependencies and robot tasks not integrated.",
            ),
            gap("differentiation", "可微分学习工作流", "Requires separate solver/gradient acceptance.", pb.UNVERIFIED),
        ),
    ),
    DomainSpec(
        "digital_twin",
        "数字孪生、仓储与物流",
        (
            implemented(
                "layout",
                "多资产布局与场景渲染",
                "simulate",
                "Prepared meshes and primitives; application semantics belong to the caller.",
            ),
            implemented(
                "random_layout",
                "随机布局与沉降",
                "randomize",
                "No warehouse constraint solver or logistics optimization.",
                historical=False,
            ),
            gap(
                "conveyor_forklift",
                "传送带、叉车、移动机器人与物流动力学",
                "No accepted general logistics model/controller sessions.",
            ),
            gap(
                "enterprise",
                "cuOpt、MES、WMS、PLC 与业务调度",
                "External systems have no simulator-side connector in this adapter.",
            ),
        ),
    ),
    DomainSpec(
        "reconstruction",
        "真实场景重建、3DGS、内容管理与协作",
        (
            implemented(
                "gaussian",
                "CUDA 3DGS 与网格混合渲染",
                "simulate",
                "Prepared Gaussian PLY plus proxy mesh; radiance is baked, not relightable PBR.",
            ),
            implemented(
                "preloaded",
                "预加载 Gaussian 资产视角",
                "render_asset",
                "Requires object.ply in the operator-configured asset directory.",
                fixed={"kind": "GAUSSIAN_RENDER"},
            ),
            gap("training_nurec", "3DGS 重建训练、NuRec 转换与工具", "No accepted training/NuRec conversion RPC."),
            gap(
                "content_collaboration",
                "SimReady 检索、内容库、Nucleus 与 Live Session",
                "No content-service credentials or collaboration workflow integrated.",
            ),
            gap(
                "gaussian_physics",
                "从 3DGS 自动生成碰撞和交互语义",
                "Gaussian visual radiance does not define reliable physics or object semantics.",
            ),
        ),
    ),
    DomainSpec(
        "development",
        "编程、自动化、部署与调试",
        (
            implemented(
                "fixed_step", "固定步长与手动推进", "step", "One shared robot world; persistent operations serialize."
            ),
            implemented(
                "batch",
                "独立场景批处理",
                "simulate",
                "Resident path handles eligible cubes; other scenes route to generic CUDA worker.",
            ),
            implemented(
                "diagnostics",
                "状态、物理传感器与动力学诊断",
                "snapshot",
                "Public gRPC health/catalog/jobs and benchmark tools; process administration is local.",
            ),
            gap(
                "arbitrary_code",
                "远程执行 Python/C++、OmniGraph 或任意脚本",
                "Deliberately not exposed; operators may register trusted named operations.",
                pb.UNSUPPORTED,
            ),
            gap("gui_streaming", "GUI、WebRTC、RTSP 与远程桌面", "No accepted native viewport/camera streaming path."),
            gap(
                "multi_gpu",
                "多 GPU 调度、Docker/云部署编排与 Tracy 会话",
                "Portable single-A100 Bash/Conda deployment; no multi-GPU orchestration RPC.",
            ),
        ),
    ),
)


def message_schema(descriptor: Descriptor) -> dict[str, Any]:
    """Generate a strict protobuf-JSON argument schema without importing simulator SDKs."""
    properties = {}
    for value in descriptor.fields:
        if value.message_type:
            schema = (
                {"type": "object"}
                if value.message_type.full_name == "google.protobuf.Struct"
                else message_schema(value.message_type)
            )
        elif value.enum_type:
            schema = {"type": "string", "enum": list(value.enum_type.values_by_name)}
        elif value.type == FieldDescriptor.TYPE_BOOL:
            schema = {"type": "boolean"}
        elif value.type == FieldDescriptor.TYPE_STRING:
            schema = {"type": "string"}
        elif value.type in (FieldDescriptor.TYPE_FLOAT, FieldDescriptor.TYPE_DOUBLE):
            schema = {"type": "number"}
        else:
            schema = {"type": "integer"}
        if value.is_repeated:
            schema = {"type": "array", "items": schema}
        properties[value.name] = schema
    return {"type": "object", "additionalProperties": False, "properties": properties}


@cache
def _catalog_template() -> pb.ApiCatalog:
    """Prepare the static audit and nested protobuf argument schemas once per process."""
    catalog = pb.ApiCatalog(
        api_version=API_VERSION, simulator_version=SIMULATOR_VERSION, validation_scope=VALIDATION_SCOPE
    )
    tasks = pb.SubmitTaskRequest.DESCRIPTOR.fields_by_name
    for number, domain in enumerate(DOMAINS, 1):
        output = catalog.domains.add(number=number, id=domain.name, title=domain.title)
        for feature in domain.features:
            value = output.features.add(
                id=domain.name + "." + feature.name,
                title=feature.title,
                support=feature.support,
                availability=feature.support,
                reason=feature.reason,
                task_field=feature.task,
                evidence=feature.evidence,
            )
            if feature.task:
                descriptor = tasks[feature.task].message_type
                value.request_type = descriptor.full_name
                value.request_schema.update(message_schema(descriptor))
                value.fixed_arguments.update(feature.fixed)
    return catalog


def build_catalog(capabilities: list[pb.Capability]) -> pb.ApiCatalog:
    """Copy the static audit and overlay current readiness without sharing mutable output."""
    live = {value.name: value for value in capabilities}
    catalog = pb.ApiCatalog()
    catalog.CopyFrom(_catalog_template())
    for domain in catalog.domains:
        for feature in domain.features:
            if feature.task_field and (
                feature.task_field not in live or live[feature.task_field].availability != pb.AVAILABLE
            ):
                feature.availability = pb.UNAVAILABLE
                feature.reason += " Required worker/workflow is not ready."
    return catalog


def resolve_feature(request: pb.FeatureRequest) -> pb.SubmitTaskRequest:
    """Resolve one named feature to the same canonical request used by SubmitTask."""
    feature = next(
        (
            feature
            for domain in DOMAINS
            for feature in domain.features
            if domain.name + "." + feature.name == request.feature_id
        ),
        None,
    )
    if feature is None:
        raise ServiceError(grpc.StatusCode.NOT_FOUND, "Unknown feature_id; read GetApiCatalog")
    if not feature.task:
        raise ServiceError(grpc.StatusCode.UNIMPLEMENTED, feature.reason)
    # Parse the arguments as their concrete protobuf message so unknown fields,
    # invalid enum values and wrong types use the normal typed contract validation.
    arguments = MessageToDict(request.arguments, preserving_proto_field_name=True)
    for name, value in feature.fixed.items():
        if name in arguments and arguments[name] != value:
            raise ValueError(f"{request.feature_id} requires {name}={value}")
        arguments[name] = value
    result = pb.SubmitTaskRequest(request_id=request.request_id)
    try:
        ParseDict(arguments, getattr(result, feature.task), ignore_unknown_fields=False)
    except ParseError as error:
        raise ValueError(f"Invalid {feature.task} arguments: {error}") from error
    getattr(result, feature.task).SetInParent()
    return result
