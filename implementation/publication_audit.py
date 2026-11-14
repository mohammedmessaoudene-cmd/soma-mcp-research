"""Public allowlist, non-disclosing leakage scan and complete file manifest.

Generated runtime credentials are compared without printing their values. This
is a deterministic release check, not a general-purpose secret detection claim.
"""
import argparse
import json
from pathlib import Path
import re

from identity_r4 import ROOT,sha,records,identities

IMPLEMENTATION={
 'db.py','migration.sql','server.py','fixtures.py','provision.py','receiver.py','worker.py',
 'crash_client.py','test_integration.py','requirements.txt','bridge_server.py','pilot.py',
 'task_oracle.py','integrity_oracle.py','test_r4.py','fault_server.py','recovery_client.py',
 'identity_r4.py','run_r4.py','publication_audit.py','reproduce.ps1','verify_dependencies.py'}
SPEC={'R4_CONTRACT.md','ACCEPTANCE_R4.json','PILOT_FIXTURES.json','adjust_allocation.schema.json'}
DOCS={'TECHNICAL_NOTE_EN.md','BILAN_FR.md','REVIEW_SUMMARY.md'}
RESULTS={'environment.json','core_regression.json','bridge_regression.json','pilot.json',
 'summary.json','source_identity.json','reproduction.json','cleanup.json','provenance.json','publication_audit.json'}
TOP={'README.md','NOTICE.md','CITATION.cff','.gitignore','.gitattributes','MANIFEST.sha256'}
EXCLUDED={'.git','.runtime','.venv','tools','__pycache__'}
PATTERNS={
 'personal_absolute_path':re.compile(r'[A-Za-z]:[\\/](?:Users|soma)[\\/]',re.I),
 'private_chat_url':re.compile(r'chatgpt\.com/c/[0-9a-f-]{20,}',re.I),
 'github_token':re.compile(r'gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}'),
 'model_api_key':re.compile(r'sk-[A-Za-z0-9_-]{20,}'),
 'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')}


def allowed(relative):
    parts=relative.parts
    if len(parts)==1:return parts[0] in TOP
    if len(parts)==2:
        return parts[1] in {'implementation':IMPLEMENTATION,'spec':SPEC,'docs':DOCS,'results':RESULTS}.get(parts[0],set())
    return relative.as_posix()=='.github/workflows/reproduce.yml'


def public_files():
    result=[]
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts) or path.suffix=='.pyc':continue
        if path.is_symlink():raise AssertionError('Public symlink forbidden: '+relative.as_posix())
        if path.is_file():
            assert allowed(relative),'Unreviewed public path: '+relative.as_posix()
            result.append(path)
    return sorted(result)


def scan():
    secrets=[]
    for path in (ROOT/'.runtime').glob('run_*/connection.json'):
        cfg=json.loads(path.read_text(encoding='utf-8'))
        secrets.extend(value for value in cfg.get('passwords',{}).values() if isinstance(value,str) and len(value)>=32)
    files=public_files();findings=[]
    present={p.relative_to(ROOT).as_posix() for p in files}
    required={'implementation/'+name for name in IMPLEMENTATION}|{'spec/'+name for name in SPEC}
    required|={'README.md','NOTICE.md','CITATION.cff','.gitignore','.gitattributes','.github/workflows/reproduce.yml',
               'docs/TECHNICAL_NOTE_EN.md','docs/BILAN_FR.md'}
    required|={'results/'+name for name in RESULTS-{'publication_audit.json'}}
    assert required<=present,'Required public paths missing: '+str(sorted(required-present))
    for path in files:
        relative=path.relative_to(ROOT).as_posix()
        data=path.read_bytes();text=data.decode('utf-8-sig')
        for name,pattern in PATTERNS.items():
            if pattern.search(text):findings.append(dict(path=relative,reason=name))
        if any(value.encode() in data for value in secrets):
            findings.append(dict(path=relative,reason='generated_runtime_credential_value'))
    ids=identities()
    return dict(status='PASS' if not findings else 'FAIL',code_id=ids['code_id'],design_id=ids['design_id'],
        files_scanned=len(files),findings=findings,runtime_credential_values_checked=len(secrets),
        scope='Allowlisted UTF-8 text files only; no runtime/venv/binary/private chat archive is eligible',
        files=records([p for p in files if p.name!='MANIFEST.sha256' and p!=ROOT/'results/publication_audit.json']))


def write_manifest():
    report=scan();assert report['status']=='PASS',report['findings']
    (ROOT/'results/publication_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    entries=[sha(p)+'  '+p.relative_to(ROOT).as_posix() for p in public_files() if p!=ROOT/'MANIFEST.sha256']
    (ROOT/'MANIFEST.sha256').write_text('\n'.join(entries)+'\n',encoding='utf-8')
    return report


def verify_manifest():
    expected={}
    for line in (ROOT/'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
        digest,relative=line.split('  ',1)
        assert relative not in expected
        path=ROOT/relative
        assert path.resolve().is_relative_to(ROOT.resolve()) and allowed(Path(relative))
        assert sha(path)==digest,relative
        expected[relative]=digest
    actual={p.relative_to(ROOT).as_posix() for p in public_files() if p!=ROOT/'MANIFEST.sha256'}
    assert actual==set(expected)
    return dict(status='PASS',manifest_files=len(expected),code_id=identities()['code_id'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write-manifest',action='store_true');parser.add_argument('--verify-manifest',action='store_true');args=parser.parse_args()
    if args.verify_manifest:result=verify_manifest()
    elif args.write_manifest:result=write_manifest()
    else:result=scan()
    print(json.dumps({k:v for k,v in result.items() if k!='files'},indent=2))
    if result['status']!='PASS':raise SystemExit(1)
