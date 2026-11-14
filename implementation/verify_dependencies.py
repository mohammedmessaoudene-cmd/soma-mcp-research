"""Verify the installed environment against every exact declared package pin."""
import importlib.metadata
from pathlib import Path
import sys


def verify():
    assert sys.version_info[:2]==(3,12), 'Use Python 3.12'
    result={}
    for line in (Path(__file__).resolve().parent/'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():continue
        name,version=line.strip().split('==')
        observed=importlib.metadata.version(name)
        assert observed==version,(name,version,observed)
        result[name]=observed
    return result


if __name__=='__main__':
    print('Exact dependency pins verified:',len(verify()))
