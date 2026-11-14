"""Fresh OS client process used by the real SDK interruption proof."""
import asyncio
import json
import os
from pathlib import Path
import sys

from pilot import sdk_session


async def recover(request,marker):
    async with sdk_session('soma',extra_env={'SOMA_R4_START_MARKER':str(marker)}) as (session,init):
        server=json.loads(marker.read_text(encoding='utf-8'))
        status=await session.call_tool('soma.operation_status',{'operation_id':request['operation_id']})
        replay=await session.call_tool('soma.adjust_allocation',request)
        return dict(client_process_pid=os.getpid(),server_process_pid=server['process_pid'],
            protocol=init.protocolVersion,request=request,status=status.structuredContent,replay=replay.structuredContent)


if __name__=='__main__':
    print(json.dumps(asyncio.run(recover(json.loads(sys.argv[1]),Path(sys.argv[2])))))
