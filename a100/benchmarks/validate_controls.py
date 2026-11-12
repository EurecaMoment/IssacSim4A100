"""Validate resident routing, result ownership and retention without a GPU process."""
import json
import os
from pathlib import Path
import tempfile
import time
from a100sim.resident_service import ResidentQueue, OWNER_TAG, compact_json
from a100sim.batch_physics import ResidentPhysics

checks=[]
def check(name, condition, details=None):
    checks.append({'name':name,'passed':bool(condition),'details':details})
    if not condition:
        raise AssertionError(name)

with tempfile.TemporaryDirectory(prefix='retention-check-',dir='results/performance') as temp:
    root=Path(temp)
    outside=root/'outside'; outside.mkdir(); (outside/'keep.txt').write_text('untouched')
    state=ResidentQueue(root/'jobs',64,'http://127.0.0.1:18323',.2,1,retention_hours=24,max_retained_jobs=2)
    now=time.time()
    def job(n,status='succeeded',age=0,owner=OWNER_TAG,wrong_id=False):
        job_id=f'{n:032x}'; directory=state.output/job_id; directory.mkdir()
        record={'id':'f'*32 if wrong_id else job_id,'owner':owner,'state':status,'finished_at':now-age}
        compact_json(directory/'job.json',record)
        return directory
    old=job(1,age=90000); failed=job(2,'failed',90000)
    overflow=job(3,age=30); recent_a=job(4,age=20);recent_b=job(5,age=10)
    running=job(6,'running',90000); foreign=job(7,age=90000,owner='another-service')
    malformed=job(8,age=90000,wrong_id=True)
    link=state.output/('9'*32);link.symlink_to(outside,target_is_directory=True)
    removed=state.prune_results(now)
    check('retention removes only completed expired or excess owned results',removed==3 and not any(p.exists() for p in [old,failed,overflow]))
    check('retention preserves live, foreign, malformed and recent results',all(p.is_dir() for p in [recent_a,recent_b,running,foreign,malformed]))
    check('retention does not traverse symlink directories',link.is_symlink() and (outside/'keep.txt').read_text()=='untouched')
    check('private directory and JSON permissions',os.stat(state.output).st_mode&0o777==0o700 and os.stat(recent_a/'job.json').st_mode&0o777==0o600)
    marker=state.output/'.service-owner';marker.write_text('not-this-service')
    try:state.prune_results(now)
    except RuntimeError:denied=True
    else:denied=False
    check('ownership mismatch blocks retention',denied)
    state.writers.shutdown();state.image_workers.shutdown();state.generic_workers.shutdown()
    def accepted(value):return ResidentPhysics.accepts(state.prepare(value))
    check('default scene retains resident GPU path',accepted({'render':{}}))
    check('far body uses unrestricted GPU backend',not accepted({'bodies':[{'position':[500,0,2]}],'render':{}}))
    check('far camera uses unrestricted GPU backend',not accepted({'render':{'distance':500}}))
    check('far camera target uses unrestricted GPU backend',not accepted({'render':{'target':[0,500,.5]}}))
    check('gaussian output uses generic GPU renderer',not accepted({'render':{'kind':'gaussian'}}))
    check('higher resolution and samples remain available via generic GPU',not accepted({'render':{'size':512}}) and not accepted({'render':{'spp':16}}))
Path('results/performance/controls-validation.json').write_text(json.dumps({'passed':True,'checks':checks},indent=2))
print(json.dumps({'passed':True,'checks':len(checks)}))
