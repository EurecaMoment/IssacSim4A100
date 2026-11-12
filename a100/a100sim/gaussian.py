"""Load Gaussian PLY/NPZ without replacing the Gaussians with a triangle mesh."""
from pathlib import Path
import numpy as np


def load_gaussians(path):
    path = Path(path)
    if path.suffix.lower() == ".ply":
        from plyfile import PlyData
        data = PlyData.read(str(path))["vertex"].data
        names = set(data.dtype.names)
        means = np.stack([data[x] for x in ("x", "y", "z")], axis=1)
        scales = np.exp(np.stack([data[f"scale_{i}"] for i in range(3)], axis=1))
        quats = np.stack([data[f"rot_{i}"] for i in range(4)], axis=1)
        opacity = 1 / (1 + np.exp(-np.clip(data["opacity"], -80, 80)))
        dc = np.stack([data[f"f_dc_{i}"] for i in range(3)], axis=1)[:, None, :]
        rest_names = sorted((x for x in names if x.startswith("f_rest_")),
                            key=lambda x: int(x.rsplit("_", 1)[1]))
        if rest_names:
            rest = np.stack([data[x] for x in rest_names], axis=1)
            rest = rest.reshape(len(means), 3, -1).transpose(0, 2, 1)
            sh = np.concatenate([dc, rest], axis=1)
        else:
            sh = dc
    elif path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as data:
            def get(*keys):
                for key in keys:
                    if key in data:
                        return np.asarray(data[key], dtype=np.float32)
                raise ValueError(f"Missing Gaussian field: {keys}")
            means = get("means", "centers", "xyz")
            if "log_scales" in data:
                scales = np.exp(data["log_scales"])
            else:
                scales = get("scales")
            quats = get("quats", "quaternions", "rotations")
            if "opacity_logits" in data:
                opacity = 1 / (1 + np.exp(-np.clip(data["opacity_logits"], -80, 80)))
            else:
                opacity = get("opacities", "opacity")
            if "sh_coeffs" in data:
                sh = get("sh_coeffs")
            elif "sh0" in data:
                sh = get("sh0").reshape(len(means), 1, 3)
                if "shN" in data:
                    sh = np.concatenate([sh, get("shN").reshape(len(means), -1, 3)], axis=1)
            else:
                sh = ((get("colors", "rgb") - 0.5) / 0.28209479177387814)[:, None, :]
    else:
        raise ValueError("Gaussian asset must be a standard 3DGS PLY or NPZ")
    means = np.asarray(means, np.float32)
    scales = np.asarray(scales, np.float32)
    quats = np.asarray(quats, np.float32)
    opacity = np.asarray(opacity, np.float32).reshape(-1, 1)
    sh = np.asarray(sh, np.float32).reshape(len(means), -1)
    n = len(means)
    if means.shape != (n, 3) or scales.shape != (n, 3) or quats.shape != (n, 4):
        raise ValueError("Invalid Gaussian geometry dimensions")
    if len(opacity) != n or not n or np.any(scales <= 0):
        raise ValueError("Invalid Gaussian count, opacity or scales")
    if np.any(opacity < 0) or np.any(opacity > 1):
        raise ValueError("NPZ opacities must be probabilities in [0, 1]")
    count = sh.shape[1] // 3
    if sh.shape[1] % 3 or int(np.sqrt(count)) ** 2 != count:
        raise ValueError("SH coefficients must have shape N x ((degree+1)^2*3)")
    norm = np.linalg.norm(quats, axis=1, keepdims=True)
    if np.any(norm < 1e-8):
        raise ValueError("Invalid zero Gaussian quaternion")
    quats /= norm
    values = {"means": means, "scales": scales, "quats": quats,
              "opacities": opacity, "sh_coeffs": sh}
    if any(not np.isfinite(v).all() for v in values.values()):
        raise ValueError("Gaussian asset contains nonfinite values")
    return values


def transform_gaussians(data, matrix):
    """Apply a USD affine transform to Gaussian means and covariance, on upload.

    Mitsuba's tensor ellipsoid constructor does not implement to_world. A rigid
    transform preserves the scales and composes wxyz quaternions directly.
    """
    matrix = np.asarray(matrix, np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.allclose(
            matrix[3], [0, 0, 0, 1]):
        raise ValueError("Gaussian transform must be a finite affine 4x4 matrix")
    linear = matrix[:3, :3]
    scales = np.linalg.norm(linear, axis=0)
    if np.any(scales < 1e-9):
        raise ValueError("Gaussian transform is singular")
    rotation = linear / scales
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or not np.allclose(
            scales, scales[0], atol=1e-5) or np.linalg.det(rotation) < 0:
        raise ValueError("Gaussian adapter currently supports rigid transforms and positive uniform scale")
    # Stable rotation matrix -> wxyz quaternion without requiring SciPy.
    trace = np.trace(rotation)
    if trace > 0:
        s = np.sqrt(trace + 1) * 2
        q = np.array([.25 * s, (rotation[2, 1] - rotation[1, 2]) / s,
                      (rotation[0, 2] - rotation[2, 0]) / s,
                      (rotation[1, 0] - rotation[0, 1]) / s])
    else:
        i = int(np.argmax(np.diag(rotation)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = np.sqrt(1 + rotation[i, i] - rotation[j, j] - rotation[k, k]) * 2
        q = np.empty(4)
        q[0] = (rotation[k, j] - rotation[j, k]) / s
        q[i + 1] = .25 * s
        q[j + 1] = (rotation[j, i] + rotation[i, j]) / s
        q[k + 1] = (rotation[k, i] + rotation[i, k]) / s
    original = data["quats"]
    aw, av = q[0], q[1:]
    bw, bv = original[:, :1], original[:, 1:]
    quat = np.concatenate([aw * bw - np.sum(av * bv, axis=1, keepdims=True),
                           aw * bv + bw * av + np.cross(av, bv)], axis=1)
    quat /= np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-10)
    return {**data, "means": (data["means"] @ linear.T + matrix[:3, 3]).astype(np.float32),
            "scales": (data["scales"] * scales[0]).astype(np.float32),
            "quats": quat.astype(np.float32)}


def shape_dict(data, matrix=None):
    import mitsuba as mi
    if matrix is not None:
        data = transform_gaussians(data, matrix)
    result = {"type": "ellipsoids", "extent": 3.0,
              "centers": mi.TensorXf(data["means"]),
              "scales": mi.TensorXf(data["scales"]),
              # 3DGS files use wxyz; Dr.Jit's Quaternion4f storage is xyzw.
              "quaternions": mi.TensorXf(data["quats"][:, [1, 2, 3, 0]]),
              "opacities": mi.TensorXf(data["opacities"]),
              "sh_coeffs": mi.TensorXf(data["sh_coeffs"])}
    return result
