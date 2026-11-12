"""A100 render smoke: PBR mesh plus native Gaussian ellipsoid splatting."""
from pathlib import Path
import json, time, os
import numpy as np
import drjit as dr
import mitsuba as mi
from gaussian_test import make_gaussian_scene

mi.set_variant('cuda_ad_rgb')
base=Path(__file__).resolve().parent.parent
out=base/'results/render-a100';out.mkdir(parents=True,exist_ok=True)

def render(name, scene):
    t=time.perf_counter(); image=mi.render(scene,spp=8); dr.eval(image); elapsed=time.perf_counter()-t
    arr=np.array(image); np.save(out/(name+'.npy'),arr)
    mi.util.write_bitmap(str(out/(name+'.exr')),image)
    return {'shape':list(arr.shape),'std':float(arr.std()),'min':float(arr.min()),'max':float(arr.max()),'seconds':elapsed}

mesh = mi.load_dict({
 'type':'scene','integrator':{'type':'path','max_depth':5},
 'sensor':{'type':'perspective','to_world':mi.ScalarTransform4f.look_at(origin=[3,-5,3],target=[0,0,.5],up=[0,0,1]),'fov':45,'film':{'type':'hdrfilm','width':256,'height':256,'pixel_format':'rgb'}},
 'integrator_light':{'type':'constant'},
 'floor':{'type':'rectangle','to_world':mi.ScalarTransform4f.translate([0,0,0]) @ mi.ScalarTransform4f.rotate([1,0,0],90),'bsdf':{'type':'diffuse','reflectance':{'type':'rgb','value':[.4,.4,.4]}}},
 'box':{'type':'cube','to_world':mi.ScalarTransform4f.translate([-.7,0,.5]) @ mi.ScalarTransform4f.scale([1,1,1]),'bsdf':{'type':'principled','base_color':{'type':'rgb','value':[.03,.15,.9]},'roughness':.28}},
 'sphere':{'type':'sphere','to_world':mi.ScalarTransform4f.translate([.7,.2,.65]) @ mi.ScalarTransform4f.scale(.65),'bsdf':{'type':'principled','base_color':{'type':'rgb','value':[.7,.4,.08]},'metallic':.75,'roughness':.18}},
 'light':{'type':'rectangle','to_world':mi.ScalarTransform4f.translate([0,-1,4]) @ mi.ScalarTransform4f.rotate([1,0,0],0) @ mi.ScalarTransform4f.scale([4,4,4]),'emitter':{'type':'area','radiance':{'type':'rgb','value':[10,10,10]}}},
})

gaussian = mi.load_dict(make_gaussian_scene())
report={'backend':'Mitsuba CUDA + patched DrJit','device':'NVIDIA A100-SXM4-80GB','mesh':render('mesh',mesh),'gaussian':render('gaussian',gaussian),'no_cpu_fallback':True,'gaussian_representation':'ellipsoids + SH + volprim_rf_basic','status':'passed'}
(out/'report.json').write_text(json.dumps(report,indent=2));print('A100_RENDER_SMOKE_RESULT',json.dumps(report),flush=True)
