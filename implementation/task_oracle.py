"""Literal frozen task expectations, independent of controller/admission logic."""
import json
from pathlib import Path

from db import connect

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=json.loads((ROOT/'spec/PILOT_FIXTURES.json').read_text(encoding='utf-8'))


def check(scenario,step,outcome):
    recipe=FIXTURES['scenarios'][scenario]
    assert outcome==recipe['expected_outcomes'][step],(scenario,step,outcome)
    expected=recipe['expected_checkpoints'][step]
    with connect('soma_migration') as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        row=con.execute("SELECT total,version,spent,held,unknown,policy_version FROM soma.tenant_state WHERE tenant_id='A'").fetchone()
        accounts=dict(con.execute("SELECT account_id,balance FROM soma.accounts WHERE tenant_id='A' AND account_id IN ('allocation_a','allocation_b')").fetchall())
    actual=dict(step=step,account_a=accounts['allocation_a'],account_b=accounts['allocation_b'],
        **dict(zip(('total','version','spent','held','unknown','policy_version'),row)))
    assert actual==expected,(scenario,step,expected,actual)
    return dict(status='PASS',**actual)
