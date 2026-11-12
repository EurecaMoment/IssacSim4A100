import os
#!/usr/bin/env python3
"""End-to-end, complete-result GPU service benchmark (never counts HTTP 202 as done)."""
import argparse
import concurrent.futures
import json
import math
from pathlib import Path
import threading
import time
import urllib.request


def call(base, path, payload=None):
    request = urllib.request.Request(base + path, data=payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None,
                                    headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def percentile(values, fraction):
    if not values:
        return None
    values=sorted(values)
    return values[min(len(values)-1, math.ceil(len(values)*fraction)-1)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8321')
    parser.add_argument('--concurrency',type=int,default=64)
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--workload',choices=['light','default','render','gaussian','mesh','mixed','default_render','varied_render'],default='default')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--long-poll-ms',type=int,default=0)
    parser.add_argument('--sync-wait-ms',type=int,default=0)
    parser.add_argument('--poll-ms',type=float,default=10)
    parser.add_argument('--idle-seconds',type=float,default=0)
    args=parser.parse_args()
    report={'url':args.url,'concurrency':args.concurrency,'rounds':[], 'workload':args.workload,
            'measurement':'Client HTTP submission through succeeded result, with every required artifact present; JSON requests are serialized before simultaneous release',
            'health':call(args.url,'/health')}
    def payload(index):
        if args.workload=='varied_render':
            return {'bodies':[{'id':f'body-{j}','position':[(j%8-3.5)*.4+(index-31.5)*.003,(j//8-3.5)*.4,2+j*.005],
                               'mass':.5+index*.025,'color':[.15+index*.008,.4,.7-index*.007]} for j in range(64)],
                    'steps':240,'render':{'kind':'mesh','size':256,'spp':8,'azimuth':35+index*.3}}
        if args.workload=='default_render':
            return {'render':{'kind':'mesh','size':256,'spp':8,'azimuth':35+index*.1}}
        if args.workload=='light':
            return {'bodies':[{'id':f'drop-{index}','position':[index*.001,0,2]}],'steps':30}
        if args.workload in {'render','gaussian','mesh'}:
            return {'bodies':[{'id':'object','shape':'mesh','asset':os.environ.get('A100SIM_RENDER_ASSET', str(Path(os.environ.get('A100SIM_ROOT', Path(__file__).resolve().parents[1])) / 'assets/example'))}],
                    'steps':30,'render':{'kind':'gaussian' if args.workload=='gaussian' else 'mesh', 'size':256, 'spp':8}} if args.workload!='mesh' else {
                    'bodies':[{'id':'object','shape':'mesh','asset':os.environ.get('A100SIM_RENDER_ASSET', str(Path(os.environ.get('A100SIM_ROOT', Path(__file__).resolve().parents[1])) / 'assets/example'))}],'steps':30}
        if args.workload=='mixed':
            return {'bodies':[{'id':f'drop-{index}','position':[index*.001,0,2], 'mass':.1+index*.1}],
                    'steps':30+(index%4)*10,'gravity':9.81 if index%2==0 else 5}
        return {}
    if args.idle_seconds:
        if not 0 < args.idle_seconds <= 60:
            parser.error('idle-seconds must be between 0 and 60')
        report['idle_before_first_round_seconds']=args.idle_seconds
        time.sleep(args.idle_seconds)
    for round_index in range(args.rounds):
        barrier=threading.Barrier(args.concurrency+1)
        def run(index):
            value=json.dumps(payload(index)).encode()
            barrier.wait()
            start=time.perf_counter()
            accepted=call(args.url,'/simulate'+('?wait_ms='+str(args.sync_wait_ms) if args.sync_wait_ms else ''),value)
            accept_seconds=time.perf_counter()-start
            while True:
                job=accepted if accepted['state'] in {'succeeded','failed','interrupted'} else call(args.url,'/jobs/'+accepted['id']+('?wait_ms='+str(args.long_poll_ms) if args.long_poll_ms else ''))
                if job['state'] in {'succeeded','failed','interrupted'}:
                    break
                if time.perf_counter()-start>120:
                    raise TimeoutError(accepted['id'])
                time.sleep(args.poll_ms/1000)
            elapsed=time.perf_counter()-start
            missing=[]
            if job['state']=='succeeded':
                for key in ['scene','state_npz','simulation_result']+(['image'] if args.workload in {'render','gaussian','default_render','varied_render'} else []):
                    artifact = Path(job.get(key,'/missing'))
                    if not artifact.is_file() or artifact.stat().st_size == 0:
                        missing.append(key)
                    elif key == 'image':
                        from PIL import Image
                        with Image.open(artifact) as image:
                            image.load()
                            if image.size != (256,256):
                                missing.append('image_dimensions')
            return {'id':accepted['id'],'complete_seconds':elapsed,'accept_seconds':accept_seconds,
                    'state':job['state'],'missing':missing,'execution_seconds':job.get('execution_seconds'),
                    'queue_seconds':job.get('queue_seconds'),'batch_sequence':job.get('batch_sequence'),
                    'batch_size':job.get('batch_size'),'setup_seconds':job.get('setup_seconds'),
                    'physics_seconds':job.get('physics_seconds'),'route':job.get('route'),
                    'first_pose':job.get('bodies',[{}])[0].get('pose_xyzw'),
                    **({'error':job['error']} if 'error' in job else {})}
        rows=[]; errors=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures=[pool.submit(run,i) for i in range(args.concurrency)]
            start=time.perf_counter(); barrier.wait()
            for future in concurrent.futures.as_completed(futures):
                try: rows.append(future.result())
                except Exception as exc: errors.append(str(exc))
        elapsed=time.perf_counter()-start
        valid=[r for r in rows if r['state']=='succeeded' and not r['missing']]
        durations=[r['complete_seconds'] for r in valid]
        summary={'round':round_index+1,'wall_seconds':elapsed,'succeeded':len(valid), 'failed':len(rows)-len(valid),
                 'errors':errors,'within_1_second':sum(v<=1 for v in durations),
                 'complete_p50':percentile(durations,.5),'complete_p95':percentile(durations,.95),
                 'complete_max':max(durations) if durations else None,
                 'requests_per_second':len(valid)/elapsed,
                 'batches':sorted({(r['batch_sequence'],r['batch_size']) for r in rows if r['batch_sequence'] is not None}),
                 'rows':rows}
        report['rounds'].append(summary)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k!='rows'}),flush=True)
        time.sleep(.2)
    report['health_after']=call(args.url,'/health')
    args.report.write_text(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
