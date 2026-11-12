"""Bounded, durable loopback job queues shared by the A100 GPU services."""
from __future__ import annotations

import json
import math
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

JOB_ID = re.compile(r"^[a-f0-9]{32}$")
ASSET_ROOT = Path(os.environ.get("A100SIM_ASSET_ROOT", Path(__file__).resolve().parents[1] / "assets")).resolve()


def finite(value, name, low, high):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def integer(value, name, low, high):
    number = finite(value, name, low, high)
    if number != int(number):
        raise ValueError(f"{name} must be an integer")
    return int(number)


def asset_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("asset must be an absolute server directory")
    path = path.resolve()
    if not path.is_relative_to(ASSET_ROOT) or not path.is_dir():
        raise ValueError(f"asset must be an existing directory under {ASSET_ROOT}")
    return path


def persist(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class JobQueue:
    def __init__(self, output, capacity=64, engine="A100 CUDA"):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.jobs = queue.Queue(maxsize=capacity)
        self.lock = threading.RLock()
        self.ready = False
        self.error = None
        self.engine = engine
        self.started = time.time()
        self.active = None
        self.completed = 0
        self.failed = 0
        self.details = {}
        # A lost process must never cause automatic duplicate simulation.
        for path in self.output.glob("*/job.json"):
            try:
                record = json.loads(path.read_text())
                if record.get("state") in {"queued", "running"}:
                    record.update(state="interrupted", error="Service restarted; job was not retried",
                                  finished_at=time.time())
                    persist(path, record)
            except (OSError, ValueError):
                continue

    def prepare(self, value):
        return value

    def status(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("job_id must be 32 hexadecimal characters")
        with self.lock:
            path = self.output / job_id / "job.json"
            return json.loads(path.read_text()) if path.is_file() else None

    def submit(self, value):
        if not self.ready:
            raise RuntimeError("GPU service is initializing")
        if not isinstance(value, dict):
            raise ValueError("request must be a JSON object")
        payload = self.prepare(value)
        job_id = uuid.uuid4().hex
        directory = self.output / job_id
        record = {"id": job_id, "state": "queued", "queued_at": time.time()}
        with self.lock:
            if self.jobs.full():
                raise queue.Full()
            directory.mkdir()
            persist(directory / "request.json", payload)
            persist(directory / "job.json", record)
            self.jobs.put_nowait((job_id, payload))
        return record.copy()

    def process(self, payload, directory):
        raise NotImplementedError

    def run_one(self, item):
        job_id, payload = item
        directory = self.output / job_id
        started = time.time()
        with self.lock:
            record = self.status(job_id)
            record.update(state="running", started_at=started,
                          queue_seconds=started - record["queued_at"])
            persist(directory / "job.json", record)
            self.active = job_id
        try:
            result = self.process(payload, directory)
            record.update(result)
            record.update(id=job_id, state="succeeded")
            self.completed += 1
        except Exception as exc:
            record.update(id=job_id, state="failed", error=str(exc)[:1000])
            self.failed += 1
            print(f"JOB_FAILED {job_id}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            record.update(finished_at=time.time(), execution_seconds=time.time() - started)
            with self.lock:
                persist(directory / "job.json", record)
                self.active = None
            self.jobs.task_done()

    def work_forever(self):
        while True:
            self.run_one(self.jobs.get())

    def health(self):
        with self.lock:
            return {"status": "ready" if self.ready else "failed" if self.error else "initializing",
                    "engine": self.engine, "physical_gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
                    "cuda_required": True, "cpu_fallback": False, "native_rtx": False,
                    "uptime_seconds": round(time.time() - self.started, 2),
                    "queue_depth": self.jobs.qsize(), "queue_capacity": self.jobs.maxsize,
                    "active_job": self.active, "completed": self.completed, "failed": self.failed,
                    **self.details, **({"error": self.error} if self.error else {})}


def start_http(state, host, port, submit_route):
    class Server(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def reply(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == "/health":
                self.reply(state.health(), 200 if state.ready else 503)
                return
            if self.path.startswith("/jobs/"):
                try:
                    value = state.status(self.path[len("/jobs/"):])
                    self.reply(value or {"error": "job not found"}, 200 if value else 404)
                except ValueError as exc:
                    self.reply({"error": str(exc)}, 400)
                return
            self.reply({"error": f"Use /health, /jobs/<id>, or POST {submit_route}"}, 404)

        def do_POST(self):
            if self.path != submit_route:
                self.reply({"error": f"Use POST {submit_route}"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256 * 1024:
                    raise ValueError("JSON body must be between 1 and 262144 bytes")
                value = json.loads(self.rfile.read(length))
                self.reply(state.submit(value), 202)
            except queue.Full:
                self.reply({"error": "GPU queue is full; no job was submitted"}, 429)
            except (ValueError, TypeError, KeyError) as exc:
                self.reply({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self.reply({"error": str(exc)}, 503)

    server = Server((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name=f"http-{port}")
    thread.start()
    return server
