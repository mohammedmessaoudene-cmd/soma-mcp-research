"""Host bridge over the unchanged R3 kernel; no additional agent authority."""
import asyncio
import os
import json
from pathlib import Path

from db import model_result as original_model_result
import server as core

SOMA_KEY='org.messaoudene.soma/feedback'
CLASSIC_KEY='local.quota/observation'
ARMS=('conventional','soma','conventional_enriched')


def build_result(result,facade='soma'):
    if facade not in ARMS:
        raise ValueError('Unknown host facade')
    if facade=='conventional_enriched':
        public,text,meta=original_model_result(result,'soma')
        return public,text,({CLASSIC_KEY:meta[SOMA_KEY]} if SOMA_KEY in meta else {})
    return original_model_result(result,facade)


core.model_result=build_result
core.server.name='SOMA-R4-Quota-Pilot'
core.server.version='0.4.0-research'

if __name__=='__main__':
    if os.environ.get('SOMA_FACADE','soma') not in ARMS:
        raise ValueError('Host facade must be preconfigured')
    if os.environ.get('SOMA_R4_START_MARKER'):
        marker=Path(os.environ['SOMA_R4_START_MARKER'])
        temporary=marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(process_pid=os.getpid(),kind='NORMAL_BRIDGE_SERVER')),encoding='utf-8')
        temporary.replace(marker)
    asyncio.run(core.main())
