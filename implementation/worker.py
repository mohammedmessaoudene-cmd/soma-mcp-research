"""One bounded delivery/reconciliation attempt. The receiver is a subprocess."""
import json
import os
from pathlib import Path
import subprocess
import sys

from db import connect

HERE=Path(__file__).resolve().parent


def transition(name,*args):
    queries={'claim':'SELECT soma.claim(%s)','mark_unknown':'SELECT soma.mark_unknown(%s)',
             'settle':'SELECT soma.settle(%s,%s,%s)'}
    with connect('soma_worker') as con:
        return con.execute(queries[name],args).fetchone()[0]


def receiver_call(receiver_path,request):
    p=subprocess.run([sys.executable,str(HERE/'receiver.py'),str(receiver_path)],
                      input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=10)
    if p.returncode:
        raise RuntimeError('RECEIVER_UNAVAILABLE_OR_CONFLICT')
    return json.loads(p.stdout)


def deliver(eid,receiver_path,checkpoint=None):
    item=transition('claim',eid)
    if item['status'] not in ('CLAIMED','RECONCILE_ONLY'):
        return item
    try:
        # Resolve from durable receiver state first. Empty lookup alone never releases.
        ack=receiver_call(receiver_path,{'event_id':eid,'query':True})
        if ack is None:
            if item['status']=='RECONCILE_ONLY':
                transition('mark_unknown',eid)
                return {'status':'UNKNOWN','retry_limit':3}
            ack=receiver_call(receiver_path,item)
        if checkpoint:
            Path(checkpoint).write_text(json.dumps(dict(boundary='RECEIVER_COMMITTED',process_pid=os.getpid())),encoding='utf-8')
            # Test harness terminates this actual worker process here.
            import time
            while True:
                time.sleep(.1)
        return transition('settle',eid,ack['payload_hash'],ack['cost'])
    except (RuntimeError,subprocess.TimeoutExpired,OSError,ValueError):
        transition('mark_unknown',eid)
        return {'status':'UNKNOWN'}


if __name__=='__main__':
    result=deliver(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
    print(json.dumps(result),flush=True)
