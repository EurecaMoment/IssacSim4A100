"""Check material fidelity with actual GLB, USD and renderer material data structures."""

from __future__ import annotations

import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path

from a100sim.materials import principled_material, validate_material


class MaterialContractTests(unittest.TestCase):
    """Keep explicit zeroes, invalid numbers and unsupported channels unambiguous."""

    def test_material_rejects_unsupported_channels_and_preserves_explicit_zero(self) -> None:
        material = principled_material(channels=("metallic", "roughness"), overrides={"metallic": 0, "roughness": 0})
        self.assertEqual(material["material"]["metallic"], 0)
        self.assertEqual(material["material"]["roughness"], 0)
        for value in (
            {"normal": [0, 0, 1]},
            {"metallic": True},
            {"roughness": -0.1},
            {"base_color": [1, 2, 0]},
            {"metallic": float("nan")},
            {"base_color": "red"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_material(value)


@unittest.skipUnless(
    all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "PIL")),
    "Install requirements-assets.txt in a separate test environment",
)
class AssetPbrTests(unittest.TestCase):
    """Check glTF channel order, transfer functions, GLB conversion and material warnings."""

    def setUp(self) -> None:
        """Load the same conversion implementation used by the isolated RPC workflow."""
        path = Path(__file__).resolve().parents[1] / "examples/prepare_fasttrellis_assets.py"
        spec = importlib.util.spec_from_file_location("asset_conversion", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_texture_baking_preserves_linear_metallic_roughness_and_color_factors(self) -> None:
        import numpy as np
        import trimesh
        from PIL import Image

        image = Image.fromarray(np.array([[[0, 64, 192]]], np.uint8))
        base = Image.fromarray(np.array([[[128, 64, 255]]], np.uint8))
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=base,
            baseColorFactor=[128, 255, 255, 255],
            metallicRoughnessTexture=image,
            metallicFactor=0.5,
            roughnessFactor=0.25,
        )
        mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
        mesh.visual = trimesh.visual.TextureVisuals(uv=np.array([[0.5, 0.5]] * 3), material=material)
        result = self.module.bake_pbr(mesh)
        np.testing.assert_allclose(result["metallic"], (192 / 255) * 0.5, atol=1e-7)
        np.testing.assert_allclose(result["roughness"], (64 / 255) * 0.25, atol=1e-7)
        expected = self.module.decode_srgb(np.array([128, 64, 255]) / 255) * [128 / 255, 1, 1]
        np.testing.assert_allclose(result["colors"], np.tile(expected, (3, 1)), atol=1e-6)

    def test_base_color_interpolates_after_decoding_srgb(self) -> None:
        import numpy as np
        from PIL import Image

        image = Image.fromarray(np.array([[[0, 0, 0], [255, 255, 255]]], np.uint8))
        value = self.module.sample_texture(image, np.array([[0.5, 0.5]]), srgb=True)
        np.testing.assert_allclose(value, [[0.5, 0.5, 0.5]])

    def test_glb_conversion_retains_channels_and_reports_unrepresented_inputs(self) -> None:
        import numpy as np
        import trimesh
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mesh = trimesh.creation.box()
            image = Image.fromarray(np.array([[[0, 80, 200]]], np.uint8))
            material = trimesh.visual.material.PBRMaterial(
                baseColorFactor=[255, 128, 64, 255],
                metallicFactor=0.4,
                roughnessFactor=0.7,
                metallicRoughnessTexture=image,
                normalTexture=image,
            )
            mesh.visual = trimesh.visual.TextureVisuals(uv=np.full((len(mesh.vertices), 2), 0.5), material=material)
            source = root / "pbr.glb"
            source.write_bytes(mesh.export(file_type="glb"))
            output = root / "converted"
            report = self.module.convert_asset(source, output, "z", 1.2, "test")
            with np.load(output / "mesh.npz", allow_pickle=False) as data:
                np.testing.assert_allclose(data["metallic"], (200 / 255) * 0.4, atol=1e-6)
                np.testing.assert_allclose(data["roughness"], (80 / 255) * 0.7, atol=1e-6)
                self.assertEqual(len(data["faces"]), len(mesh.faces))
                self.assertAlmostEqual(float(np.ptp(data["vertices"], axis=0).max()), 1.2, places=6)
                self.assertAlmostEqual(float(data["vertices"][:, 2].min()), 0)
            self.assertTrue(any("normalTexture" in warning for warning in report["material_warnings"]))
            with self.assertRaisesRegex(ValueError, "normalTexture"):
                self.module.convert_asset(source, root / "strict", "z", 1.2, "strict", True)
            self.assertFalse((root / "strict").exists())

    def test_external_glb_resources_are_rejected_before_asset_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "external.glb"
            contents = json.dumps({"images": [{"uri": "https://example.invalid/texture.png"}]}).encode()
            contents += b" " * (-len(contents) % 4)
            source.write_bytes(
                struct.pack("<5I", 0x46546C67, 2, 20 + len(contents), len(contents), 0x4E4F534A) + contents
            )
            with self.assertRaisesRegex(ValueError, "embedded"):
                self.module.inspect_material_limits(source)

    def test_npz_material_channel_validation_rejects_corrupt_data(self) -> None:
        import numpy as np

        from a100sim.materials import read_mesh_channels

        for data in (
            {"roughness": np.array([0.1])},
            {"metallic": np.array([0, float("nan"), 1])},
            {"metallic": np.array([0, 1, 2])},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_mesh_channels(data, 3)

    def test_mesh_colors_respect_false_flags_and_reject_ambiguous_values(self) -> None:
        import numpy as np

        from a100sim.materials import read_mesh_colors

        colors = np.array([[0.5, 0.04, 1.0]], np.float32)
        expected = self.module.decode_srgb(colors)
        for flag in (None, False, np.array(False)):
            data = {"colors": colors, **({"colors_linear": flag} if flag is not None else {})}
            np.testing.assert_allclose(read_mesh_colors(data, 1), expected, atol=1e-7)
        np.testing.assert_array_equal(read_mesh_colors({"colors": colors, "colors_linear": True}, 1), colors)
        for flag in ("false", 0, [True, False]):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "boolean"):
                read_mesh_colors({"colors": colors, "colors_linear": flag}, 1)
        with self.assertRaisesRegex(ValueError, "finite"):
            read_mesh_colors({"colors": [[float("nan"), 0, 1]]}, 1)


