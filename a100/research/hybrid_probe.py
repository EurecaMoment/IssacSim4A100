from pathlib import Path
import os
import mitsuba as mi,drjit as dr,numpy as np,time
mi.set_variant('cuda_ad_rgb')
from a100sim import hybrid_integrator
from gaussian_test import make_gaussian_scene
s=make_gaussian_scene();s['integrator']={'type':'a100_hybrid_path','max_depth':6}
s['env']={'type':'constant','radiance':{'type':'rgb','value':[.2,.25,.3]}}
s['floor']={'type':'rectangle','to_world':mi.ScalarTransform4f.scale(4),'bsdf':{'type':'diffuse','reflectance':{'type':'rgb','value':[.3,.3,.3]}}}
s['sphere']={'type':'sphere','center':[.5,0,.6],'radius':.6,'bsdf':{'type':'principled','base_color':{'type':'rgb','value':[.7,.3,.1]},'metallic':.9,'roughness':.12}}
s['sensor']['film']['width']=128;s['sensor']['film']['height']=128
scene=mi.load_dict(s)
t=time.perf_counter();image=mi.render(scene,spp=8);dr.eval(image)
mi.util.write_bitmap(str(Path(os.environ.get('A100SIM_ROOT', Path(__file__).resolve().parents[1])) / 'results/render-a100/hybrid.png'),image)
a=np.array(image);print('HYBRID_PASS',a.shape,float(a.std()),time.perf_counter()-t,flush=True)
