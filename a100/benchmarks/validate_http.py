"""Production async wait, validation and bounded admission semantics."""
import concurrent.futures
import os
import json
from pathlib import Path
import threading
import time
import urllib.request, urllib.error
BASE=os.environ.get('A100SIM_URL', 'http://127.0.0.1:8321')
checks=[]
def call(path,payload=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=40) as reply:return reply.status,json.load(reply)
    except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
def check(name,condition,details=None):
    checks.append({'name':name,'passed':bool(condition),'details':details})
    if not condition:raise AssertionError((name,details))
code,health=call('/health');check('ready GPU service',code==200 and health['status']=='ready')
code,_=call('/simulate',{'unexpected':True});check('rejects unknown request fields',code==400)
code,_=call('/jobs/invalid');check('rejects malformed job ID',code==400)
code,_=call('/jobs/'+'f'*32+'?wait_ms=30000');check('unknown job does not wait',code==404)
code,accepted=call('/simulate',{'steps':1200,'render':{}})
check('async submission returns durable pending receipt',code==202 and accepted['state'] in {'queued','running'})
start=time.perf_counter();code,pending=call('/jobs/'+accepted['id']+'?wait_ms=1')
check('bounded wait can return pending without claiming success',code==200 and pending['state'] in {'queued','running'} and time.perf_counter()-start<.5)
code,done=call('/jobs/'+accepted['id']+'?wait_ms=30000')
check('long poll wakes on full result',code==200 and done['state']=='succeeded' and Path(done['image']).stat().st_size>0)
# The workload runs long enough that no slot can complete during the 65 admissions.
barrier=threading.Barrier(66)
def submit(i):
    barrier.wait();return call('/simulate',{'steps':1200,'render':{}})
with concurrent.futures.ThreadPoolExecutor(max_workers=65) as pool:
    futures=[pool.submit(submit,i) for i in range(65)];barrier.wait()
    values=[f.result() for f in futures]
accepted=[v for c,v in values if c==202];denied=[v for c,v in values if c==429]
check('exactly 64 in flight; overload rejected without submission',len(accepted)==64 and len(denied)==1,{'accepted':len(accepted),'overload_429':len(denied)})
with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
    values=list(pool.map(lambda job:call('/jobs/'+job['id']+'?wait_ms=30000'),accepted))
check('all 64 admitted jobs complete full simulation and rendering',all(c==200 and v['state']=='succeeded' and Path(v['image']).is_file() for c,v in values))
code,after=call('/health');check('all admission slots released after completion',after['active_jobs']==0 and after['queue_depth']==0)
Path('results/performance/http-validation.json').write_text(json.dumps({'passed':True,'checks':checks},indent=2))
print(json.dumps({'passed':True,'checks':len(checks)}))
