"""Persistent fixture receiver, separate process and database from the business DB.

stdin/stdout JSON lines are LOCAL trusted worker transport, not an MCP replacement.
Exactly one effect and one logical synthetic charge per event; attempts are counted.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def receive(path, request):
    con=sqlite3.connect(path,timeout=5,isolation_level=None)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=FULL')
        con.execute('CREATE TABLE IF NOT EXISTS receipts(event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, payload TEXT NOT NULL, cost INTEGER NOT NULL, attempts INTEGER NOT NULL)')
        con.execute('BEGIN IMMEDIATE')
        eid=request['event_id']
        row=con.execute('SELECT payload_hash,cost,attempts FROM receipts WHERE event_id=?',(eid,)).fetchone()
        if request.get('query'):
            con.commit()
            return None if row is None else dict(event_id=eid,payload_hash=row[0],cost=row[1],attempts=row[2])
        body=request['payload']
        ph=hashlib.sha256(body.encode()).hexdigest()
        if ph!=request['payload_hash'] or (row and row[0]!=ph):
            raise ValueError('RECEIVER_PAYLOAD_CONFLICT')
        if row:
            con.execute('UPDATE receipts SET attempts=attempts+1 WHERE event_id=?',(eid,))
        else:
            con.execute('INSERT INTO receipts VALUES(?,?,?,?,1)',(eid,ph,body,40))
        con.commit()
        return dict(event_id=eid,payload_hash=ph,cost=40)
    finally:
        con.close()


if __name__=='__main__':
    for line in sys.stdin:
        if len(line.encode())>16384:
            raise ValueError('FRAME_LIMIT')
        answer=receive(Path(sys.argv[1]),json.loads(line))
        print(json.dumps(answer),flush=True)
