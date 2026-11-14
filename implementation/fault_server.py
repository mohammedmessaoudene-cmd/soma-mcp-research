"""TEST-ONLY host failpoint, no failpoint argument exists in the MCP catalogue."""
import asyncio
import json
import os
from pathlib import Path
import time

import bridge_server

core=bridge_server.core
original_call=core.call


def commit_then_pause(*args,**kwargs):
    result=original_call(*args,**kwargs)
    if result.get('status')=='COMMITTED' and not result.get('replayed'):
        marker=Path(os.environ['SOMA_R4_FAILPOINT_MARKER'])
        data=dict(boundary='POSTGRES_COMMITTED_BEFORE_MCP_RESPONSE',process_pid=os.getpid(),
                  operation_id=result['operation_id'],event_id=result.get('event_id'))
        temporary=marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(data),encoding='utf-8')
        temporary.replace(marker)
        while True:time.sleep(.05)
    return result


if __name__=='__main__':
    core.call=commit_then_pause
    asyncio.run(core.main())
