import mitsuba as mi
mi.set_variant('cuda_ad_rgb')
print('Mesh', [x for x in dir(mi.Mesh) if 'attribute' in x.lower() or 'face' in x.lower() or 'normal' in x.lower() or 'vertex' in x.lower()],flush=True)
print('Shape', [x for x in dir(mi.Shape) if 'attribute' in x.lower() or 'id' in x.lower() or 'eval' in x.lower()],flush=True)
print('SI', [x for x in dir(mi.SurfaceInteraction3f) if 'normal' in x.lower() or 't'==x or 'prim' in x.lower()],flush=True)
print('AOV', mi.load_dict({'type':'integrator','integrator':{'type':'aov','aovs':'depth:depth'}}) if False else 'available')
print('MESH DOC',mi.Mesh.__doc__[:1000] if mi.Mesh.__doc__ else '',flush=True)
print('OPTIX DENOISER',mi.OptixDenoiser.__doc__[:400] if mi.OptixDenoiser.__doc__ else '',flush=True)
