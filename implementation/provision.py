"""Disposable loopback-only PostgreSQL cluster, no service/global configuration."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import uuid

import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
NO_WINDOW=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def pg_run(command):
    # Windows background postgres may inherit pipes. A file avoids communicate()
    # waiting for inherited pipe handles long after pg_ctl itself has exited.
    directory=ROOT/'.runtime';directory.mkdir(exist_ok=True)
    output=directory/('command_'+uuid.uuid4().hex[:12]+'.log')
    with output.open('wb') as log:
        result=subprocess.run([str(c) for c in command],stdout=log,stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL,creationflags=NO_WINDOW,timeout=60)
    message=output.read_text(encoding='utf-8',errors='replace')
    if result.returncode:
        raise RuntimeError(message)
    return message


def start():
    pg_bin=Path(os.environ.get('SOMA_PG_BIN',ROOT/'tools/postgresql/pgsql/bin'))
    version=pg_run([pg_bin/'postgres.exe','--version']).strip()
    if version!='postgres (PostgreSQL) 18.7':
        raise RuntimeError('Expected pinned PostgreSQL 18.7, got '+version)
    runtime=ROOT/'.runtime'/('run_'+uuid.uuid4().hex[:12]);runtime.mkdir(parents=True)
    passwords={name:secrets.token_hex(24) for name in
               ('soma_migration','soma_alice','soma_peer','soma_bob','soma_worker')}
    pwfile=runtime/'initpw';pwfile.write_text(passwords['soma_migration'],encoding='utf-8')
    cluster=runtime/'cluster'
    init=pg_run([pg_bin/'initdb.exe','-D',cluster,'-U','soma_migration',
                 '--pwfile',pwfile,'--auth=scram-sha-256','--encoding=UTF8','--locale=C'])
    pwfile.unlink()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with (cluster/'postgresql.conf').open('a',encoding='utf-8') as f:
        f.write("\nlisten_addresses='127.0.0.1'\nmax_connections=24\nfsync=on\nsynchronous_commit=on\nfull_page_writes=on\n")
    pg_run([pg_bin/'pg_ctl.exe','-D',cluster,'-l',runtime/'postgres.log',
            '-w','start','-o',f'-p {port}'])
    cfg=dict(port=port,database='postgres',passwords=passwords,
             cluster=str(cluster),pg_bin=str(pg_bin),runtime=str(runtime))
    path=runtime/'connection.json';path.write_text(json.dumps(cfg),encoding='utf-8')
    os.environ['SOMA_CONFIG']=str(path)
    try:
        with psycopg.connect(host='127.0.0.1',port=port,dbname='postgres',
                              user='soma_migration',password=passwords['soma_migration'],autocommit=True) as con:
            con.execute('CREATE ROLE soma_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            for name in ('soma_alice','soma_peer','soma_bob','soma_worker'):
                con.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {}').format(sql.Identifier(name),sql.Literal(passwords[name])))
            con.execute("REVOKE ALL ON DATABASE postgres FROM PUBLIC")
            con.execute("GRANT CONNECT ON DATABASE postgres TO soma_alice,soma_peer,soma_bob,soma_worker")
            # TEMP is intentionally absent by default; shadow tests use search_path/public.
            con.execute((ROOT/'implementation/migration.sql').read_text(encoding='utf-8'))
        (runtime/'initdb.log').write_text(init,encoding='utf-8')
        return path
    except BaseException:
        stop(path)
        raise


def stop(config_path):
    cfg=json.loads(Path(config_path).read_text(encoding='utf-8'))
    return pg_run([Path(cfg['pg_bin'])/'pg_ctl.exe','-D',cfg['cluster'],'-m','fast','-w','stop'])


if __name__=='__main__':
    print(start())
