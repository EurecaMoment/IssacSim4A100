import argparse
import json
import os
import time
from pathlib import Path
from a100sim.renderer import Renderer

p = argparse.ArgumentParser()
p.add_argument("asset", type=Path)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--size", type=int, default=256)
p.add_argument("--spp", type=int, default=8)
p.add_argument("--azimuths", type=float, nargs="+", default=[0, 45, 180, 270])
p.add_argument("--kind", choices=["mesh", "gaussian", "both"], default="both")
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
report = {"physical_gpu": os.environ.get("CUDA_VISIBLE_DEVICES"), "renders": []}
for kind in (["mesh", "gaussian"] if args.kind == "both" else [args.kind]):
    t = time.perf_counter()
    renderer = Renderer(args.asset, kind, args.size, args.size)
    print("ASSET_READY", kind, renderer.elements, "load_seconds", time.perf_counter() - t, flush=True)
    for azimuth in args.azimuths:
        output = args.output / f"{kind}-{int(azimuth):03d}.png"
        result = renderer.render(spp=args.spp, azimuth=azimuth, output=output)
        report["renders"].append(result)
        (args.output / "render_report.json").write_text(json.dumps(report, indent=2))
        print("RENDER", json.dumps(result), flush=True)
    del renderer
