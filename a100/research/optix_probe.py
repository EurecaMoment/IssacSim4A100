import mitsuba as mi,drjit as dr,time,os
print('MITSUBA',mi.__version__,'DRJIT',dr.__version__,flush=True)
mi.set_variant('cuda_ad_rgb')
print('CUDA_AVAILABLE',dr.has_backend(dr.JitBackend.CUDA),flush=True)
s=mi.load_dict({'type':'scene','integrator':{'type':'path'},'sensor':{'type':'perspective','to_world':mi.ScalarTransform4f.look_at(origin=[0,0,5],target=[0,0,0],up=[0,1,0]),'film':{'type':'hdrfilm','width':64,'height':64}},'sphere':{'type':'sphere','bsdf':{'type':'diffuse','reflectance':{'type':'rgb','value':[0.7,0.1,0.05]}}},'env':{'type':'constant','radiance':{'type':'rgb','value':[1,1,1]}}})
t=time.perf_counter();im=mi.render(s,spp=4);dr.eval(im)
import numpy as np
ar=np.array(im);print('OPTIX_A100_RENDER',ar.shape,float(ar.std()),'SECONDS',time.perf_counter()-t,flush=True)
