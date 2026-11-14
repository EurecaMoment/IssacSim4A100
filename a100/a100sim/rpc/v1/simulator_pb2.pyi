from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Availability(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AVAILABILITY_UNSPECIFIED: _ClassVar[Availability]
    AVAILABLE: _ClassVar[Availability]
    UNAVAILABLE: _ClassVar[Availability]
    UNSUPPORTED: _ClassVar[Availability]
    UNVERIFIED: _ClassVar[Availability]
    PARTIAL: _ClassVar[Availability]

class Shape(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CUBE: _ClassVar[Shape]
    SPHERE: _ClassVar[Shape]
    MESH: _ClassVar[Shape]

class RenderKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MESH_RENDER: _ClassVar[RenderKind]
    GAUSSIAN_RENDER: _ClassVar[RenderKind]

class PhysicsRoute(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTO: _ClassVar[PhysicsRoute]
    GENERIC: _ClassVar[PhysicsRoute]

class ControlMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POSITION: _ClassVar[ControlMode]
    VELOCITY: _ClassVar[ControlMode]
    EFFORT: _ClassVar[ControlMode]

class NewtonSolver(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    XPBD: _ClassVar[NewtonSolver]
    MUJOCO_NATIVE: _ClassVar[NewtonSolver]

class JobState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_STATE_UNSPECIFIED: _ClassVar[JobState]
    QUEUED: _ClassVar[JobState]
    RUNNING: _ClassVar[JobState]
    SUCCEEDED: _ClassVar[JobState]
    FAILED: _ClassVar[JobState]
    CANCELLED: _ClassVar[JobState]
    INTERRUPTED: _ClassVar[JobState]
AVAILABILITY_UNSPECIFIED: Availability
AVAILABLE: Availability
UNAVAILABLE: Availability
UNSUPPORTED: Availability
UNVERIFIED: Availability
PARTIAL: Availability
CUBE: Shape
SPHERE: Shape
MESH: Shape
MESH_RENDER: RenderKind
GAUSSIAN_RENDER: RenderKind
AUTO: PhysicsRoute
GENERIC: PhysicsRoute
POSITION: ControlMode
VELOCITY: ControlMode
EFFORT: ControlMode
XPBD: NewtonSolver
MUJOCO_NATIVE: NewtonSolver
JOB_STATE_UNSPECIFIED: JobState
QUEUED: JobState
RUNNING: JobState
SUCCEEDED: JobState
FAILED: JobState
CANCELLED: JobState
INTERRUPTED: JobState

class Feature(_message.Message):
    __slots__ = ("id", "title", "support", "availability", "reason", "task_field", "request_type", "request_schema", "fixed_arguments", "evidence")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUPPORT_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_FIELD_NUMBER: _ClassVar[int]
    REQUEST_TYPE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_SCHEMA_FIELD_NUMBER: _ClassVar[int]
    FIXED_ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    support: Availability
    availability: Availability
    reason: str
    task_field: str
    request_type: str
    request_schema: _struct_pb2.Struct
    fixed_arguments: _struct_pb2.Struct
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., support: _Optional[_Union[Availability, str]] = ..., availability: _Optional[_Union[Availability, str]] = ..., reason: _Optional[str] = ..., task_field: _Optional[str] = ..., request_type: _Optional[str] = ..., request_schema: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., fixed_arguments: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...

class ApiDomain(_message.Message):
    __slots__ = ("number", "id", "title", "features")
    NUMBER_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    number: int
    id: str
    title: str
    features: _containers.RepeatedCompositeFieldContainer[Feature]
    def __init__(self, number: _Optional[int] = ..., id: _Optional[str] = ..., title: _Optional[str] = ..., features: _Optional[_Iterable[_Union[Feature, _Mapping]]] = ...) -> None: ...

class ApiCatalog(_message.Message):
    __slots__ = ("api_version", "simulator_version", "domains", "validation_scope")
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    SIMULATOR_VERSION_FIELD_NUMBER: _ClassVar[int]
    DOMAINS_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_SCOPE_FIELD_NUMBER: _ClassVar[int]
    api_version: str
    simulator_version: str
    domains: _containers.RepeatedCompositeFieldContainer[ApiDomain]
    validation_scope: str
    def __init__(self, api_version: _Optional[str] = ..., simulator_version: _Optional[str] = ..., domains: _Optional[_Iterable[_Union[ApiDomain, _Mapping]]] = ..., validation_scope: _Optional[str] = ...) -> None: ...

class FeatureRequest(_message.Message):
    __slots__ = ("request_id", "feature_id", "arguments")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    FEATURE_ID_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    feature_id: str
    arguments: _struct_pb2.Struct
    def __init__(self, request_id: _Optional[str] = ..., feature_id: _Optional[str] = ..., arguments: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Capability(_message.Message):
    __slots__ = ("name", "availability", "reason", "scope", "details")
    NAME_FIELD_NUMBER: _ClassVar[int]
    AVAILABILITY_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    name: str
    availability: Availability
    reason: str
    scope: str
    details: _struct_pb2.Struct
    def __init__(self, name: _Optional[str] = ..., availability: _Optional[_Union[Availability, str]] = ..., reason: _Optional[str] = ..., scope: _Optional[str] = ..., details: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Capabilities(_message.Message):
    __slots__ = ("api_version", "simulator_version", "native_rtx", "capabilities", "queue_capacity", "queued_jobs", "running_jobs")
    API_VERSION_FIELD_NUMBER: _ClassVar[int]
    SIMULATOR_VERSION_FIELD_NUMBER: _ClassVar[int]
    NATIVE_RTX_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    QUEUE_CAPACITY_FIELD_NUMBER: _ClassVar[int]
    QUEUED_JOBS_FIELD_NUMBER: _ClassVar[int]
    RUNNING_JOBS_FIELD_NUMBER: _ClassVar[int]
    api_version: str
    simulator_version: str
    native_rtx: bool
    capabilities: _containers.RepeatedCompositeFieldContainer[Capability]
    queue_capacity: int
    queued_jobs: int
    running_jobs: int
    def __init__(self, api_version: _Optional[str] = ..., simulator_version: _Optional[str] = ..., native_rtx: _Optional[bool] = ..., capabilities: _Optional[_Iterable[_Union[Capability, _Mapping]]] = ..., queue_capacity: _Optional[int] = ..., queued_jobs: _Optional[int] = ..., running_jobs: _Optional[int] = ...) -> None: ...

class PbrMaterial(_message.Message):
    __slots__ = ("base_color", "metallic", "roughness")
    BASE_COLOR_FIELD_NUMBER: _ClassVar[int]
    METALLIC_FIELD_NUMBER: _ClassVar[int]
    ROUGHNESS_FIELD_NUMBER: _ClassVar[int]
    base_color: _containers.RepeatedScalarFieldContainer[float]
    metallic: float
    roughness: float
    def __init__(self, base_color: _Optional[_Iterable[float]] = ..., metallic: _Optional[float] = ..., roughness: _Optional[float] = ...) -> None: ...

class Body(_message.Message):
    __slots__ = ("id", "shape", "position", "orientation_xyzw", "size", "radius", "mass", "asset_id", "color", "material", "semantic_label")
    ID_FIELD_NUMBER: _ClassVar[int]
    SHAPE_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    ORIENTATION_XYZW_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    RADIUS_FIELD_NUMBER: _ClassVar[int]
    MASS_FIELD_NUMBER: _ClassVar[int]
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    COLOR_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_FIELD_NUMBER: _ClassVar[int]
    SEMANTIC_LABEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    shape: Shape
    position: _containers.RepeatedScalarFieldContainer[float]
    orientation_xyzw: _containers.RepeatedScalarFieldContainer[float]
    size: _containers.RepeatedScalarFieldContainer[float]
    radius: float
    mass: float
    asset_id: str
    color: _containers.RepeatedScalarFieldContainer[float]
    material: PbrMaterial
    semantic_label: str
    def __init__(self, id: _Optional[str] = ..., shape: _Optional[_Union[Shape, str]] = ..., position: _Optional[_Iterable[float]] = ..., orientation_xyzw: _Optional[_Iterable[float]] = ..., size: _Optional[_Iterable[float]] = ..., radius: _Optional[float] = ..., mass: _Optional[float] = ..., asset_id: _Optional[str] = ..., color: _Optional[_Iterable[float]] = ..., material: _Optional[_Union[PbrMaterial, _Mapping]] = ..., semantic_label: _Optional[str] = ...) -> None: ...

class Light(_message.Message):
    __slots__ = ("color", "intensity", "exposure", "width", "height", "origin", "target")
    COLOR_FIELD_NUMBER: _ClassVar[int]
    INTENSITY_FIELD_NUMBER: _ClassVar[int]
    EXPOSURE_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    color: _containers.RepeatedScalarFieldContainer[float]
    intensity: float
    exposure: float
    width: float
    height: float
    origin: _containers.RepeatedScalarFieldContainer[float]
    target: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, color: _Optional[_Iterable[float]] = ..., intensity: _Optional[float] = ..., exposure: _Optional[float] = ..., width: _Optional[float] = ..., height: _Optional[float] = ..., origin: _Optional[_Iterable[float]] = ..., target: _Optional[_Iterable[float]] = ...) -> None: ...

class Lighting(_message.Message):
    __slots__ = ("dome", "key", "fills")
    DOME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    FILLS_FIELD_NUMBER: _ClassVar[int]
    dome: Light
    key: Light
    fills: _containers.RepeatedCompositeFieldContainer[Light]
    def __init__(self, dome: _Optional[_Union[Light, _Mapping]] = ..., key: _Optional[_Union[Light, _Mapping]] = ..., fills: _Optional[_Iterable[_Union[Light, _Mapping]]] = ...) -> None: ...

class RenderSettings(_message.Message):
    __slots__ = ("kind", "size", "spp", "seed", "azimuth", "elevation", "distance", "target", "lighting", "gaussian_gain", "annotations")
    KIND_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    SPP_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    AZIMUTH_FIELD_NUMBER: _ClassVar[int]
    ELEVATION_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    LIGHTING_FIELD_NUMBER: _ClassVar[int]
    GAUSSIAN_GAIN_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    kind: RenderKind
    size: int
    spp: int
    seed: int
    azimuth: float
    elevation: float
    distance: float
    target: _containers.RepeatedScalarFieldContainer[float]
    lighting: Lighting
    gaussian_gain: float
    annotations: bool
    def __init__(self, kind: _Optional[_Union[RenderKind, str]] = ..., size: _Optional[int] = ..., spp: _Optional[int] = ..., seed: _Optional[int] = ..., azimuth: _Optional[float] = ..., elevation: _Optional[float] = ..., distance: _Optional[float] = ..., target: _Optional[_Iterable[float]] = ..., lighting: _Optional[_Union[Lighting, _Mapping]] = ..., gaussian_gain: _Optional[float] = ..., annotations: _Optional[bool] = ...) -> None: ...

class SimulateRequest(_message.Message):
    __slots__ = ("bodies", "steps", "dt", "gravity", "ground", "render", "route")
    BODIES_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    DT_FIELD_NUMBER: _ClassVar[int]
    GRAVITY_FIELD_NUMBER: _ClassVar[int]
    GROUND_FIELD_NUMBER: _ClassVar[int]
    RENDER_FIELD_NUMBER: _ClassVar[int]
    ROUTE_FIELD_NUMBER: _ClassVar[int]
    bodies: _containers.RepeatedCompositeFieldContainer[Body]
    steps: int
    dt: float
    gravity: float
    ground: bool
    render: RenderSettings
    route: PhysicsRoute
    def __init__(self, bodies: _Optional[_Iterable[_Union[Body, _Mapping]]] = ..., steps: _Optional[int] = ..., dt: _Optional[float] = ..., gravity: _Optional[float] = ..., ground: _Optional[bool] = ..., render: _Optional[_Union[RenderSettings, _Mapping]] = ..., route: _Optional[_Union[PhysicsRoute, str]] = ...) -> None: ...

class JointTargets(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, values: _Optional[_Iterable[float]] = ...) -> None: ...

class ControlRequest(_message.Message):
    __slots__ = ("mode", "arm_index", "targets", "steps")
    MODE_FIELD_NUMBER: _ClassVar[int]
    ARM_INDEX_FIELD_NUMBER: _ClassVar[int]
    TARGETS_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    mode: ControlMode
    arm_index: int
    targets: _containers.RepeatedCompositeFieldContainer[JointTargets]
    steps: int
    def __init__(self, mode: _Optional[_Union[ControlMode, str]] = ..., arm_index: _Optional[int] = ..., targets: _Optional[_Iterable[_Union[JointTargets, _Mapping]]] = ..., steps: _Optional[int] = ...) -> None: ...

class StepRequest(_message.Message):
    __slots__ = ("steps",)
    STEPS_FIELD_NUMBER: _ClassVar[int]
    steps: int
    def __init__(self, steps: _Optional[int] = ...) -> None: ...

class CaptureRequest(_message.Message):
    __slots__ = ("size", "spp")
    SIZE_FIELD_NUMBER: _ClassVar[int]
    SPP_FIELD_NUMBER: _ClassVar[int]
    size: int
    spp: int
    def __init__(self, size: _Optional[int] = ..., spp: _Optional[int] = ...) -> None: ...

class GripperRequest(_message.Message):
    __slots__ = ("operation",)
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    operation: str
    def __init__(self, operation: _Optional[str] = ...) -> None: ...

class TrajectoryRequest(_message.Message):
    __slots__ = ("points", "arm_index", "max_velocity", "max_acceleration")
    POINTS_FIELD_NUMBER: _ClassVar[int]
    ARM_INDEX_FIELD_NUMBER: _ClassVar[int]
    MAX_VELOCITY_FIELD_NUMBER: _ClassVar[int]
    MAX_ACCELERATION_FIELD_NUMBER: _ClassVar[int]
    points: _containers.RepeatedCompositeFieldContainer[JointTargets]
    arm_index: int
    max_velocity: float
    max_acceleration: float
    def __init__(self, points: _Optional[_Iterable[_Union[JointTargets, _Mapping]]] = ..., arm_index: _Optional[int] = ..., max_velocity: _Optional[float] = ..., max_acceleration: _Optional[float] = ...) -> None: ...

class DifferentialDriveRequest(_message.Message):
    __slots__ = ("linear_velocity", "angular_velocity", "wheel_radius", "wheel_base")
    LINEAR_VELOCITY_FIELD_NUMBER: _ClassVar[int]
    ANGULAR_VELOCITY_FIELD_NUMBER: _ClassVar[int]
    WHEEL_RADIUS_FIELD_NUMBER: _ClassVar[int]
    WHEEL_BASE_FIELD_NUMBER: _ClassVar[int]
    linear_velocity: float
    angular_velocity: float
    wheel_radius: float
    wheel_base: float
    def __init__(self, linear_velocity: _Optional[float] = ..., angular_velocity: _Optional[float] = ..., wheel_radius: _Optional[float] = ..., wheel_base: _Optional[float] = ...) -> None: ...

class ClothRequest(_message.Message):
    __slots__ = ("frames",)
    FRAMES_FIELD_NUMBER: _ClassVar[int]
    frames: int
    def __init__(self, frames: _Optional[int] = ...) -> None: ...

class ImportRobotRequest(_message.Message):
    __slots__ = ("asset_id", "relative_path", "physics_check")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIVE_PATH_FIELD_NUMBER: _ClassVar[int]
    PHYSICS_CHECK_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    relative_path: str
    physics_check: bool
    def __init__(self, asset_id: _Optional[str] = ..., relative_path: _Optional[str] = ..., physics_check: _Optional[bool] = ...) -> None: ...

class NewtonRequest(_message.Message):
    __slots__ = ("solver", "steps", "iterations", "line_search_iterations")
    SOLVER_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    ITERATIONS_FIELD_NUMBER: _ClassVar[int]
    LINE_SEARCH_ITERATIONS_FIELD_NUMBER: _ClassVar[int]
    solver: NewtonSolver
    steps: int
    iterations: int
    line_search_iterations: int
    def __init__(self, solver: _Optional[_Union[NewtonSolver, str]] = ..., steps: _Optional[int] = ..., iterations: _Optional[int] = ..., line_search_iterations: _Optional[int] = ...) -> None: ...

class ConvertAssetRequest(_message.Message):
    __slots__ = ("asset_id", "relative_path", "source_up", "max_size", "strict_materials")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIVE_PATH_FIELD_NUMBER: _ClassVar[int]
    SOURCE_UP_FIELD_NUMBER: _ClassVar[int]
    MAX_SIZE_FIELD_NUMBER: _ClassVar[int]
    STRICT_MATERIALS_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    relative_path: str
    source_up: str
    max_size: float
    strict_materials: bool
    def __init__(self, asset_id: _Optional[str] = ..., relative_path: _Optional[str] = ..., source_up: _Optional[str] = ..., max_size: _Optional[float] = ..., strict_materials: _Optional[bool] = ...) -> None: ...

class RandomizeRequest(_message.Message):
    __slots__ = ("scene", "seed", "position_min", "position_max", "randomize_color", "metallic_range", "roughness_range", "mass_range")
    SCENE_FIELD_NUMBER: _ClassVar[int]
    SEED_FIELD_NUMBER: _ClassVar[int]
    POSITION_MIN_FIELD_NUMBER: _ClassVar[int]
    POSITION_MAX_FIELD_NUMBER: _ClassVar[int]
    RANDOMIZE_COLOR_FIELD_NUMBER: _ClassVar[int]
    METALLIC_RANGE_FIELD_NUMBER: _ClassVar[int]
    ROUGHNESS_RANGE_FIELD_NUMBER: _ClassVar[int]
    MASS_RANGE_FIELD_NUMBER: _ClassVar[int]
    scene: SimulateRequest
    seed: int
    position_min: _containers.RepeatedScalarFieldContainer[float]
    position_max: _containers.RepeatedScalarFieldContainer[float]
    randomize_color: bool
    metallic_range: _containers.RepeatedScalarFieldContainer[float]
    roughness_range: _containers.RepeatedScalarFieldContainer[float]
    mass_range: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, scene: _Optional[_Union[SimulateRequest, _Mapping]] = ..., seed: _Optional[int] = ..., position_min: _Optional[_Iterable[float]] = ..., position_max: _Optional[_Iterable[float]] = ..., randomize_color: _Optional[bool] = ..., metallic_range: _Optional[_Iterable[float]] = ..., roughness_range: _Optional[_Iterable[float]] = ..., mass_range: _Optional[_Iterable[float]] = ...) -> None: ...

class ExtensionRequest(_message.Message):
    __slots__ = ("operation", "arguments")
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    operation: str
    arguments: _struct_pb2.Struct
    def __init__(self, operation: _Optional[str] = ..., arguments: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class SubmitTaskRequest(_message.Message):
    __slots__ = ("request_id", "simulate", "render_asset", "snapshot", "step", "control", "dynamics", "scan", "capture", "gripper", "trajectory", "differential_drive", "rl_step", "cloth", "import_robot", "newton", "convert_asset", "export_scene", "inspect_gains", "ros2_status", "randomize", "extension")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SIMULATE_FIELD_NUMBER: _ClassVar[int]
    RENDER_ASSET_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    CONTROL_FIELD_NUMBER: _ClassVar[int]
    DYNAMICS_FIELD_NUMBER: _ClassVar[int]
    SCAN_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_FIELD_NUMBER: _ClassVar[int]
    GRIPPER_FIELD_NUMBER: _ClassVar[int]
    TRAJECTORY_FIELD_NUMBER: _ClassVar[int]
    DIFFERENTIAL_DRIVE_FIELD_NUMBER: _ClassVar[int]
    RL_STEP_FIELD_NUMBER: _ClassVar[int]
    CLOTH_FIELD_NUMBER: _ClassVar[int]
    IMPORT_ROBOT_FIELD_NUMBER: _ClassVar[int]
    NEWTON_FIELD_NUMBER: _ClassVar[int]
    CONVERT_ASSET_FIELD_NUMBER: _ClassVar[int]
    EXPORT_SCENE_FIELD_NUMBER: _ClassVar[int]
    INSPECT_GAINS_FIELD_NUMBER: _ClassVar[int]
    ROS2_STATUS_FIELD_NUMBER: _ClassVar[int]
    RANDOMIZE_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    simulate: SimulateRequest
    render_asset: RenderSettings
    snapshot: _empty_pb2.Empty
    step: StepRequest
    control: ControlRequest
    dynamics: _empty_pb2.Empty
    scan: _empty_pb2.Empty
    capture: CaptureRequest
    gripper: GripperRequest
    trajectory: TrajectoryRequest
    differential_drive: DifferentialDriveRequest
    rl_step: ControlRequest
    cloth: ClothRequest
    import_robot: ImportRobotRequest
    newton: NewtonRequest
    convert_asset: ConvertAssetRequest
    export_scene: _empty_pb2.Empty
    inspect_gains: _empty_pb2.Empty
    ros2_status: _empty_pb2.Empty
    randomize: RandomizeRequest
    extension: ExtensionRequest
    def __init__(self, request_id: _Optional[str] = ..., simulate: _Optional[_Union[SimulateRequest, _Mapping]] = ..., render_asset: _Optional[_Union[RenderSettings, _Mapping]] = ..., snapshot: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., step: _Optional[_Union[StepRequest, _Mapping]] = ..., control: _Optional[_Union[ControlRequest, _Mapping]] = ..., dynamics: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., scan: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., capture: _Optional[_Union[CaptureRequest, _Mapping]] = ..., gripper: _Optional[_Union[GripperRequest, _Mapping]] = ..., trajectory: _Optional[_Union[TrajectoryRequest, _Mapping]] = ..., differential_drive: _Optional[_Union[DifferentialDriveRequest, _Mapping]] = ..., rl_step: _Optional[_Union[ControlRequest, _Mapping]] = ..., cloth: _Optional[_Union[ClothRequest, _Mapping]] = ..., import_robot: _Optional[_Union[ImportRobotRequest, _Mapping]] = ..., newton: _Optional[_Union[NewtonRequest, _Mapping]] = ..., convert_asset: _Optional[_Union[ConvertAssetRequest, _Mapping]] = ..., export_scene: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., inspect_gains: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., ros2_status: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., randomize: _Optional[_Union[RandomizeRequest, _Mapping]] = ..., extension: _Optional[_Union[ExtensionRequest, _Mapping]] = ...) -> None: ...

class JobRequest(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class WatchJobRequest(_message.Message):
    __slots__ = ("job_id", "after_revision")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_REVISION_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    after_revision: int
    def __init__(self, job_id: _Optional[str] = ..., after_revision: _Optional[int] = ...) -> None: ...

class Failure(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class Timings(_message.Message):
    __slots__ = ("gateway_queue_seconds", "backend_wait_seconds", "upstream_queue_seconds", "upstream_execution_seconds", "total_seconds", "submit_seconds")
    GATEWAY_QUEUE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    BACKEND_WAIT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    UPSTREAM_QUEUE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    UPSTREAM_EXECUTION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SUBMIT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    gateway_queue_seconds: float
    backend_wait_seconds: float
    upstream_queue_seconds: float
    upstream_execution_seconds: float
    total_seconds: float
    submit_seconds: float
    def __init__(self, gateway_queue_seconds: _Optional[float] = ..., backend_wait_seconds: _Optional[float] = ..., upstream_queue_seconds: _Optional[float] = ..., upstream_execution_seconds: _Optional[float] = ..., total_seconds: _Optional[float] = ..., submit_seconds: _Optional[float] = ...) -> None: ...

class Artifact(_message.Message):
    __slots__ = ("artifact_id", "name", "size_bytes", "media_type")
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    name: str
    size_bytes: int
    media_type: str
    def __init__(self, artifact_id: _Optional[str] = ..., name: _Optional[str] = ..., size_bytes: _Optional[int] = ..., media_type: _Optional[str] = ...) -> None: ...

class Job(_message.Message):
    __slots__ = ("job_id", "request_id", "operation", "state", "revision", "result", "failure", "artifacts", "timings", "created_at_unix")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    TIMINGS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    request_id: str
    operation: str
    state: JobState
    revision: int
    result: _struct_pb2.Struct
    failure: Failure
    artifacts: _containers.RepeatedCompositeFieldContainer[Artifact]
    timings: Timings
    created_at_unix: float
    def __init__(self, job_id: _Optional[str] = ..., request_id: _Optional[str] = ..., operation: _Optional[str] = ..., state: _Optional[_Union[JobState, str]] = ..., revision: _Optional[int] = ..., result: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ..., artifacts: _Optional[_Iterable[_Union[Artifact, _Mapping]]] = ..., timings: _Optional[_Union[Timings, _Mapping]] = ..., created_at_unix: _Optional[float] = ...) -> None: ...

class Asset(_message.Message):
    __slots__ = ("asset_id", "files")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    files: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, asset_id: _Optional[str] = ..., files: _Optional[_Iterable[str]] = ...) -> None: ...

class AssetList(_message.Message):
    __slots__ = ("assets",)
    ASSETS_FIELD_NUMBER: _ClassVar[int]
    assets: _containers.RepeatedCompositeFieldContainer[Asset]
    def __init__(self, assets: _Optional[_Iterable[_Union[Asset, _Mapping]]] = ...) -> None: ...

class UploadChunk(_message.Message):
    __slots__ = ("asset_id", "relative_path", "data", "sha256")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIVE_PATH_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    relative_path: str
    data: bytes
    sha256: str
    def __init__(self, asset_id: _Optional[str] = ..., relative_path: _Optional[str] = ..., data: _Optional[bytes] = ..., sha256: _Optional[str] = ...) -> None: ...

class AssetFile(_message.Message):
    __slots__ = ("asset_id", "relative_path", "size_bytes", "sha256")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIVE_PATH_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    relative_path: str
    size_bytes: int
    sha256: str
    def __init__(self, asset_id: _Optional[str] = ..., relative_path: _Optional[str] = ..., size_bytes: _Optional[int] = ..., sha256: _Optional[str] = ...) -> None: ...

class DownloadRequest(_message.Message):
    __slots__ = ("job_id", "artifact_id", "offset", "chunk_bytes")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    CHUNK_BYTES_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    artifact_id: str
    offset: int
    chunk_bytes: int
    def __init__(self, job_id: _Optional[str] = ..., artifact_id: _Optional[str] = ..., offset: _Optional[int] = ..., chunk_bytes: _Optional[int] = ...) -> None: ...

class DownloadChunk(_message.Message):
    __slots__ = ("offset", "data")
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    offset: int
    data: bytes
    def __init__(self, offset: _Optional[int] = ..., data: _Optional[bytes] = ...) -> None: ...
