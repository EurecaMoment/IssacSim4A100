"""Share the metallic/roughness material contract across USD and CUDA renderers."""

from __future__ import annotations

import math
from typing import Any

MATERIAL_CHANNELS = ("metallic", "roughness")


def validate_material(value: Any) -> dict[str, Any]:
    """Validate explicit linear RGB and scalar PBR overrides without adding defaults."""
    if not isinstance(value, dict) or set(value) - {"base_color", *MATERIAL_CHANNELS}:
        raise ValueError("material accepts base_color, metallic and roughness only")
    result: dict[str, Any] = {}
    for name, channel in value.items():
        numbers = channel if name == "base_color" else [channel]
        if not isinstance(numbers, (list, tuple)) or (name == "base_color" and len(numbers) != 3):
            raise ValueError("material.base_color requires three linear RGB values")
        checked = []
        for number in numbers:
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"material.{name} must be numeric")
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError(f"material.{name} must be finite and between 0 and 1")
            checked.append(float(number))
        result[name] = checked if name == "base_color" else checked[0]
    return result


def principled_material(
    *, vertex_color: bool = True, channels: tuple[str, ...] = (), overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a Mitsuba material dictionary; importing this module needs no renderer."""
    values = validate_material(overrides or {})
    result: dict[str, Any] = {
        "type": "principled",
        "base_color": (
            {"type": "mesh_attribute", "name": "vertex_color"}
            if vertex_color and "base_color" not in values
            else {"type": "rgb", "value": values.get("base_color", [0.32, 0.34, 0.37])}
        ),
    }
    for name, default in (("metallic", 0.0), ("roughness", 0.85)):
        result[name] = (
            {"type": "mesh_attribute", "name": "vertex_" + name}
            if name in channels and name not in values
            else values.get(name, default)
        )
    return {"type": "twosided", "material": result}


def read_mesh_channels(data: Any, count: int) -> dict[str, Any]:
    """Read optional per-vertex channels, rejecting corrupt or incompatible NPZ data."""
    import numpy as np

    result = {}
    for name in MATERIAL_CHANNELS:
        if name not in data:
            continue
        channel = np.asarray(data[name], dtype=np.float32)
        if channel.shape != (count,) or not np.isfinite(channel).all() or np.any((channel < 0) | (channel > 1)):
            raise ValueError(f"Mesh {name} requires one finite value in [0, 1] per vertex")
        result[name] = channel
    return result


def read_mesh_colors(data: Any, count: int) -> Any:
    """Read validated vertex RGB, decoding sRGB when the linear flag is absent or false."""
    import numpy as np

    colors = np.asarray(data["colors"], dtype=np.float32)
    if colors.shape != (count, 3) or not np.isfinite(colors).all() or np.any((colors < 0) | (colors > 1)):
        raise ValueError("Mesh colors require three finite values in [0, 1] per vertex")
    flag = np.asarray(data.get("colors_linear", False))
    if flag.size != 1 or flag.dtype.kind != "b":
        raise ValueError("Mesh colors_linear must contain one boolean")
    if not flag.item():
        colors = np.where(colors <= 0.04045, colors / 12.92, ((colors + 0.055) / 1.055) ** 2.4).astype(np.float32)
    return colors


def author_material(visual: Any, overrides: dict[str, Any], channels: dict[str, Any] | None = None) -> None:
    """Bind portable UsdPreviewSurface constants or standard primvar reader connections."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

    values = validate_material(overrides)
    channels = channels or {}
    prim = visual.GetPrim()
    path = str(prim.GetPath()) + "/Material"
    stage = prim.GetStage()
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(0)
    color = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    if "base_color" in values:
        color.Set(Gf.Vec3f(*values["base_color"]))
    elif prim.IsA(UsdGeom.Mesh):
        reader = UsdShade.Shader.Define(stage, path + "/Color")
        reader.CreateIdAttr("UsdPrimvarReader_float3")
        reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("displayColor")
        color.ConnectToSource(reader.CreateOutput("result", Sdf.ValueTypeNames.Float3))
    else:
        colors = UsdGeom.Gprim(prim).GetDisplayColorAttr().Get()
        color.Set(colors[0] if colors else Gf.Vec3f(0.32, 0.34, 0.37))
    for name in MATERIAL_CHANNELS:
        socket = shader.CreateInput(name, Sdf.ValueTypeNames.Float)
        if name in values:
            socket.Set(values[name])
        elif name in channels:
            attribute = UsdGeom.PrimvarsAPI(prim).CreatePrimvar(
                "a100sim:" + name, Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex
            )
            attribute.Set(Vt.FloatArray.FromNumpy(channels[name]))
            reader = UsdShade.Shader.Define(stage, path + "/" + name.title())
            reader.CreateIdAttr("UsdPrimvarReader_float")
            reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("a100sim:" + name)
            socket.ConnectToSource(reader.CreateOutput("result", Sdf.ValueTypeNames.Float))
        else:
            socket.Set(0.85 if name == "roughness" else 0.0)
    material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput("surface", Sdf.ValueTypeNames.Token))
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


def read_usd_material(prim: Any, *, vertex_color: bool = False, channels: tuple[str, ...] = ()) -> dict[str, Any]:
    """Read the supported PreviewSurface subset, explicitly rejecting unknown shader networks."""
    import numpy as np
    from pxr import UsdGeom, UsdShade

    display = UsdGeom.Gprim(prim).GetDisplayColorAttr().Get() if prim.IsA(UsdGeom.Gprim) else None
    default = list(display[0]) if display else [0.32, 0.34, 0.37]
    overrides = {} if vertex_color else {"base_color": default}
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
        shader = material.ComputeSurfaceSource()[0]
        if not shader or shader.GetIdAttr().Get() != "UsdPreviewSurface":
            raise ValueError(f"Unsupported USD material on {prim.GetPath()}")
        for name in ("diffuseColor", "roughness", "metallic"):
            value = shader.GetInput(name)
            if value and value.HasConnectedSource():
                source = value.GetConnectedSource()[0]
                reader = UsdShade.Shader(source.GetPrim())
                expected = "displayColor" if name == "diffuseColor" else "a100sim:" + name
                reader_id = "UsdPrimvarReader_float3" if name == "diffuseColor" else "UsdPrimvarReader_float"
                available = vertex_color if name == "diffuseColor" else name in channels
                if (
                    not available
                    or reader.GetIdAttr().Get() != reader_id
                    or reader.GetInput("varname").Get() != expected
                ):
                    raise ValueError(f"Unsupported USD texture network: {prim.GetPath()}/{name}")
                continue
            if value and value.Get() is not None:
                overrides["base_color" if name == "diffuseColor" else name] = (
                    list(value.Get()) if name == "diffuseColor" else float(value.Get())
                )
        unsupported = {
            "opacity": 1,
            "opacityThreshold": 0,
            "emissiveColor": [0, 0, 0],
            "normal": [0, 0, 1],
            "clearcoat": 0,
            "displacement": 0,
            "useSpecularWorkflow": 0,
        }
        for name, default_value in unsupported.items():
            value = shader.GetInput(name)
            if value and (
                value.HasConnectedSource() or (value.Get() is not None and not np.allclose(value.Get(), default_value))
            ):
                raise ValueError(f"Unsupported USD material input: {prim.GetPath()}/{name}")
    return principled_material(vertex_color=vertex_color, channels=channels, overrides=overrides)
