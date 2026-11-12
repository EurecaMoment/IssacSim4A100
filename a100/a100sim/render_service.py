"""Small persistent CUDA render worker for a fixed SAM3D asset.

The worker loads mesh and Gaussian data once, then serializes renders on one
GPU. The HTTP queue is bounded so callers get an explicit overload response.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class RenderState:
    def __init__(self, asset: Path, output: Path, size: int, queue_size: int):
        self.asset = asset
        self.output = output
        self.size = size
        self.jobs: queue.Queue[dict | None] = queue.Queue(maxsize=queue_size)
        self.results: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.started = time.time()
        self.completed = 0
        self.failed = 0
        self.renderers = {}

    def worker(self) -> None:
        from .renderer import Renderer

        self.output.mkdir(parents=True, exist_ok=True)
        self.renderers = {
            "mesh": Renderer(self.asset, "mesh", self.size, self.size),
            "gaussian": Renderer(self.asset, "gaussian", self.size, self.size),
        }
        while True:
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                return
            job_id = job["id"]
            try:
                path = self.output / f"{job_id}.png"
                result = self.renderers[job["kind"]].render(
                    spp=job["spp"], seed=job["seed"], azimuth=job["azimuth"],
                    elevation=job["elevation"], distance=job["distance"], output=path,
                )
                result.update(id=job_id, state="succeeded", image=str(path),
                              finished_at=time.time())
                with self.lock:
                    self.completed += 1
                    self.results[job_id] = result
            except Exception as exc:  # Keep the worker alive after a bad request.
                with self.lock:
                    self.failed += 1
                    self.results[job_id] = {"id": job_id, "state": "failed",
                                            "error": str(exc)[:500]}
            finally:
                self.jobs.task_done()

    def submit(self, value: dict) -> dict:
        kind = value.get("kind", "mesh")
        if kind not in self.renderers:
            raise ValueError("kind must be mesh or gaussian")
        job = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "spp": max(1, min(int(value.get("spp", 8)), 256)),
            "seed": int(value.get("seed", 42)),
            "azimuth": float(value.get("azimuth", 0)),
            "elevation": max(-80.0, min(float(value.get("elevation", 12)), 80.0)),
            "distance": max(.2, min(float(value.get("distance", 2.15)), 20.0)),
        }
        self.jobs.put_nowait(job)
        with self.lock:
            self.results[job["id"]] = {"id": job["id"], "state": "queued",
                                        "kind": kind, "queued_at": time.time()}
        return self.results[job["id"]]

    def snapshot(self) -> dict:
        with self.lock:
            return {"status": "ready", "uptime_seconds": round(time.time() - self.started, 1),
                    "queue_depth": self.jobs.qsize(), "queue_capacity": self.jobs.maxsize,
                    "completed": self.completed, "failed": self.failed,
                    "loaded_kinds": sorted(self.renderers), "cuda_required": True}


def serve(state: RenderState, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def reply(self, value: dict, status=200):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self.reply(state.snapshot())
                return
            if self.path.startswith("/jobs/"):
                job_id = self.path.rsplit("/", 1)[-1]
                with state.lock:
                    value = state.results.get(job_id)
                self.reply(value or {"error": "job not found"}, 200 if value else 404)
                return
            self.reply({"error": "use /health, /jobs/<id>, or POST /render"}, 404)

        def do_POST(self):
            if self.path != "/render":
                self.reply({"error": "use POST /render"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16 * 1024:
                    raise ValueError("invalid request body")
                value = json.loads(self.rfile.read(length))
                result = state.submit(value)
                self.reply(result, 202)
            except queue.Full:
                self.reply({"error": "render queue full", "retry_after_seconds": 1}, 429)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.reply({"error": str(exc)}, 400)

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--queue-size", type=int, default=64)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8320)
    args = parser.parse_args()
    state = RenderState(args.asset, args.output, args.size, args.queue_size)
    thread = threading.Thread(target=state.worker, daemon=True)
    thread.start()
    while thread.is_alive() and not state.renderers:
        thread.join(.1)
    if not state.renderers:
        raise RuntimeError("CUDA renderer initialization failed; inspect service log")
    serve(state, args.host, args.port)


if __name__ == "__main__":
    main()
