import json,time
import numpy as np
import mitsuba as mi
import drjit as dr
mi.set_variant('cuda_ad_rgb')
for count in [1,4,16,64]:
 t=time.perf_counter()
 sensor={'type':'batch','film':{'type':'hdrfilm','width':count*256,'height':256,'pixel_format':'rgb','rfilter':{'type':'box'}}}
 for i in range(count):
  sensor[f'camera_{i:02d}']={'type':'perspective','fov':34,'to_world':mi.ScalarTransform4f.look_at(origin=[0,-5,2],target=[0,0,.5],up=[0,0,1])}
 scene=mi.load_dict({'type':'scene','integrator':{'type':'path','max_depth':6},'sensor':sensor,
  'object':{'type':'cube','to_world':mi.ScalarTransform4f.translate([0,0,.5])@mi.ScalarTransform4f.scale(.5), 'bsdf':{'type':'diffuse','reflectance':{'type':'rgb','value':[.4,.2,.2]}}},
  'floor':{'type':'rectangle','to_world':mi.ScalarTransform4f.scale(100),'bsdf':{'type':'diffuse'}},
  'env':{'type':'constant','radiance':{'type':'rgb','value':[.5,.5,.5]}}})
 print('BATCH_SCENE_BUILT',count,time.perf_counter()-t,flush=True)
 for repeat in range(2):
  t=time.perf_counter(); image=mi.render(scene,spp=8,seed=42);dr.eval(image);dr.sync_thread()
  print('BATCH_RENDER',count,repeat,time.perf_counter()-t,'SHAPE',image.shape,flush=True)
