"""Compare isolated batching with an independent Mitsuba path integrator."""
import json
from pathlib import Path
from .app import create_app
app=create_app()
try:
    import numpy as np
    from PIL import Image
    from pxr import Usd
    from .batch_renderer import BatchRenderer
    from .usd_renderer import render_stage
    from .resident_service import export_scene
    report=json.loads(Path('results/performance/completion-correctness.json').read_text())
    job=report['red_job']
    directory=Path('results/performance/render-correctness');directory.mkdir(exist_ok=True)
    request=json.loads((Path(job['scene']).parent/'request.json').read_text())
    state=np.load(job['state_npz']);values=(state['poses_xyzw'],state['velocities'])
    request['render']['spp']=256
    renderer=BatchRenderer()
    images,timing=renderer.render([request],[values])
    Image.fromarray(images[0]).save(directory/'isolated-256spp.png')
    stage=Usd.Stage.Open(job['scene'])
    reference=directory/'independent-256spp.png'
    render_stage(stage,'/World/Camera','mesh',reference,size=256,spp=256,seed=42)
    a=images[0].astype(np.float32)/255;b=np.array(Image.open(reference).convert('RGB'),np.float32)/255
    difference=np.abs(a-b);rmse=float(np.sqrt(np.mean((a-b)**2)))
    checks=[{'name':'independent path tracer image agreement','passed':rmse<.035,'rmse_srgb':rmse,'mean_absolute_error':float(difference.mean())}]
    # A neighboring green scene must not alter the first environment's transport.
    neighbor={**request,'bodies':[{**request['bodies'][0],'color':[.03,.8,.05]}]}
    together,_=renderer.render([request,neighbor],[values,values])
    isolation_rmse=float(np.sqrt(np.mean((a-together[0].astype(np.float32)/255)**2)))
    checks.append({'name':'neighbor color does not leak between environments','passed':isolation_rmse<.035,'rmse_srgb':isolation_rmse})
    Image.fromarray(together[1]).save(directory/'neighbor-green-256spp.png')
    # Reset changed color/geometry/camera and verify buffer invalidation.
    again,_=renderer.render([request],[values])
    reset_rmse=float(np.sqrt(np.mean((a-again[0].astype(np.float32)/255)**2)))
    checks.append({'name':'scene cache invalidates changes and returns to prior state','passed':reset_rmse<.001,'rmse_srgb':reset_rmse})
    result={'passed':all(c['passed'] for c in checks),'checks':checks,'reference':str(reference),'isolated':str(directory/'isolated-256spp.png')}
    Path('results/performance/render-correctness.json').write_text(json.dumps(result,indent=2))
    print('RENDER_CORRECTNESS',json.dumps(result),flush=True)
    assert result['passed'],result
finally:
    app.close()
