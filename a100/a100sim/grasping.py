"""Build a PhysX constraint suction demo or the unverified native gripper probe."""

from __future__ import annotations

from typing import Any


def create_gripper(stage: Any, *, backend: str = "constraint") -> dict[str, Any]:
    """Author a driven suction body and cube, with an explicitly selected backend."""
    from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdPhysics

    root = "/World/Grasp"
    UsdGeom.Xform.Define(stage, root)

    def cube(name: str, height: float, *, kinematic: bool = False, no_gravity: bool = False) -> str:
        path = root + "/" + name
        prim = UsdGeom.Cube.Define(stage, path)
        prim.CreateSizeAttr(0.04)
        prim.AddTranslateOp().Set(Gf.Vec3d(-3.0, -3.0, height))
        prim.AddOrientOp().Set(Gf.Quatf(1.0, Gf.Vec3f(0)))
        prim.CreateDisplayColorAttr([Gf.Vec3f(0.8, 0.35, 0.1) if name == "Target" else Gf.Vec3f(0.6)])
        UsdPhysics.CollisionAPI.Apply(prim.GetPrim())
        rigid = UsdPhysics.RigidBodyAPI.Apply(prim.GetPrim())
        rigid.CreateKinematicEnabledAttr(kinematic)
        UsdPhysics.MassAPI.Apply(prim.GetPrim()).CreateMassAttr(0.05)
        PhysxSchema.PhysxRigidBodyAPI.Apply(prim.GetPrim()).CreateDisableGravityAttr(no_gravity)
        return path

    carrier = cube("Carrier", 0.4, kinematic=True)
    body = cube("Body", 0.2, no_gravity=True)
    target = cube("Target", 0.02)
    joint = UsdPhysics.PrismaticJoint.Define(stage, root + "/Drive")
    joint.CreateBody0Rel().SetTargets([carrier])
    joint.CreateBody1Rel().SetTargets([body])
    joint.CreateAxisAttr("Z")
    joint.CreateLowerLimitAttr(-1.0)
    joint.CreateUpperLimitAttr(1.0)
    joint.CreateLocalPos0Attr(Gf.Vec3f(0))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0))
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
    drive.CreateTypeAttr("force")
    drive.CreateMaxForceAttr(1e6)
    drive.CreateStiffnessAttr(1e6)
    drive.CreateDampingAttr(1e4)
    drive.CreateTargetPositionAttr(-0.2)
    if backend == "constraint":
        return {
            "backend": "custom PhysX fixed-joint suction demo",
            "held": False,
            "target": target,
            "body": body,
            "drive": drive.GetTargetPositionAttr(),
        }
    if backend != "native":
        raise ValueError("gripper backend must be constraint or native")
    from isaacsim.robot.surface_gripper import GripperView, _surface_gripper
    from usd.schema.isaac import robot_schema

    attach = UsdPhysics.Joint.Define(stage, root + "/Attachment")
    attach.CreateBody0Rel().SetTargets([body])
    attach.CreateBody1Rel().SetTargets([carrier])
    attach.CreateLocalPos0Attr(Gf.Vec3f(0, 0, -0.02))
    attach.CreateLocalPos1Attr(Gf.Vec3f(0, 0, -0.22))
    down = Gf.Quatf(0.70710678, 0, 0.70710678, 0)
    attach.CreateLocalRot0Attr(down)
    attach.CreateLocalRot1Attr(down)
    attach.CreateExcludeFromArticulationAttr(True)
    for axis in ("transX", "transY", "transZ", "rotX", "rotY", "rotZ"):
        limit = UsdPhysics.LimitAPI.Apply(attach.GetPrim(), axis)
        limit.CreateLowAttr(1.0)
        limit.CreateHighAttr(-1.0)
    robot_schema.ApplyAttachmentPointAPI(attach.GetPrim())
    attach.GetPrim().CreateAttribute(robot_schema.Attributes.FORWARD_AXIS.name, Sdf.ValueTypeNames.Token).Set("X")
    attach.GetPrim().CreateAttribute(robot_schema.Attributes.CLEARANCE_OFFSET.name, Sdf.ValueTypeNames.Float).Set(0.001)
    path = root + "/SurfaceGripper"
    prim = robot_schema.CreateSurfaceGripper(stage, path)
    # The deployed rc.14 binary also reads the gripper-wide axis; author both.
    prim.CreateAttribute(robot_schema.Attributes.FORWARD_AXIS.name, Sdf.ValueTypeNames.Token).Set("X")
    prim.GetRelationship(robot_schema.Relations.ATTACHMENT_POINTS.name).SetTargets([attach.GetPath()])
    _surface_gripper.acquire_surface_gripper_interface().set_write_to_usd(True)
    view = GripperView(
        paths=path,
        max_grip_distance=[0.02],
        coaxial_force_limit=[100.0],
        shear_force_limit=[100.0],
        retry_interval=[1.0],
    )
    return {
        "backend": "native surface gripper",
        "view": view,
        "target": target,
        "body": body,
        "drive": drive.GetTargetPositionAttr(),
        "status_type": _surface_gripper.GripperStatus,
    }


def read_gripper(gripper: dict[str, Any], rigid_view: Any) -> dict[str, Any]:
    """Read the actual held object paths and PhysX cube height."""
    if "view" not in gripper:
        return {
            "backend": gripper["backend"],
            "status": "Closed" if gripper["held"] else "Open",
            "gripped_objects": [gripper["target"]] if gripper["held"] else [],
            "target_pose_xyzw": rigid_view.get_transforms().numpy()[0].tolist(),
        }
    view = gripper["view"]
    return {
        "backend": gripper["backend"],
        "status": gripper["status_type"](view.get_surface_gripper_status()[0]).name,
        "gripped_objects": list(view.get_gripped_objects()[0]),
        "target_pose_xyzw": rigid_view.get_transforms().numpy()[0].tolist(),
    }


def close_constraint_gripper(stage: Any, gripper: dict[str, Any], body_view: Any, target_view: Any) -> None:
    """Create a real PhysX attachment only after a near-surface collision query hits."""
    import omni.physx
    from pxr import Gf, UsdPhysics

    body, target = body_view.get_transforms().numpy()[0], target_view.get_transforms().numpy()[0]
    origin = [float(body[0]), float(body[1]), float(body[2]) - 0.021]
    hit = omni.physx.get_physx_scene_query_interface().raycast_closest(origin, [0, 0, -1], 0.02)
    if not hit.get("hit") or hit.get("rigidBody") != gripper["target"]:
        raise RuntimeError("No target surface within the 2 cm grasp range")
    joint = UsdPhysics.FixedJoint.Define(stage, "/World/Grasp/CustomAttachment")
    joint.CreateBody0Rel().SetTargets([gripper["body"]])
    joint.CreateBody1Rel().SetTargets([gripper["target"]])
    # The prismatic demonstration constrains body orientation to identity.
    joint.CreateLocalPos0Attr(Gf.Vec3f(*[float(v) for v in target[:3] - body[:3]]))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0))
    joint.CreateLocalRot0Attr(Gf.Quatf(float(target[6]), Gf.Vec3f(*[float(v) for v in target[3:6]])))
    joint.CreateLocalRot1Attr(Gf.Quatf(1, Gf.Vec3f(0)))
    joint.CreateBreakForceAttr(100.0)
    joint.CreateBreakTorqueAttr(100.0)
    gripper["held"] = True
