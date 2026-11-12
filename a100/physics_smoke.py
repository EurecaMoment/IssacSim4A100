from pathlib import Path
import os,json,time
from isaacsim import SimulationApp
base=Path(os.environ.get('A100SIM_ROOT', Path(__file__).resolve().parent))
root=base/'packages/isaacsim'
extensions=['omni.physx','omni.physx.tensors','omni.physx.fabric','omni.warp.core','usdrt.scenegraph','omni.kit.loop','omni.usd','omni.kit.usd.layers','omni.hydra.usdrt_delegate']
args=['--ext-folder',str(root/'extscache'),'--ext-folder',str(root/'exts'),'--ext-folder',str(root/'extsDeprecated'),'--/app/extensions/registryEnabled=false','--/app/renderer/enabled=false','--/exts/omni.kit.renderer.core/compatibilityMode=true','--/exts/omni.kit.renderer.core/raytracingEnabled=false','--/app/window/enabled=false','--/app/audio/enabled=false','--/telemetry/enableAnonymousData=false','--/crashreporter/skipOldDumpUpload=true','--portable-root',os.environ.get('A100SIM_KIT_ROOT',str(base/'state/kit'))]
for ext in extensions:args.extend(['--enable',ext])
start=time.perf_counter()
app=SimulationApp({'headless':True,'multi_gpu':False,'limit_cpu_threads':8,'extra_args':args,'fast_shutdown':False,'enable_crashreporter':False},experience=None)
print('PHYSICS_APP_STARTED',round(time.perf_counter()-start,3),flush=True)
import carb,omni.usd,omni.physx,omni.physics.tensors as tensors
from pxr import UsdGeom,UsdPhysics,PhysxSchema,Gf,Sdf
ctx=omni.usd.get_context();ctx.new_stage();stage=ctx.get_stage()
UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z);UsdGeom.SetStageMetersPerUnit(stage,1.0)
scene=UsdPhysics.Scene.Define(stage,'/World/PhysicsScene')
scene.CreateGravityDirectionAttr(Gf.Vec3f(0,0,-1));scene.CreateGravityMagnitudeAttr(9.81)
settings=PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim());settings.CreateEnableGPUDynamicsAttr(True);settings.CreateBroadphaseTypeAttr('GPU')
settings.CreateEnableCCDAttr(False);settings.CreateEnableStabilizationAttr(True)
ground=UsdGeom.Cube.Define(stage,'/World/Ground');ground.CreateSizeAttr(1)
UsdGeom.Xformable(ground).AddTranslateOp().Set(Gf.Vec3d(0,0,-.1));UsdGeom.Xformable(ground).AddScaleOp().Set(Gf.Vec3f(8,8,.2))
UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
for i in range(64):
 cube=UsdGeom.Cube.Define(stage,f'/World/Box_{i:03}')
 cube.CreateSizeAttr(.2);cube.AddTranslateOp().Set(Gf.Vec3d((i%8-3.5)*.4,(i//8-3.5)*.4,2+i*.005))
 UsdPhysics.CollisionAPI.Apply(cube.GetPrim());UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim());mass=UsdPhysics.MassAPI.Apply(cube.GetPrim());mass.CreateMassAttr(1)
carb.settings.get_settings().set('/physics/updateToUsd',True)
carb.settings.get_settings().set('/physics/suppressReadback',True)
carb.settings.get_settings().set('/physics/cudaDevice',0)
phys=omni.physx.get_physx_interface();sim=omni.physx.get_physx_simulation_interface()
sim.attach_stage(ctx.get_stage_id())
phys.start_simulation()
view=tensors.create_simulation_view('warp');view.set_subspace_roots('/World')
rigids=view.create_rigid_body_view('/World/Box_*')
print('GPU_PHYSICS_DEVICE',view.device,'bodies',rigids.count,flush=True)
t0=time.perf_counter()
for step in range(240):sim.simulate(1/120,step/120);sim.fetch_results()
elapsed=time.perf_counter()-t0
transforms=rigids.get_transforms().numpy();z=transforms[:,2]
assert str(view.device).startswith('cuda'), str(view.device)
assert len(z)==64 and float(z.max())<.2 and float(z.min())>.05,(float(z.min()),float(z.max()))
active=[x['name'] for x in app.app.get_extension_manager().get_extensions() if app.app.get_extension_manager().is_extension_enabled(x['id'])]
assert not any(x.startswith('omni.hydra.rtx') for x in active),active
out=base/'results/physics';out.mkdir(parents=True,exist_ok=True)
phys.update_transformations(False,True,True,False)
stage.GetRootLayer().Export(str(out/'scene.usda'))
result={'version':'6.2.0','device':str(view.device),'bodies':64,'steps':240,'dt':1/120,'seconds':elapsed,'steps_per_second':240/elapsed,'z_min':float(z.min()),'z_max':float(z.max()),'no_rtx_renderer':True,'extensions':active}
(out/'report.json').write_text(json.dumps(result,indent=2));print('PHYSICS_SMOKE_RESULT',json.dumps(result),flush=True)
phys.reset_simulation();sim.detach_stage();app.close()
