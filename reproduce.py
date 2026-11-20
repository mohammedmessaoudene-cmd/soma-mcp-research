"""Offline deterministic reception. This script does not call an AI model."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import json
import hashlib

ROOT = Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination',type=Path,help='New directory outside the package')
    args=p.parse_args()
    dest=args.destination.resolve()
    if dest.exists() or dest.is_relative_to(ROOT):
        raise ValueError('Choose a new directory outside this package')
    manifest=json.loads((ROOT/'MANIFEST.json').read_text(encoding='utf8'))
    for name, digest in manifest['files'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Package identity mismatch: '+name)
    dest.mkdir()
    kit=dest/'kit'; shutil.copytree(ROOT/'kit',kit)
    observations=[]
    def run(label, argv, cwd=kit):
        proc=subprocess.run([sys.executable,'-B',*map(str,argv)],cwd=cwd,capture_output=True)
        (dest/(label+'.stdout')).write_bytes(proc.stdout)
        (dest/(label+'.stderr')).write_bytes(proc.stderr)
        observations.append({'name':label,'exit_code':proc.returncode})
        if proc.returncode: raise RuntimeError(label+' failed; see preserved logs')
    run('tests',['-m','unittest','discover','-s','tests'])
    run('oracles',['-m','unittest','verification.test_oracles'])
    fixture=dest/'fixtures'; shutil.copytree(ROOT/'examples/directory',fixture)
    run('directory',['workflow.py','run',fixture/'old',fixture/'new',dest/'delivery'])
    run('replay',['workflow.py','replay',fixture/'old',dest/'delivery/result/patch',dest/'reconstructed'])
    stock=dest/'stock'; shutil.copytree(ROOT/'kit',stock)
    for path in (ROOT/'examples/stock').iterdir():
        if path.is_file(): shutil.copyfile(path,stock/path.name)
    run('stock',['run_reception.py','evidence'],stock)
    run('stock_oracle',['reception_oracle.py','evidence'],stock)
    data={'verdict':'PASS','mode':'DETERMINISTIC_NO_MODEL','checks':observations,
          'delivery_report':json.loads((dest/'delivery/result/report.json').read_text(encoding='utf8')),
          'stock_oracle':json.loads((stock/'evidence/oracle-verdict.json').read_text(encoding='utf8'))}
    (dest/'REPRODUCTION.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'verdict':'PASS','model_calls':0,'evidence':str(dest/'REPRODUCTION.json')}))

if __name__=='__main__': main()