@unittest.skipUnless(
    importlib.util.find_spec("pxr") and importlib.util.find_spec("numpy"),
    "Install usd-core and numpy in a separate test environment",
)
class UsdPbrTests(unittest.TestCase):
    """Round-trip standard PreviewSurface bindings independently of the GPU runtime."""

    def test_usd_round_trip_preserves_primvar_channels_and_override_precedence(self) -> None:
        import numpy as np
        from pxr import Usd, UsdGeom

        from a100sim.materials import author_material, read_usd_material

        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, "/World/Mesh")
        mesh.CreateDisplayColorPrimvar("vertex").Set([(0.5, 0.2, 0.1)] * 3)
        author_material(mesh, {"metallic": 0}, {"roughness": np.array([0.1, 0.5, 0.9], np.float32)})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "material.usda"
            stage.GetRootLayer().Export(str(path))
            reopened = Usd.Stage.Open(str(path))
            material = read_usd_material(
                reopened.GetPrimAtPath("/World/Mesh"), vertex_color=True, channels=("roughness",)
            )
            self.assertEqual(material["material"]["metallic"], 0)
            self.assertEqual(material["material"]["roughness"]["name"], "vertex_roughness")
            self.assertEqual(material["material"]["base_color"]["name"], "vertex_color")

    def test_unsupported_usd_material_inputs_fail_explicitly(self) -> None:
        from pxr import Sdf, Usd, UsdGeom, UsdShade

        from a100sim.materials import author_material, read_usd_material

        stage = Usd.Stage.CreateInMemory()
        visual = UsdGeom.Cube.Define(stage, "/Cube")
        author_material(visual, {"base_color": [0.1, 0.2, 0.3]})
        shader = UsdShade.Shader.Get(stage, "/Cube/Material/Surface")
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.5)
        with self.assertRaisesRegex(ValueError, "opacity"):
            read_usd_material(visual.GetPrim())
        shader.GetInput("opacity").Set(1)
        shader.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).Set((0, 1, 0))
        with self.assertRaisesRegex(ValueError, "normal"):
            read_usd_material(visual.GetPrim())


@unittest.skipUnless(
    importlib.util.find_spec("mitsuba") and importlib.util.find_spec("numpy"),
    "Install Mitsuba in a separate test environment for material plugin checks",
)
class MitsubaMaterialTests(unittest.TestCase):
    """Test scalar material interpolation only; this is not CUDA rendering acceptance."""

    def test_baked_channels_interpolate_at_the_actual_surface_hit(self) -> None:
        import mitsuba as mi
        import numpy as np

        mi.set_variant("scalar_rgb")
        props = mi.Properties()
        props["bsdf"] = mi.load_dict(principled_material(channels=("metallic", "roughness")))
        mesh = mi.Mesh("pbr_fixture", 3, 1, props)
        mesh.add_attribute("vertex_color", 3, np.tile([0.5, 0.25, 0.1], 3).astype(np.float32))
        mesh.add_attribute("vertex_metallic", 1, np.array([0, 0.4, 0.8], np.float32))
        mesh.add_attribute("vertex_roughness", 1, np.array([0.2, 0.6, 1.0], np.float32))
        parameters = mi.traverse(mesh)
        parameters["vertex_positions"] = np.array([0, 0, 0, 1, 0, 0, 0, 1, 0], np.float32)
        parameters["faces"] = np.array([0, 1, 2], np.uint32)
        parameters.update()
        scene = mi.load_dict({"type": "scene", "mesh": mesh})
        hit = scene.ray_intersect(mi.Ray3f(o=[0.25, 0.25, 1], d=[0, 0, -1]))
        self.assertTrue(hit.is_valid())
        for channel, expected in (("metallic", 0.3), ("roughness", 0.5)):
            texture = mi.load_dict({"type": "mesh_attribute", "name": "vertex_" + channel})
            self.assertAlmostEqual(float(texture.eval_1(hit)), expected, places=6)
        response = mesh.bsdf().eval(mi.BSDFContext(), hit, mi.Vector3f(0, 0, 1))
        self.assertTrue(np.isfinite(np.array(response)).all())


if __name__ == "__main__":
    unittest.main()
