"""Real GPU completion, routing and render invalidation checks through production HTTP."""
import os
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

BASE=os.environ.get('A100SIM_URL', 'http://127.0.0.1:8321')
ASSET=os.environ.get('A100SIM_RENDER_ASSET', str(Path(os.environ.get('A100SIM_ROOT', Path(__file__).resolve().parents[1])) / 'assets/example'))
checks=[]
def call(path,payload=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as reply:return json.load(reply)
def run(name,payload,route):
    start=time.perf_counter();job=call('/simulate?wait_ms=30000',payload)
    while job['state'] in {'queued','running'}:job=call('/jobs/'+job['id']+'?wait_ms=30000')
    elapsed=time.perf_counter()-start
    assert job['state']=='succeeded',(name,job.get('error'))
    assert job['route']==route,(name,job['route'])
    for key in ['scene','state_npz','simulation_result','image']:
        assert Path(job[key]).is_file(),(name,key)
    from PIL import Image
    with Image.open(job['image']) as image:
        image.load()
        assert image.size==(payload['render'].get('size',256),)*2
    row={'name':name,'passed':True,'complete_seconds':elapsed,'job_id':job['id'],'route':job['route'],
         'physical_gpu':job['physical_gpu'],'cpu_fallback':job['cpu_fallback'],'steps':job['steps'],
         'image':job['image'],'scene':job['scene'],'render':job.get('render'),
         'first_pose':job['bodies'][0]['pose_xyzw'],'fallback_reason':job.get('fallback_reason')}
    assert job['cpu_fallback'] is False
    checks.append(row);print(json.dumps({k:v for k,v in row.items() if k not in {'render','first_pose'}}),flush=True)
    return job
capture={'kind':'mesh','size':256,'spp':8,'target':[0,0,1.65],'distance':3}
red={'bodies':[{'id':'red','position':[0,0,2],'color':[.85,.05,.03]}],'steps':30,'render':capture}
green={'bodies':[{'id':'green','position':[.2,0,2],'color':[.03,.8,.05]}],'steps':30,'render':{**capture,'azimuth':110}}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    a=pool.submit(run,'resident-red',red,'resident-gpu');b=pool.submit(run,'resident-green',green,'resident-gpu')
    red_job,green_job=a.result(),b.result()
assert Path(red_job['image']).read_bytes()!=Path(green_job['image']).read_bytes(),'color/camera/geometry must affect the rendered image'
assert abs(red_job['bodies'][0]['pose_xyzw'][2]-(2-9.81*(1/120)**2*30*31/2))<2e-5
run('far-object-and-camera',{'bodies':[{'id':'far','position':[150,0,2]}],'steps':30,'render':{**capture,'target':[150,0,1.65]}},'generic-gpu')
escaped=run('post-simulation-boundary-fallback',{'bodies':[{'id':'escape','position':[0,0,-89]}],'steps':1200,'dt':.02,'render':{}},'generic-gpu')
assert escaped.get('fallback_reason')
run('sphere-and-512px',{'bodies':[{'id':'sphere','shape':'sphere','radius':.3}],'steps':30,'render':{'size':512,'spp':8,'target':[0,0,1.6]}},'generic-gpu')
run('higher-sample-count',{'bodies':[{'id':'hi'}],'steps':30,'render':{**capture,'spp':32}},'generic-gpu')
for kind in ['mesh','gaussian']:
    run('sam3d-'+kind,{'bodies':[{'id':'sam3d','shape':'mesh','asset':ASSET}],'steps':30,'render':{'kind':kind,'size':256,'spp':8}},'generic-gpu')
report={'passed':True,'measurement':'HTTP completion includes simulation, GPU rendering and required files; generic cases are functional tests, not a 1s SLA',
        'checks':checks,'red_job':red_job,'green_job':green_job}
Path('results/performance/completion-correctness.json').write_text(json.dumps(report,indent=2))
