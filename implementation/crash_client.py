"""A real process paused at an explicit boundary, terminated by the test parent."""
import json
import os
from pathlib import Path
import sys
import time

from db import call,connect,invoke


if __name__=='__main__':
    mode,op,marker=sys.argv[1:4]
    request=dict(operation_id=op,account_id='allocation_a',delta=5,expected_version=0)
    if mode=='verify':
        with connect() as con:
            result=invoke(con,'soma.adjust_allocation',request)
            result['verification_backend_pid']=con.execute('SELECT pg_backend_pid()').fetchone()[0]
        result['verification_process_pid']=os.getpid()
        print(json.dumps(result),flush=True)
    else:
        with connect() as con:
            result=invoke(con,'soma.adjust_allocation',request)
            if mode=='after_commit':
                con.commit()
            backend_pid=con.execute('SELECT pg_backend_pid()').fetchone()[0]
            Path(marker).write_text(json.dumps(dict(boundary=mode,process_pid=os.getpid(),backend_pid=backend_pid)),encoding='utf-8')
            while True:
                time.sleep(.1)
