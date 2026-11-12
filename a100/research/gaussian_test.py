import numpy as np

def make_gaussian_scene():
    means=[];scales=[];quats=[];opacities=[];sh=[]
    # Native Gaussian ellipsoids, not a triangulated substitute.
    for i in range(12):
        t=i/11
        a=2*np.pi*t
        means.append([.8*np.cos(a),.8*np.sin(a),.9+.15*np.sin(2*a)])
        scales.append([.10,.25,.10]);quats.append([1,0,0,0]);opacities.append([.75])
        color=[abs(np.sin(a)),.25+.65*t,1-t]
        sh.append(np.asarray(color)/.28209479177387814)
    return {'type':'scene','integrator':{'type':'moment','nested':{'type':'volprim_rf_basic','max_depth':8}},
      'sensor':{'type':'perspective','to_world':__import__('mitsuba').ScalarTransform4f.look_at(origin=[0,-4,1.4],target=[0,0,1],up=[0,0,1]),'fov':45,'film':{'type':'hdrfilm','width':256,'height':256,'pixel_format':'rgb'}},
      'shape':{'type':'ellipsoids','centers':__import__('mitsuba').TensorXf(np.asarray(means,np.float32)),'scales':__import__('mitsuba').TensorXf(np.asarray(scales,np.float32)),'quaternions':__import__('mitsuba').TensorXf(np.asarray(quats,np.float32)),'opacities':__import__('mitsuba').TensorXf(np.asarray(opacities,np.float32)),'sh_coeffs':__import__('mitsuba').TensorXf(np.asarray(sh,np.float32))}}
