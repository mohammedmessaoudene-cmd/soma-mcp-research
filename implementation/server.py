"""Official MCP SDK v1 stdio server. The host selects one immutable SQL identity."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from db import call,model_result

ROOT=Path(__file__).resolve().parents[1]
server=Server('SOMA-R3',version='3.0.0-local')
role=os.environ.get('SOMA_ROLE','soma_alice')
if role not in ('soma_alice','soma_peer','soma_bob'):
    raise ValueError('Host must select an application role')


@server.list_tools()
async def list_tools():
    adjust=json.loads((ROOT/'spec/adjust_allocation.schema.json').read_text(encoding='utf-8'))
    return [types.Tool(name='soma.adjust_allocation',description='Correct an integer allocation under current authority and capacity.',inputSchema=adjust),
            types.Tool(name='soma.read_state',description='Read the authenticated tenant summary.',inputSchema={'type':'object','properties':{},'additionalProperties':False}),
            types.Tool(name='soma.operation_status',description='Query the same operation key; absence may mean pending.',inputSchema={'type':'object','properties':{'operation_id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,64}$'}},'required':['operation_id'],'additionalProperties':False})]


@server.call_tool(validate_input=False)
async def call_tool(name,arguments):
    result=await asyncio.to_thread(call,name,arguments,role)
    public,text,meta=model_result(result,os.environ.get('SOMA_FACADE','soma'))
    return types.CallToolResult(content=[types.TextContent(type='text',text=text)],
        structuredContent=public,isError=result.get('status') in ('DENIED','OUTCOME_PENDING'),
        _meta=meta)


class BoundedInput:
    """SDK text stream reader with a hard limit BEFORE JSON parsing."""
    def __init__(self,stream):
        self.stream=stream

    def __getattr__(self,name):
        return getattr(self.stream,name)

    def __aiter__(self):
        return self

    async def __anext__(self):
        line=await asyncio.to_thread(self.stream.readline,16385)
        if not line:
            raise StopAsyncIteration
        if len(line)>16384:
            raise ValueError('MCP_FRAME_LIMIT')
        return line.decode('utf-8',errors='strict')


async def main():
    async with stdio_server(stdin=BoundedInput(sys.stdin.buffer)) as (read,write):
        await server.run(read,write,server.create_initialization_options())


if __name__=='__main__':
    asyncio.run(main())
