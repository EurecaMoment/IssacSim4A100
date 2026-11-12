"""Resident CUDA path tracer with a multi-camera film and isolated light transport."""
from __future__ import annotations
import math
import time
from pathlib import Path
import numpy as np
import drjit as dr
import mitsuba as mi
mi.set_variant('cuda_ad_rgb')


def mis_weight(a, b):
    aa, bb = dr.square(a), dr.square(b)
    return dr.select(aa + bb > 0, aa / (aa + bb), 0)


class IsolatedPathIntegrator(mi.SamplingIntegrator):
    def __init__(self, props):
        super().__init__(props)
        self.max_depth = int(props.get('max_depth', 6))
        self.active_count = dr.opaque(mi.UInt32, 64)
        self.lights = None
        self.spacing = 512.0

    def clip(self, ray, center):
        # Every environment is fully contained in this box. An escaping ray sees its dome.
        boundary = center + dr.select(ray.d > 0, mi.Vector3f(101, 101, 101), mi.Vector3f(-101, -101, -101))
        distance = dr.select(dr.abs(ray.d) > 1e-12, (boundary - ray.o) / ray.d, dr.inf)
        ray.maxt = dr.minimum(ray.maxt, dr.min(distance))
        return ray

    @dr.syntax
    def sample(self, scene, sampler, ray, medium=None, active=True):
        ray = mi.Ray3f(ray)
        ex = mi.UInt32(dr.round(ray.o.x / self.spacing))
        ey = mi.UInt32(dr.round(ray.o.y / self.spacing))
        environment = ey * 8 + ex
        center = mi.Point3f(mi.Float(ex) * self.spacing, mi.Float(ey) * self.spacing, 0)
        active = mi.Bool(active) & (environment < self.active_count)
        light = dr.gather(mi.EmitterPtr, self.lights, environment, active)
        dome = scene.environment()
        result = mi.Spectrum(0)
        throughput = mi.Spectrum(1)
        depth = mi.UInt32(0)
        previous = dr.zeros(mi.SurfaceInteraction3f)
        previous_pdf = mi.Float(1)
        previous_delta = mi.Bool(True)
        valid = mi.Bool(False)
        context = mi.BSDFContext()
        while dr.hint(active, max_iterations=self.max_depth, label='Independent GPU camera paths'):
            ray = self.clip(ray, center)
            si = scene.ray_intersect(ray, ray_flags=mi.RayFlags.All, coherent=depth == 0, active=active)
            hit = si.is_valid()
            valid |= active & hit
            hit_emitter = si.emitter(scene)
            ds_hit = mi.DirectionSample3f(scene, si=si, ref=previous)
            emitter_pdf = hit_emitter.pdf_direction(previous, ds_hit, active & ~previous_delta) * .5
            weight = dr.select(previous_delta, 1, mis_weight(previous_pdf, emitter_pdf))
            result += dr.select(active, throughput * weight * hit_emitter.eval(si, active), 0)
            bsdf = si.bsdf(ray)
            cont = active & hit & (depth + 1 < self.max_depth)
            direct = cont & mi.has_flag(bsdf.flags(), mi.BSDFFlags.Smooth)
            sample = sampler.next_2d()
            choose_light = sample.x < .5
            sample.x = dr.select(choose_light, sample.x * 2, (sample.x - .5) * 2)
            emitter = dr.select(choose_light, light, mi.EmitterPtr(dome))
            ds, light_weight = emitter.sample_direction(si, sample, direct)
            ds.pdf *= .5
            light_weight *= 2
            direct &= ds.pdf > 0
            shadow_ray = si.spawn_ray_to(ds.p)
            shadow_ray.d = dr.select(dr.isfinite(ds.dist), shadow_ray.d, ds.d)
            shadow_ray.maxt = dr.select(dr.isfinite(ds.dist), shadow_ray.maxt, dr.inf)
            shadow_ray = self.clip(shadow_ray, center)
            visible = ~scene.ray_test(shadow_ray, active=direct)
            bsdf_value, bsdf_pdf = bsdf.eval_pdf(context, si, si.to_local(ds.d), direct)
            mis = dr.select(ds.delta, 1, mis_weight(ds.pdf, bsdf_pdf))
            result += dr.select(direct & visible, throughput * light_weight * bsdf_value * mis, 0)
            bs, bsdf_weight = bsdf.sample(context, si, sampler.next_1d(), sampler.next_2d(), cont)
            ray = si.spawn_ray(si.to_world(bs.wo))
            throughput *= bsdf_weight
            previous, previous_pdf = si, bs.pdf
            previous_delta = mi.has_flag(bs.sampled_type, mi.BSDFFlags.Delta)
            depth += 1
            rr_probability = dr.minimum(dr.max(throughput), .95)
            roulette = depth >= 4
            throughput = dr.select(roulette, throughput / rr_probability, throughput)
            survives = ~roulette | (sampler.next_1d() < rr_probability)
            active = cont & survives & (dr.max(throughput) > 1e-5)
        return result, valid, []


