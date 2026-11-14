"""Resident, collision-isolated GPU rigid-body environments for micro-batches."""
from __future__ import annotations
import time

class ResidentPhysics:
    def __init__(self, environments=64, capacity=64, spacing=256.0):
        import numpy as np
        import warp as wp
        import omni.usd
        from pxr import Gf, UsdGeom, UsdPhysics, Sdf
        from .app import Physics
        self.np, self.wp = np, wp
        self.environments, self.capacity = environments, capacity
        self.context = omni.usd.get_context()
        self.context.new_stage()
        self.stage = stage = self.context.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, '/World').GetPrim())
        scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        groups = [Sdf.Path(f'/World/CollisionGroups/G{e:04d}') for e in range(environments)]
        self.origins = []
        for e in range(environments):
            path = f'/World/Envs/E{e:04d}'
            origin = [float(e % 8) * spacing, float(e // 8) * spacing, 0.0]
            self.origins.append(origin)
            root = UsdGeom.Xform.Define(stage, path)
            root.AddTranslateOp().Set(Gf.Vec3d(*origin))
            group = UsdPhysics.CollisionGroup.Define(stage, groups[e])
            group.GetCollidersCollectionAPI().CreateIncludesRel().SetTargets([Sdf.Path(path)])
            group.CreateFilteredGroupsRel().SetTargets([g for g in groups if g != groups[e]])
            ground = UsdGeom.Cube.Define(stage, path + '/Ground')
            ground.CreateSizeAttr(1)
            ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.1))
            ground.AddScaleOp().Set(Gf.Vec3f(200, 200, .2))
            UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
            for b in range(capacity):
                body = UsdGeom.Xform.Define(stage, path + f'/Bodies/B{b:04d}')
                body.AddTranslateOp().Set(Gf.Vec3d((b % 8 - 3.5) * .4, (b // 8 - 3.5) * .4, 2 + b * .005))
                body.AddOrientOp().Set(Gf.Quatf(1, Gf.Vec3f(0)))
                UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
                UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(1)
                visual = UsdGeom.Cube.Define(stage, str(body.GetPath()) + '/Visual')
                visual.CreateSizeAttr(.2)
                UsdPhysics.CollisionAPI.Apply(visual.GetPrim())
        self.physics = Physics(stage)
        self.physics.view.set_subspace_roots('/World')
        self.rigids = self.physics.view.create_rigid_body_view('/World/Envs/*/Bodies/B*')
        self.paths = list(self.rigids.prim_paths)
        if self.rigids.count != environments * capacity:
            raise RuntimeError(f'Expected {environments * capacity} CUDA bodies, found {self.rigids.count}')
        self.slots = np.empty((environments, capacity), np.int32)
        for index, path in enumerate(self.paths):
            parts = path.split('/')
            self.slots[int(parts[3][1:]), int(parts[5][1:])] = index
        import carb
        carb.settings.get_settings().set('/physics/updateToUsd', False)
        self.indices = wp.array(np.arange(self.rigids.count, dtype=np.uint32), dtype=wp.uint32, device='cpu')
        self.physics.step(1)
        self.indices_gpu = self.indices.to(str(self.physics.view.device))
        self.poses = self.rigids.get_transforms().numpy().copy()
        self.velocities = np.zeros((self.rigids.count, 6), np.float32)
        self.disabled = np.ones((self.rigids.count, 1), np.uint8)
        self.masses = np.ones((self.rigids.count, 1), np.float32)
        self.inertias = np.zeros((self.rigids.count, 9), np.float32)
        self.inertias[:, [0, 4, 8]] = .2 ** 2 / 6
        self.rigids.set_disable_gravities(wp.array(self.disabled, dtype=wp.uint8, device='cpu'), self.indices)
        self.batch_count = 0
        self.parking = self.poses.copy()
        for e in range(environments):
            self.parking[self.slots[e], :3] = np.asarray([(b % 8, b // 8 - 100000, -100000) for b in range(capacity)], dtype=np.float32) + self.origins[e]
        self.parking[:, 3:7] = [0, 0, 0, 1]

    @staticmethod
    def accepts(request):
        capture = request.get('render')
        supported = ((capture is None or (capture['kind'] == 'mesh' and capture['size'] == 256 and capture['spp'] <= 8))
                     and request.get('ground', True)
                     and len(request['bodies']) <= 64
                     and all(b['shape'] == 'cube' and all(abs(v - .2) < 1e-9 for v in b['size'])
                             and not b.get('material') and not b.get('semantic_label')
                             and all(abs(v) <= 90 for v in b['position'])
                             for b in request['bodies']))
        if capture and capture.get('annotations'):
            return False
        if not supported or capture is None:
            return supported
        import math
        az, el = math.radians(capture['azimuth']), math.radians(capture['elevation'])
        direction = (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
        eye = [v + capture['distance'] * d for v, d in zip(capture['target'], direction)]
        # The isolated path tracer clips rays at +/-101 m. Route distant cameras
        # and targets to the unrestricted single-scene GPU renderer.
        return all(abs(v) <= 90 for v in [*eye, *capture['target']])

    def result_in_bounds(self, values):
        poses, velocities = values
        return (self.np.isfinite(poses).all() and self.np.isfinite(velocities).all()
                and (self.np.abs(poses[:, :3]) <= 100.5).all())

    @staticmethod
    def signature(request):
        capture = request.get('render')
        return (request['dt'], request['gravity'],
                (capture['size'], capture['spp'], capture['seed'], capture['kind']) if capture else None)

    def simulate(self, requests, tensor_step=True):
        np, wp = self.np, self.wp
        if not 1 <= len(requests) <= self.environments:
            raise ValueError('Batch size exceeds resident environments')
        started = time.perf_counter()
        self.disabled.fill(1)
        self.poses[:] = self.parking
        self.velocities.fill(0)
        active = []
        positions, orientations, masses = [], [], []
        for e, request in enumerate(requests):
            indices = self.slots[e, :len(request['bodies'])]
            active.extend(indices.tolist())
            positions.extend([body['position'] for body in request['bodies']])
            orientations.extend([body['orientation_xyzw'] for body in request['bodies']])
            masses.extend([body['mass'] for body in request['bodies']])
        active = np.asarray(active, np.int32)
        counts = [len(r['bodies']) for r in requests]
        origins = np.repeat(np.asarray(self.origins[:len(requests)], np.float32), counts, axis=0)
        self.poses[active, :3] = np.asarray(positions, np.float32) + origins
        self.poses[active, 3:7] = orientations
        self.masses[active, 0] = masses
        self.inertias[active] = 0
        for axis in (0, 4, 8):
            self.inertias[active, axis] = np.asarray(masses, np.float32) * (.2 ** 2 / 6)
        self.disabled[active, 0] = 0
        def array(value, dtype):
            return wp.array(value, dtype=dtype, device='cpu')
        self.rigids.set_disable_gravities(array(self.disabled, wp.uint8), self.indices)
        self.rigids.set_transforms(wp.array(self.poses, dtype=wp.float32, device=str(self.physics.view.device)), self.indices_gpu)
        self.rigids.set_velocities(wp.array(self.velocities, dtype=wp.float32, device=str(self.physics.view.device)), self.indices_gpu)
        self.rigids.set_masses(array(self.masses, wp.float32), self.indices)
        self.rigids.set_inertias(array(self.inertias, wp.float32), self.indices)
        self.rigids.wake_up(self.indices)
        self.physics.view.set_gravity([0, 0, -requests[0]['gravity']])
        setup = time.perf_counter() - started
        tick = time.perf_counter()
        dt = requests[0]['dt']
        max_steps = max(r['steps'] for r in requests)
        results = [None] * len(requests)
        checkpoints = sorted({r['steps'] for r in requests})
        previous = 0
        for stop in checkpoints:
            for _ in range(stop - previous):
                if tensor_step:
                    self.physics.view.step(dt)
                else:
                    self.physics.step(1, dt)
            poses = self.rigids.get_transforms().numpy().copy()
            velocities = self.rigids.get_velocities().numpy().copy()
            for e, request in enumerate(requests):
                if request['steps'] == stop:
                    indices = self.slots[e, :len(request['bodies'])]
                    local_poses = poses[indices].copy()
                    local_poses[:, :3] -= self.origins[e]
                    results[e] = (local_poses, velocities[indices].copy())
            previous = stop
        self.batch_count += 1
        return results, {'setup_seconds': setup, 'physics_seconds': time.perf_counter() - tick,
                         'batch_size': len(requests), 'batch_sequence': self.batch_count,
                         'resident_bodies': self.rigids.count, 'tensor_step': tensor_step}

    def close(self):
        self.physics.close()


def benchmark():
    import argparse, json
    from .app import create_app
    from .physics_service import PhysicsQueue
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('--spacing', type=float, default=32)
    args = parser.parse_args()
    app = create_app()
    try:
        tick = time.perf_counter()
        resident = ResidentPhysics(spacing=args.spacing)
        print('RESIDENT_BUILT', time.perf_counter() - tick, flush=True)
        queue = PhysicsQueue(Path('results/batch-prototype'))
        small = queue.prepare({'bodies': [{'id': 'drop', 'position': [0, 0, 2]}], 'steps': 30})
        default = queue.prepare({})
        for step_mode in [False, True]:
            for payload, count in [(small,1), (small,64), (default,64), (default,64), (default,1)]:
                tick = time.perf_counter()
                results, timing = resident.simulate([payload] * count, tensor_step=step_mode)
                print('BATCH_BENCH', json.dumps({**timing, 'wall':time.perf_counter()-tick,
                      'bodies_per_request':len(payload['bodies']), 'steps':payload['steps'],
                      'first_pose':results[0][0][0].tolist(), 'last_pose':results[-1][0][0].tolist()}),flush=True)
        resident.close()
    finally:
        app.close()

if __name__ == '__main__':
    benchmark()
