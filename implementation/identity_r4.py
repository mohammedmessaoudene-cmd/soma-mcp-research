"""Content identity of executable inputs and the predeclared R4 contract."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def records(paths):
    return sorted([dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p)) for p in paths],key=lambda r:r['path'])


def content_id(items):
    return hashlib.sha256(json.dumps(items,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def identities():
    sources=records([p for p in (ROOT/'implementation').rglob('*') if p.is_file() and p.suffix in ('.py','.sql','.ps1','.txt')]
                    + [p for p in (ROOT/'.github/workflows').glob('*.yml')]
                    + [ROOT/'.gitattributes',ROOT/'.gitignore'])
    designs=records([ROOT/'spec/R4_CONTRACT.md',ROOT/'spec/ACCEPTANCE_R4.json',ROOT/'spec/adjust_allocation.schema.json',ROOT/'spec/PILOT_FIXTURES.json'])
    return dict(code_id=content_id(sources),design_id=content_id(designs),source_files=sources,design_files=designs)


if __name__=='__main__':
    print(json.dumps(identities(),indent=2))