mi.register_integrator('a100_isolated_path', lambda props: IsolatedPathIntegrator(props))


def cube_template():
    vertices, normals, faces = [], [], []
    for axis in range(3):
        other = [a for a in range(3) if a != axis]
        for sign in [-1, 1]:
            start = len(vertices)
            for a, b in [(-1,-1),(1,-1),(1,1),(-1,1)]:
                v=[0.,0.,0.]; v[axis]=sign*.5; v[other[0]]=a*.5; v[other[1]]=b*.5
                n=[0.,0.,0.]; n[axis]=float(sign)
                vertices.append(v); normals.append(n)
            # Winding is checked against the outward normal.
            tri = [start, start+1, start+2]
            if np.dot(np.cross(np.asarray(vertices[tri[1]])-vertices[tri[0]],np.asarray(vertices[tri[2]])-vertices[tri[0]]),normals[start]) < 0:
                faces += [[start,start+2,start+1],[start,start+3,start+2]]
            else:
                faces += [[start,start+1,start+2],[start,start+2,start+3]]
    return np.asarray(vertices,np.float32),np.asarray(normals,np.float32),np.asarray(faces,np.uint32)


def rotation_matrix(quaternions):
    x,y,z,w=quaternions.T
    result=np.empty((len(quaternions),3,3),np.float32)
    result[:,0,0]=1-2*(y*y+z*z);result[:,0,1]=2*(x*y-z*w);result[:,0,2]=2*(x*z+y*w)
    result[:,1,0]=2*(x*y+z*w);result[:,1,1]=1-2*(x*x+z*z);result[:,1,2]=2*(y*z-x*w)
    result[:,2,0]=2*(x*z-y*w);result[:,2,1]=2*(y*z+x*w);result[:,2,2]=1-2*(x*x+y*y)
    return result


