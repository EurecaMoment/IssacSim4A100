"""Resident GPU micro-batch service; generic scenes retain the original backend."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import io
try:
    import orjson
except ImportError:
    orjson = None

def encode_json(value):
    return orjson.dumps(value) if orjson is not None else json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()

import queue
import shutil
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from functools import partial
import multiprocessing
from pathlib import Path

from .physics_service import PhysicsQueue
from .service_queue import JOB_ID

OWNER_TAG = "isaacsim-a100-resident-v1"


def compact_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encode_json(value))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def save_png(path, pixels):
    from PIL import Image
    Image.fromarray(pixels).save(path, compress_level=1)
    os.chmod(path, 0o600)


def export_scene(request, poses, destination):
    """Write an independent, complete USD scene without sharing a mutable stage."""
    def vec(values):
        return '(' + ', '.join(format(float(v), '.9g') for v in values) + ')'
    lines = ['#usda 1.0', '(\ndefaultPrim = "World"\nupAxis = "Z"\nmetersPerUnit = 1\n)',
             'def Xform "World" {', 'def PhysicsScene "PhysicsScene" {',
             'vector3f physics:gravityDirection = (0, 0, -1)',
             f'float physics:gravityMagnitude = {request["gravity"]}', '}',
             'def Cube "Ground" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {',
             'double size = 1', 'double3 xformOp:translate = (0, 0, -0.1)',
             'float3 xformOp:scale = (200, 200, 0.2)',
             'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]', '}',
             'def Scope "Bodies" {']
    for index, (body, pose) in enumerate(zip(request['bodies'], poses)):
        lines += [f'def Xform "B{index:04d}" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {{',
                  'double3 xformOp:translate = ' + vec(pose[:3]),
                  'quatf xformOp:orient = ' + vec([pose[6], *pose[3:6]]),
                  'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient"]',
                  f'float physics:mass = {body["mass"]}',
                  'def Cube "Visual" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {',
                  'double size = 1', 'float3 xformOp:scale = (0.2, 0.2, 0.2)',
                  'uniform token[] xformOpOrder = ["xformOp:scale"]',
                  'color3f[] primvars:displayColor = [' + vec(body['color']) + ']', '}', '}']
    lines += ['}']
    capture = request.get('render')
    if capture is not None:
        import math
        az, el = math.radians(capture['azimuth']), math.radians(capture['elevation'])
        eye = [v + capture['distance'] * d for v, d in zip(capture['target'],
               [math.sin(az)*math.cos(el), -math.cos(az)*math.cos(el), math.sin(el)])]
        lines += ['def Camera "Camera" {', 'float focalLength = 35',
                  f'float horizontalAperture = {2 * 35 * math.tan(math.radians(17))}',
                  f'float verticalAperture = {2 * 35 * math.tan(math.radians(17))}',
                  'double3 xformOp:translate = ' + vec(eye),
                  'float3 xformOp:rotateXYZ = ' + vec([90-capture['elevation'], 0, capture['azimuth']]),
                  'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]', '}',
                  'def DomeLight "Dome" {', 'color3f inputs:color = (0.45, 0.48, 0.52)',
                  'float inputs:intensity = 1', '}',
                  'def RectLight "KeyLight" {', 'float inputs:width = 3.6', 'float inputs:height = 3.6',
                  'float inputs:intensity = 5', 'double3 xformOp:translate = (-2, -3, 4)',
                  'float3 xformOp:rotateXYZ = ' + vec([math.degrees(math.atan2(math.sqrt(13), 3.5)), 0,
                                                        -math.degrees(math.atan2(2, 3))]),
                  'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]', '}']
    lines += ['}']
    destination.write_text('\n'.join(lines), encoding='utf-8')
    os.chmod(destination, 0o600)


def build_artifacts(directory, request, values, timing, image, physics_device, physical_gpu):
    """CPU-only export in a spawned process; GPU simulation stays in its owner."""
    import numpy as np
    directory = Path(directory)
    poses, velocities = values
    bodies = [{'id': body['id'], 'prim_path': f'/World/Bodies/B{i:04d}',
               'pose_xyzw': pose.tolist(), 'velocity': velocity.tolist()}
              for i, (body, pose, velocity) in enumerate(zip(request['bodies'], poses, velocities))]
    artifact_started = time.perf_counter()
    buffer = io.BytesIO()
    np.savez(buffer, poses_xyzw=poses, velocities=velocities)
    (directory / 'state.npz').write_bytes(buffer.getvalue())
    os.chmod(directory / 'state.npz', 0o600)
    npz_finished = time.perf_counter()
    export_scene(request, poses, directory / 'scene.usda')
    scene_finished = time.perf_counter()
    result = {'isaac_sim': '6.2.0', 'physics_device': physics_device,
              'physical_gpu': physical_gpu, 'native_rtx': False,
              'cpu_fallback': False, 'bodies': bodies, 'rigid_bodies': len(bodies),
              'steps': request['steps'], 'dt': request['dt'],
              'simulation_seconds': request['steps'] * request['dt'],
              **timing, 'state_npz': str(directory / 'state.npz'),
              'collision': 'PhysX primitives; independent collision groups per request',
              'scene': str(directory / 'scene.usda'),
              'artifact_npz_seconds': npz_finished-artifact_started,
              'artifact_scene_seconds': scene_finished-npz_finished}
    if image is not None:
        save_png(directory / 'render.png', image)
        result['render'] = {**timing, 'mode':request['render']['kind'], 'image':'render.png'}
        result['image'] = str(directory / 'render.png')
    compact_json(directory / 'simulation.json', result)
    result['artifact_seconds'] = time.perf_counter()-artifact_started
    result['simulation_result'] = str(directory / 'simulation.json')
    return result


class ResidentQueue(PhysicsQueue):
    def __init__(self, output, capacity, generic_url, batch_wait, writers,
                 legacy_output=None, retention_hours=24, max_retained_jobs=4096, artifact_processes=0, batch_idle=.05):
        output = Path(output).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = output / '.service-owner'
        if marker.is_symlink():
            raise RuntimeError('Refusing a symlinked result ownership marker')
        if marker.exists() and marker.read_text(encoding='utf-8').strip() != OWNER_TAG:
            raise RuntimeError('Result directory belongs to another service')
        if not marker.exists():
            if any(output.iterdir()):
                raise RuntimeError('Refusing to claim a nonempty result directory')
            marker.write_text(OWNER_TAG + '\n', encoding='utf-8')
            os.chmod(marker, 0o600)
        os.chmod(output, 0o700)
        super().__init__(output, capacity, 'Isaac Sim 6.2 resident GPU PhysX micro-batches')
        self.generic_url = generic_url.rstrip('/')
        self.legacy_output = Path(legacy_output) if legacy_output else None
        self.batch_wait = batch_wait
        self.batch_idle = batch_idle
        self.retention_hours = retention_hours
        self.max_retained_jobs = max_retained_jobs
        self.retention_stop = threading.Event()
        self._default = super().prepare({})
        self.records = {}
        self.events = {}
        self.async_waiters = {}
        self.active_ids = set()
        self.slots_available = threading.BoundedSemaphore(capacity)
        self.artifact_processes = artifact_processes
        self.writers = (ProcessPoolExecutor(max_workers=artifact_processes, mp_context=multiprocessing.get_context('spawn'))
                        if artifact_processes else ThreadPoolExecutor(max_workers=writers, thread_name_prefix='artifact'))
        self.image_workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix='png')
        self.generic_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='generic')
        self.resident = None
        self.batch_renderer = None
        self.last_idle_step = time.monotonic()
        self.warm_request = None

    def prepare(self, value):
        if not value:
            return self._default
        if 'bodies' not in value:
            prepared = super().prepare({**value, 'bodies':[{'id':'validation'}]})
            prepared['bodies'] = self._default['bodies']
            return prepared
        return super().prepare(value)

    def status(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError('job_id must be 32 hexadecimal characters')
        with self.lock:
            value = self.records.get(job_id)
            if value is not None:
                return value.copy()
        value = super().status(job_id)
        if value is None and self.legacy_output is not None:
            path = self.legacy_output / job_id / 'job.json'
            if path.is_file():
                return json.loads(path.read_text())
        return value

    def submit(self, value):
        queued_at = time.time()
        if not self.ready:
            raise RuntimeError('GPU service is initializing')
        if not isinstance(value, dict):
            raise ValueError('request must be a JSON object')
        payload = self.prepare(value)
        if not self.slots_available.acquire(blocking=False):
            raise queue.Full()
        job_id = uuid.uuid4().hex
        directory = self.output / job_id
        route = 'resident-gpu' if self.resident.accepts(payload) else 'generic-gpu'
        record = {'id': job_id, 'state': 'queued', 'queued_at': queued_at, 'route': route, 'owner': OWNER_TAG}
        try:
            directory.mkdir(mode=0o700)
            compact_json(directory / 'request.json', payload)
            compact_json(directory / 'job.json', record)
            with self.lock:
                self.records[job_id] = record
                self.events[job_id] = threading.Event()
            if route == 'resident-gpu':
                self.jobs.put_nowait((job_id, payload))
            else:
                self.generic_workers.submit(self.run_generic, (job_id, payload))
        except Exception:
            self.slots_available.release()
            raise
        return record.copy()

    def begin(self, job_id):
        now = time.time()
        with self.lock:
            record = self.records[job_id].copy()
            record.update(state='running', started_at=now, queue_seconds=now-record['queued_at'])
            self.records[job_id] = record
            self.active_ids.add(job_id)
        return record

    def finish(self, record, result=None, error=None):
        job_id = record['id']
        if result is not None:
            record.update(result)
        record.update(id=job_id, state='failed' if error else 'succeeded', finished_at=time.time())
        record['execution_seconds'] = record['finished_at'] - record['started_at']
        record['total_seconds'] = record['finished_at'] - record['queued_at']
        if error:
            record['error'] = str(error)[:1000]
            print('JOB_FAILED', job_id, record['error'], flush=True)
        try:
            compact_json(self.output / job_id / 'job.json', record)
            with self.lock:
                self.records[job_id] = record
                self.completed += error is None
                self.failed += error is not None
                self.active_ids.discard(job_id)
                self.events[job_id].set()
                for loop, future in self.async_waiters.pop(job_id, []):
                    def complete(waiter=future):
                        if not waiter.done():
                            waiter.set_result(None)
                    loop.call_soon_threadsafe(complete)
                if len(self.records) > 2048:
                    for old_id in list(self.records)[:512]:
                        if self.records[old_id]['state'] in {'succeeded', 'failed'}:
                            self.records.pop(old_id, None)
                            self.events.pop(old_id, None)
        finally:
            self.slots_available.release()

    def artifacts_done(self, record, future):
        try:
            result = future.result()
        except Exception as exc:
            self.finish(record, error=exc)
        else:
            self.finish(record, result)
        finally:
            self.jobs.task_done()

    def run_generic(self, item, record=None):
        job_id, payload = item
        record = self.begin(job_id) if record is None else record
        def call(path, data=None):
            request = urllib.request.Request(self.generic_url + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        try:
            accepted = call('/simulate', payload)
            upstream_id = accepted['id']
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                value = call('/jobs/' + upstream_id)
                if value['state'] == 'succeeded':
                    value.update(upstream_job_id=upstream_id, route='generic-gpu')
                    value.pop('id', None)
                    value.pop('queued_at', None)
                    value.pop('started_at', None)
                    self.finish(record, value)
                    return
                if value['state'] in {'failed', 'interrupted'}:
                    raise RuntimeError(value.get('error', 'Generic GPU request failed'))
                time.sleep(.01)
            raise TimeoutError('Generic GPU job exceeded 900 seconds; inspect upstream job ' + upstream_id)
        except Exception as exc:
            self.finish(record, error=exc)

    def work_forever(self):
        from collections import deque
        pending = deque()
        while True:
            try:
                first = pending.popleft() if pending else self.jobs.get(timeout=2)
            except queue.Empty:
                # A short heartbeat keeps CUDA/Dr.Jit resident without blocking the next request.
                self.resident.physics.view.step(1 / 120)
                if self.batch_renderer is not None and self.jobs.empty():
                    self.batch_renderer.keep_warm()
                self.last_idle_step = time.monotonic()
                continue
            batch = [first]
            signature = self.resident.signature(first[1])
            deadline = time.monotonic() + self.batch_wait
            idle_deadline = time.monotonic() + self.batch_idle
            while len(batch) < self.resident.environments:
                if pending:
                    candidate = pending.popleft()
                else:
                    try:
                        candidate = self.jobs.get(timeout=max(0, min(deadline, idle_deadline)-time.monotonic()))
                    except queue.Empty:
                        break
                if self.resident.signature(candidate[1]) == signature:
                    batch.append(candidate)
                    idle_deadline = time.monotonic() + self.batch_idle
                else:
                    pending.appendleft(candidate)
                    break
            records = [self.begin(job_id) for job_id, _ in batch]
            requests = [payload for _, payload in batch]
            try:
                results, timing = self.resident.simulate(requests, tensor_step=True)
                rows = list(zip(records, requests, results))
                records, requests, results = [], [], []
                for record, request, values in rows:
                    if not self.resident.result_in_bounds(values):
                        record.update(route='generic-gpu', fallback_reason='State escaped the isolated resident environment')
                        with self.lock:
                            self.records[record['id']] = record
                        self.generic_workers.submit(self.run_generic, (record['id'], request), record)
                        self.jobs.task_done()
                    else:
                        records.append(record)
                        requests.append(request)
                        results.append(values)
                if not requests:
                    continue
                images = [None] * len(requests)
                if requests[0]['render'] is not None:
                    images, render_timing = self.batch_renderer.render(requests, results)
                    timing = {**timing, **render_timing}
                for record, request, values, image in zip(records, requests, results, images):
                    future = self.writers.submit(build_artifacts, str(self.output / record['id']), request, values,
                                                 timing, image, str(self.resident.physics.view.device),
                                                 os.environ.get('CUDA_VISIBLE_DEVICES'))
                    future.add_done_callback(partial(self.artifacts_done, record))
            except Exception as exc:
                for record in records:
                    self.finish(record, error=exc)
                    self.jobs.task_done()

    async def wait_async(self, job_id, timeout):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pair = (loop, future)
        with self.lock:
            value = self.status(job_id)
            if value is None or value['state'] in {'succeeded', 'failed', 'interrupted'}:
                return value
            self.async_waiters.setdefault(job_id, []).append(pair)
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            with self.lock:
                waiters = self.async_waiters.get(job_id, [])
                if pair in waiters:
                    waiters.remove(pair)
                if not waiters:
                    self.async_waiters.pop(job_id, None)
            future.cancel()
        return self.status(job_id)

    def wait(self, job_id, timeout):
        with self.lock:
            event = self.events.get(job_id)
        if event is not None:
            event.wait(timeout)
        return self.status(job_id)

    def prune_results(self, now=None):
        marker = self.output / '.service-owner'
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError('Result ownership marker is missing or unsafe')
        if marker.read_text(encoding='utf-8').strip() != OWNER_TAG:
            raise RuntimeError('Result directory belongs to another service')
        now = time.time() if now is None else now
        cutoff = now - self.retention_hours * 3600
        owned = []
        for job_file in self.output.glob('*/job.json'):
            directory = job_file.parent
            if directory.is_symlink() or job_file.is_symlink():
                continue
            try:
                record = json.loads(job_file.read_text(encoding='utf-8'))
                job_id = record.get('id', '')
                if (record.get('owner') != OWNER_TAG or not JOB_ID.fullmatch(job_id)
                        or job_id != directory.name
                        or record.get('state') not in {'succeeded', 'failed'}):
                    continue
                finished = float(record.get('finished_at', job_file.stat().st_mtime))
            except (OSError, TypeError, ValueError):
                continue
            owned.append((finished, directory, job_id))
        owned.sort(key=lambda item: item[0], reverse=True)
        removed = 0
        for index, (finished, directory, job_id) in enumerate(owned):
            if finished >= cutoff and index < self.max_retained_jobs:
                continue
            if directory.parent != self.output or directory.is_symlink():
                continue
            shutil.rmtree(directory)
            with self.lock:
                self.records.pop(job_id, None)
                self.events.pop(job_id, None)
            removed += 1
        return removed

    def start_retention(self):
        def worker():
            while not self.retention_stop.wait(300):
                try:
                    removed = self.prune_results()
                    if removed:
                        print('RETENTION_PRUNED', removed, flush=True)
                except Exception as exc:
                    print('RETENTION_ERROR', type(exc).__name__, str(exc)[:300], flush=True)
        threading.Thread(target=worker, daemon=True, name='result-retention').start()

    def health(self):
        value = super().health()
        with self.lock:
            value.update(active_jobs=len(self.active_ids),
                         active_job=next(iter(self.active_ids), None),
                         generic_backend=self.generic_url,
                         max_inflight=self.jobs.maxsize,
                         microbatch_wait_ms=round(self.batch_wait * 1000),
                         microbatch_idle_ms=round(self.batch_idle * 1000),
                         result_retention_hours=self.retention_hours,
                         result_retention_max_jobs=self.max_retained_jobs,
                         result_directory=str(self.output),
                         artifact_processes=self.artifact_processes,
                         result_permissions='0700 directories, 0600 files',
                         resident_render=self.batch_renderer is not None,
                         render_profile={'fast_path':{'kind':'mesh','size':256,'max_spp':8,'max_depth':6,'input_bounds_m':90,'output_bounds_m':100.5},
                                         'other_mesh_and_gaussian_scenes':'generic GPU backend'},
                         resident_environments=self.resident.environments if self.resident else 0,
                         resident_rigid_bodies=self.resident.rigids.count if self.resident else 0)
        return value


def start_resident_http(state, host, port):
    from aiohttp import web
    loop = asyncio.new_event_loop()
    started = threading.Event()
    failures = []
    runner = None

    async def handle(request):
        try:
            wait = min(30000, max(0, float(request.query.get('wait_ms', '0')))) / 1000
            path = request.path
            if request.method == 'GET' and path == '/health':
                result, code = state.health(), 200 if state.ready else 503
            elif request.method == 'GET' and path.startswith('/jobs/'):
                job_id = path[len('/jobs/'):]
                if not JOB_ID.fullmatch(job_id):
                    raise ValueError('Invalid job id')
                result = await state.wait_async(job_id, wait) if wait else state.status(job_id)
                result, code = (result, 200) if result else ({'error':'Job not found'}, 404)
            elif request.method == 'POST' and path == '/simulate':
                raw = await request.read()
                if not 0 < len(raw) <= 262144:
                    raise ValueError('JSON body must be between 1 and 262144 bytes')
                value = orjson.loads(raw) if orjson is not None else json.loads(raw)
                # Admission is short and serialized; GPU work runs on the Kit owner thread.
                accepted = state.submit(value)
                result = await state.wait_async(accepted['id'], wait) if wait else accepted
                code = 200 if result['state'] in {'succeeded','failed','interrupted'} else 202
            else:
                result, code = {'error':'Use /health, /jobs/<id>, POST /simulate'}, 404
        except queue.Full:
            result, code = {'error':'GPU queue is full; no job was submitted'}, 429
        except (ValueError, KeyError, TypeError) as exc:
            result, code = {'error':str(exc)}, 400
        except RuntimeError as exc:
            result, code = {'error':str(exc)}, 503
        return web.Response(body=encode_json(result), status=code, content_type='application/json')

    async def serve():
        nonlocal runner
        app = web.Application(client_max_size=262144)
        app.router.add_route('*', '/{tail:.*}', handle)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        await runner.setup()
        await web.TCPSite(runner, host, port, backlog=256).start()

    def run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve())
        except BaseException as exc:
            failures.append(exc)
            started.set()
            return
        started.set()
        loop.run_forever()
        loop.close()

    thread = threading.Thread(target=run, daemon=True, name=f'http-{port}')
    thread.start()
    if not started.wait(15):
        raise TimeoutError('HTTP listener did not initialize')
    if failures:
        raise failures[0]
    class Server:
        def shutdown(self):
            if runner is not None:
                asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
    return Server()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8321)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--queue-size', type=int, default=64)
    parser.add_argument('--batch-wait-ms', type=float, default=300)
    parser.add_argument('--batch-idle-ms', type=float, default=50)
    parser.add_argument('--writers', type=int, default=4)
    parser.add_argument('--artifact-processes', type=int, default=0)
    parser.add_argument('--legacy-output', type=Path)
    parser.add_argument('--retention-hours', type=int, default=24)
    parser.add_argument('--max-retained-jobs', type=int, default=4096)
    parser.add_argument('--generic-url', default='http://127.0.0.1:18323')
    args = parser.parse_args()
    if not 1 <= args.retention_hours <= 168 or not 64 <= args.max_retained_jobs <= 100000:
        parser.error('retention must be 1..168 hours and 64..100000 jobs')
    state = ResidentQueue(args.output, args.queue_size, args.generic_url,
                          args.batch_wait_ms / 1000, args.writers, args.legacy_output,
                          args.retention_hours, args.max_retained_jobs, args.artifact_processes, args.batch_idle_ms/1000)
    server = start_resident_http(state, args.host, args.port)
    from .app import create_app
    from .batch_physics import ResidentPhysics
    app = create_app()
    try:
        state.resident = ResidentPhysics()
        small = state.prepare({'bodies': [{'id':'warmup'}], 'steps':1})
        for _ in range(2):
            state.resident.simulate([small] * 64)
        from .batch_renderer import BatchRenderer
        state.batch_renderer = BatchRenderer()
        render_warm = state.prepare({'render':{'size':256,'spp':8}})
        warm_states, _ = state.resident.simulate([render_warm]*64)
        for _ in range(2):
            state.batch_renderer.render([render_warm]*64, warm_states)
        from PIL import Image
        Image.new('RGB', (256,256)).save(args.output / 'warmup.png', compress_level=1)
        export_scene(small, state.resident.simulate([small])[0][0][0], args.output / 'warmup.usda')
        # Warm all result writers before readiness; startup is excluded from request latency.
        from concurrent.futures import wait
        futures = [state.image_workers.submit(save_png, args.output / f'warmup-{i}.png',
                                             state.batch_renderer.render([render_warm], warm_states[:1])[0][0])
                   for i in range(2)]
        wait(futures)
        for future in futures:
            future.result()
        warm_directory = args.output / 'writer-warmup'
        warm_directory.mkdir(mode=0o700, exist_ok=True)
        warm_futures = []
        warm_image = state.batch_renderer.render([render_warm], warm_states[:1])[0][0]
        for i in range(max(1, args.artifact_processes)):
            directory = warm_directory / str(i)
            directory.mkdir(mode=0o700, exist_ok=True)
            warm_futures.append(state.writers.submit(build_artifacts, str(directory), render_warm,
                                                    warm_states[0], {}, warm_image,
                                                    str(state.resident.physics.view.device), os.environ.get('CUDA_VISIBLE_DEVICES')))
        for future in warm_futures:
            future.result()
        state.warm_request = render_warm
        state.start_retention()
        state.details = {'supported_shapes':['cube','sphere','mesh'], 'render_kinds':['mesh','gaussian'],
                         'max_bodies':256, 'max_steps':1200,
                         'resident_fast_path':'up to 64 cubes of size 0.2; ground; input positions/camera within 90m; optional 256px mesh render at <=8 spp; matching dt/gravity/spp/seed are batched'}
        state.ready = True
        print('RESIDENT_PHYSICS_READY', json.dumps(state.health()), flush=True)
        state.work_forever()
    finally:
        state.ready = False
        state.retention_stop.set()
        server.shutdown()
        if state.resident:
            state.resident.close()
        app.close()

if __name__ == '__main__':
    main()