class BatchRenderer:
    def __init__(self, size=256, environments=64, capacity=64):
        if not dr.has_backend(dr.JitBackend.CUDA):
            raise RuntimeError('CUDA rendering required')
        self.size, self.environments, self.capacity = size, environments, capacity
        self.origins=np.asarray([[e%8*512.,e//8*512.,0] for e in range(environments)],np.float32)
        self.template,self.normals,self.face_template=cube_template()
        count=environments*(capacity+1)
        self.positions=np.zeros((count,7),np.float32);self.positions[:,6]=1
        self.positions[:,:3]=np.repeat(self.origins,capacity+1,axis=0)
        self.positions[:,2]=-10000
        self.scales=np.full((count,3),.2,np.float32)
        self.colors=np.tile(np.array([.35,.5,.7],np.float32),(count,1))
        self.ground_indices=np.arange(environments)*(capacity+1)+capacity
        self.positions[self.ground_indices,:3]=self.origins+[0,0,-.1]
        self.scales[self.ground_indices]=[200,200,.2]
        self.colors[self.ground_indices]=[.32,.34,.37]
        vertices=np.repeat(self.positions[:,:3],24,axis=0)
        props=mi.Properties()
        props['bsdf']=mi.load_dict({'type':'twosided','material':{'type':'principled','roughness':.85,'metallic':0,
                                  'base_color':{'type':'mesh_attribute','name':'vertex_color'}}})
        mesh=mi.Mesh('batch_geometry',len(vertices),count*len(self.face_template),props,has_vertex_normals=True)
        mesh.add_attribute('vertex_color',3,np.repeat(self.colors,24,axis=0).reshape(-1))
        params=mi.traverse(mesh)
        params['vertex_positions']=vertices.reshape(-1)
        params['vertex_normals']=np.tile(self.normals,(count,1)).reshape(-1)
        params['faces']=(self.face_template[None,:,:]+np.arange(count,dtype=np.uint32)[:,None,None]*24).reshape(-1)
        params.update()
        sensor={'type':'batch','film':{'type':'hdrfilm','width':size*environments,'height':size,
                'pixel_format':'rgb','rfilter':{'type':'box'}}}
        scene={'type':'scene','integrator':{'type':'a100_isolated_path','max_depth':6},
               'geometry':mesh,'sensor':sensor,'dome':{'type':'constant','radiance':{'type':'rgb','value':[.45,.48,.52]}}}
        for e,origin in enumerate(self.origins):
            sensor[f'camera_{e:02d}']={'type':'perspective','fov':34,'fov_axis':'x',
                'to_world':mi.ScalarTransform4f.look_at(origin=origin+[0,-5,2],target=origin+[0,0,.5],up=[0,0,1])}
            scene[f'light_{e:02d}']={'type':'rectangle',
                'to_world':mi.ScalarTransform4f.look_at(origin=origin+[-2,-3,4],target=origin+[0,0,.5],up=[0,0,1])@mi.ScalarTransform4f.scale(1.8),
                'emitter':{'type':'area','radiance':{'type':'rgb','value':[5,5,5]}}}
        self.scene=mi.load_dict(scene)
        self.params=mi.traverse(self.scene)
        self.scene.integrator().lights=mi.EmitterPtr(
            [next(s.emitter() for s in self.scene.shapes() if s.id()==f'light_{e:02d}') for e in range(environments)])
        self.last_timing={}
        self.camera_state={}
        self.geometry_state={}

    def keep_warm(self):
        self.scene.integrator().active_count = dr.opaque(mi.UInt32, 1)
        image = mi.render(self.scene, spp=8, seed=42)
        dr.eval(image)
        dr.sync_thread()

    def render(self, requests, states):
        started=time.perf_counter()
        self.positions[:,2]=-10000
        self.positions[self.ground_indices,:3]=self.origins+[0,0,-.1]
        for e,(request,(poses,velocities)) in enumerate(zip(requests,states)):
            indices=np.arange(e*(self.capacity+1),e*(self.capacity+1)+len(poses))
            self.positions[indices,:3]=poses[:,:3]+self.origins[e]
            self.positions[indices,3:7]=poses[:,3:7]
            self.colors[indices]=[b['color'] for b in request['bodies']]
            capture=request['render']
            az,el=math.radians(capture['azimuth']),math.radians(capture['elevation'])
            target=np.asarray(capture['target'])+self.origins[e]
            eye=target+capture['distance']*np.array([math.sin(az)*math.cos(el),-math.cos(az)*math.cos(el),math.sin(el)])
            camera_key=(*eye,*target)
            if self.camera_state.get(e) != camera_key:
                self.params[f'sensor.camera_{e:02d}.to_world']=mi.ScalarTransform4f.look_at(origin=eye,target=target,up=[0,0,1])
                self.camera_state[e]=camera_key
        rotations=rotation_matrix(self.positions[:,3:7])
        transposed=rotations.swapaxes(1,2)
        vertices=(self.template[None,:,:]*self.scales[:,None,:]) @ transposed + self.positions[:,None,:3]
        normals=self.normals @ transposed
        for key,value in [('geometry.vertex_positions',vertices.reshape(-1)),
                          ('geometry.vertex_normals',normals.reshape(-1)),
                          ('geometry.vertex_color',np.repeat(self.colors,24,axis=0).reshape(-1))]:
            previous=self.geometry_state.get(key)
            if previous is None or not np.array_equal(previous,value):
                self.params[key]=value
                self.geometry_state[key]=value.copy()
        # Reuse unchanged acceleration structures and material buffers, never image results.
        self.params.update()
        self.scene.integrator().active_count=dr.opaque(mi.UInt32,len(requests))
        setup=time.perf_counter()-started
        tick=time.perf_counter()
        image=mi.render(self.scene,spp=requests[0]['render']['spp'],seed=requests[0]['render']['seed'])
        dr.eval(image);dr.sync_thread()
        render_seconds=time.perf_counter()-tick
        tick=time.perf_counter()
        srgb=mi.TensorXf(mi.math.linear_to_srgb(dr.maximum(image.array, 0)), shape=image.shape)
        srgb=dr.clip(srgb, 0, 1)
        pixels=dr.cuda.ad.UInt8(dr.round(srgb.array*255))
        dr.eval(pixels)
        value=np.asarray(pixels).reshape(image.shape).copy()
        self.last_timing={'render_setup_seconds':setup,'render_seconds':render_seconds,
                          'image_readback_seconds':time.perf_counter()-tick,
                          'renderer':'A100 CUDA isolated multi-camera path tracer','native_rtx':False,'cpu_fallback':False,
                          'pixel_encoding':'srgb-u8','resolution':[self.size,self.size],'spp':requests[0]['render']['spp'],'max_depth':6}
        return [value[:,e*self.size:(e+1)*self.size].copy() for e in range(len(requests))],self.last_timing


def benchmark():
    import json
    renderer=BatchRenderer()
    print('BATCH_RENDERER_BUILT',len(renderer.params.keys()),list(renderer.params.keys())[:12],flush=True)
    def request(e):
        return {'bodies':[{'color':[.35+.005*e,.5,.7]} for i in range(64)],
                'render':{'azimuth':35+e,'elevation':20,'target':[0,0,.5],'distance':5,'spp':8,'seed':42}}
    requests=[request(e) for e in range(64)]
    poses=np.zeros((64,7),np.float32);poses[:,6]=1
    poses[:,:3]=[[(i%8-3.5)*.4,(i//8-3.5)*.4,.1] for i in range(64)]
    for count in [1,64,64,64]:
        tick=time.perf_counter();images,timing=renderer.render(requests[:count],[(poses,np.zeros((64,6),np.float32))]*count)
        print('ISOLATED_RENDER_BENCH',json.dumps({**timing,'batch_size':count,'wall':time.perf_counter()-tick,'mean':float(np.mean(images[0]))}),flush=True)
        for e in [0,count-1]:
            mi.util.write_bitmap(str(Path('results/performance')/f'batch-{count}-{e}.png'),images[e])

if __name__=='__main__':
    benchmark()
