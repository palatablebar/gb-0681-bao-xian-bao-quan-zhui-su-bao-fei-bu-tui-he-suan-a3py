import csv, io, json
from decimal import Decimal
import pytest
from app.modules.m8_waiver import model, rules

DET = {'policy_id': 'P900', 'main_cover': '主险', 'policy_start': '2026-01-01', 'policy_end': '2027-01-01',
       'disability_date': '2026-03-01', 'confirm_date': '2026-08-15'}
PAY = {'ref': 'PAY-1', 'paid_date': '2026-01-10', 'amount': '3650.00', 'period_start': '2026-01-01', 'period_end': '2027-01-01'}
BILLED_A = {'ref': 'BILL-1', 'cover': '附加医疗', 'amount': '600.00', 'due_date': '2026-09-01', 'bill_date': '2026-07-01'}
BILLED_B = {'ref': 'BILL-2', 'cover': '附加意外', 'amount': '400.00', 'due_date': '2026-12-01', 'bill_date': '2026-08-10'}
UNBILLED = {'ref': 'BILL-3', 'cover': '附加医疗', 'amount': '500.00', 'due_date': '2027-03-01'}

@pytest.fixture
def case(client):
    det = client.post('/api/m8_waiver/determinations', json=DET).get_json()
    det_id = det['determination']['id']
    client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=PAY)
    for receivable in (BILLED_A, BILLED_B, UNBILLED):
        client.post(f'/api/m8_waiver/determinations/{det_id}/receivables', json=receivable)
    return client, det_id

def settle(client, det_id):
    return client.post(f'/api/m8_waiver/determinations/{det_id}/settle').get_json()['settlement']

def test_seed_demo_pending(client):
    listed = client.get('/api/m8_waiver').get_json()
    demo = [d for d in listed if d['policy_id'] == 'P100']
    assert len(demo) == 1 and demo[0]['status'] == '已认定'

def test_initial_state_pending(case):
    client, det_id = case
    view = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()
    assert view['determination']['status'] == '已认定' and view['settlement'] is None
    page = client.get(f'/m8_waiver?determination={det_id}').data.decode()
    assert '保费去向未定' in page

def test_adjudicated_settlement(case):
    client, det_id = case
    s = settle(client, det_id)
    assert s['waiver_start'] == '2026-03-01' and s['waiver_end'] == '2027-01-01'
    assert s['refundable'] == '3060.00' and s['offset_total'] == '1000.00'
    assert s['refund_total'] == '2060.00' and s['receivable_remaining'] == '500.00'

def test_every_payment_has_disposition(case):
    client, det_id = case
    s = settle(client, det_id)
    view = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()
    assert {d['payment_id'] for d in s['dispositions']} == {p['id'] for p in view['payments']}
    for d in s['dispositions']:
        assert Decimal(d['kept']) + Decimal(d['offset']) + Decimal(d['refund']) == Decimal(d['amount'])
    disp = s['dispositions'][0]
    assert disp['kept'] == '590.00' and disp['offset'] == '1000.00' and disp['refund'] == '2060.00'

def test_offset_only_billed_receivables(case):
    client, det_id = case
    s = settle(client, det_id)
    lines = {l['ref']: l for l in s['receivables']}
    assert lines['BILL-1']['offset'] == '600.00' and lines['BILL-1']['remaining'] == '0.00'
    assert lines['BILL-2']['offset'] == '400.00' and lines['BILL-2']['remaining'] == '0.00'
    assert lines['BILL-3']['billed'] is False and lines['BILL-3']['offset'] == '0.00' and lines['BILL-3']['remaining'] == '500.00'

def test_billed_after_confirm_not_offset(client):
    det = dict(DET, policy_id='P901')
    det_id = client.post('/api/m8_waiver/determinations', json=det).get_json()['determination']['id']
    client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=PAY)
    client.post(f'/api/m8_waiver/determinations/{det_id}/receivables',
                json={'ref': 'LATE', 'amount': '800.00', 'due_date': '2026-10-01', 'bill_date': '2026-09-01'})
    s = settle(client, det_id)
    assert s['offset_total'] == '0.00' and s['refund_total'] == '3060.00' and s['receivable_remaining'] == '800.00'

def test_offset_capped_and_ordered(client):
    det = dict(DET, policy_id='P902')
    det_id = client.post('/api/m8_waiver/determinations', json=det).get_json()['determination']['id']
    client.post(f'/api/m8_waiver/determinations/{det_id}/payments',
                json={'ref': 'PAY-X', 'paid_date': '2026-04-01', 'amount': '1000.00', 'period_start': '2026-03-01', 'period_end': '2027-01-01'})
    client.post(f'/api/m8_waiver/determinations/{det_id}/receivables',
                json={'ref': 'R-A', 'amount': '800.00', 'due_date': '2026-09-01', 'bill_date': '2026-07-01'})
    client.post(f'/api/m8_waiver/determinations/{det_id}/receivables',
                json={'ref': 'R-B', 'amount': '800.00', 'due_date': '2026-10-01', 'bill_date': '2026-07-01'})
    s = settle(client, det_id)
    lines = {l['ref']: l for l in s['receivables']}
    assert s['offset_total'] == '1000.00' and s['refund_total'] == '0.00'
    assert lines['R-A']['remaining'] == '0.00' and lines['R-B']['remaining'] == '600.00'

def test_settle_idempotent_no_new_entries(case):
    client, det_id = case
    first = settle(client, det_id)
    with model.connection() as db:
        before = (db.execute('SELECT COUNT(*) c FROM waiver_settlements').fetchone()['c'],
                  db.execute('SELECT COUNT(*) c FROM waiver_entries').fetchone()['c'])
    second = settle(client, det_id)
    assert second['id'] == first['id'] and second['version'] == first['version']
    assert second['refund_total'] == first['refund_total'] and second['offset_total'] == first['offset_total']
    with model.connection() as db:
        after = (db.execute('SELECT COUNT(*) c FROM waiver_settlements').fetchone()['c'],
                 db.execute('SELECT COUNT(*) c FROM waiver_entries').fetchone()['c'])
    assert before == after

def test_replace_supersedes_old_version(case):
    client, det_id = case
    v1 = settle(client, det_id)
    client.post(f'/api/m8_waiver/determinations/{det_id}/payments',
                json={'ref': 'PAY-2', 'paid_date': '2026-04-01', 'amount': '500.00', 'period_start': '2026-03-01', 'period_end': '2026-06-01'})
    v2 = settle(client, det_id)
    assert v2['version'] == 2 and v2['refundable'] == '3560.00' and v2['refund_total'] == '2560.00'
    with model.connection() as db:
        rows = {r['id']: r['status'] for r in db.execute('SELECT * FROM waiver_settlements WHERE determination_id=?', (det_id,)).fetchall()}
        kept = db.execute('SELECT COUNT(*) c FROM waiver_entries WHERE settlement_id=?', (v1['id'],)).fetchone()['c']
    assert rows == {v1['id']: 'SUPERSEDED', v2['id']: 'ACTIVE'} and kept == len(v1['dispositions']) + len(v1['receivables'])

def test_interrupted_replace_keeps_old_version(case, monkeypatch):
    client, det_id = case
    v1 = settle(client, det_id)
    client.post(f'/api/m8_waiver/determinations/{det_id}/payments',
                json={'ref': 'PAY-2', 'paid_date': '2026-04-01', 'amount': '500.00', 'period_start': '2026-03-01', 'period_end': '2026-06-01'})
    def boom(db, settlement_id, plan): raise RuntimeError('换版中断')
    monkeypatch.setattr(model, 'insert_entries', boom)
    with pytest.raises(RuntimeError):
        client.post(f'/api/m8_waiver/determinations/{det_id}/settle')
    with model.connection() as db:
        rows = db.execute('SELECT * FROM waiver_settlements WHERE determination_id=?', (det_id,)).fetchall()
        entries = db.execute('SELECT COUNT(*) c FROM waiver_entries WHERE settlement_id=?', (v1['id'],)).fetchone()['c']
    assert len(rows) == 1 and rows[0]['id'] == v1['id'] and rows[0]['status'] == 'ACTIVE'
    assert entries == len(v1['dispositions']) + len(v1['receivables'])
    current = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()['settlement']
    assert current['id'] == v1['id'] and current['refund_total'] == v1['refund_total']

def test_delete_determination_locked(case):
    client, det_id = case
    settle(client, det_id)
    r = client.delete(f'/api/m8_waiver/determinations/{det_id}')
    assert r.status_code == 409 and r.get_json()['code'] == 'LOCKED'
    assert client.get(f'/api/m8_waiver/determinations/{det_id}').status_code == 200

def test_delete_determination_allowed_before_settle(case):
    client, det_id = case
    assert client.delete(f'/api/m8_waiver/determinations/{det_id}').status_code == 200
    assert client.get(f'/api/m8_waiver/determinations/{det_id}').status_code == 404

def test_delete_payment_locked(case):
    client, det_id = case
    settle(client, det_id)
    payment_id = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()['payments'][0]['id']
    r = client.delete(f'/api/m8_waiver/payments/{payment_id}')
    assert r.status_code == 409 and r.get_json()['code'] == 'LOCKED'
    assert payment_id in {p['id'] for p in client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()['payments']}

def test_delete_payment_allowed_before_settle(case):
    client, det_id = case
    payment_id = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()['payments'][0]['id']
    assert client.delete(f'/api/m8_waiver/payments/{payment_id}').status_code == 200
    assert client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()['payments'] == []

def test_page_and_csv_match_api(case):
    client, det_id = case
    settle(client, det_id)
    view = client.get(f'/api/m8_waiver/determinations/{det_id}').get_json()
    s = view['settlement']
    rows = list(csv.reader(io.StringIO(client.get(f'/api/m8_waiver/determinations/{det_id}/settlement.csv').data.decode('utf-8-sig'))))
    by_type = {}
    for row in rows[1:]: by_type.setdefault(row[0], []).append(row)
    period = json.loads(by_type['豁免区间'][0][6])
    assert period['waiver_start'] == s['waiver_start'] and period['waiver_end'] == s['waiver_end']
    csv_disp = {r[3]: json.loads(r[6]) for r in by_type['已缴处置']}
    for d in s['dispositions']:
        assert csv_disp[d['ref']] == {'维持已缴': d['kept'], '抵扣附加险': d['offset'], '退回': d['refund']}
    csv_recv = {r[3]: (r[5], json.loads(r[6])) for r in by_type['后续应收']}
    for l in s['receivables']:
        remaining, detail = csv_recv[l['ref']]
        assert remaining == l['remaining'] and detail['应收'] == l['amount'] and detail['已抵扣'] == l['offset']
    summary = json.loads(by_type['结算汇总'][0][6])
    assert summary['净退费'] == s['refund_total'] and summary['后续应收合计'] == s['receivable_remaining']
    page = client.get(f'/m8_waiver?determination={det_id}').data.decode()
    for text in (s['waiver_start'], s['waiver_end'], s['refund_total'], s['receivable_remaining']):
        assert text in page
    for d in s['dispositions']: assert d['refund'] in page
    for l in s['receivables']: assert l['remaining'] in page

def test_pending_csv_marks_undetermined(case):
    client, det_id = case
    text = client.get(f'/api/m8_waiver/determinations/{det_id}/settlement.csv').data.decode('utf-8-sig')
    assert '保费去向未定' in text

def test_rounding_conservation():
    det = {'disability_date': '2026-03-01', 'policy_start': '2026-01-01', 'policy_end': '2027-01-01', 'confirm_date': '2026-08-15'}
    payments = [{'id': 'P1', 'ref': 'a', 'paid_date': '2026-01-01', 'amount': '1000.00', 'period_start': '2026-01-01', 'period_end': '2026-04-01'}]
    plan = rules.adjudicate(det, payments, [])
    disp = plan['dispositions'][0]
    assert disp['waived'] == Decimal('344.44') and disp['kept'] == Decimal('655.56')
    assert disp['kept'] + disp['offset'] + disp['refund'] == Decimal('1000.00')

def test_determination_post_idempotent_and_conflict(client):
    a = client.post('/api/m8_waiver/determinations', json=DET).get_json()['determination']['id']
    b = client.post('/api/m8_waiver/determinations', json=DET).get_json()['determination']['id']
    assert a == b
    listed = client.get('/api/m8_waiver').get_json()
    assert len([d for d in listed if d['policy_id'] == 'P900']) == 1
    r = client.post('/api/m8_waiver/determinations', json=dict(DET, policy_end='2027-02-01'))
    assert r.status_code == 409 and r.get_json()['code'] == 'CONFLICT'

def test_payment_ref_conflict(client):
    det_id = client.post('/api/m8_waiver/determinations', json=DET).get_json()['determination']['id']
    assert client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=PAY).status_code == 200
    assert client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=PAY).status_code == 200
    r = client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=dict(PAY, amount='100.00'))
    assert r.status_code == 409 and r.get_json()['code'] == 'CONFLICT'

def test_validation_errors(client):
    det_id = client.post('/api/m8_waiver/determinations', json=DET).get_json()['determination']['id']
    bad_det = client.post('/api/m8_waiver/determinations', json=dict(DET, policy_id='P903', disability_date='2027-06-01'))
    assert bad_det.status_code == 422 and bad_det.get_json()['code'] == 'RANGE'
    missing = client.post('/api/m8_waiver/determinations', json={'policy_id': 'P904'})
    assert missing.status_code == 422 and missing.get_json()['code'] == 'MISSING'
    bad_amount = client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=dict(PAY, ref='BAD-1', amount='10.005'))
    assert bad_amount.status_code == 422 and bad_amount.get_json()['code'] == 'NUMBER'
    negative = client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=dict(PAY, ref='BAD-2', amount='-1.00'))
    assert negative.status_code == 422 and negative.get_json()['code'] == 'RANGE'
    reversed_period = client.post(f'/api/m8_waiver/determinations/{det_id}/payments',
                                  json=dict(PAY, ref='BAD-3', period_start='2026-06-01', period_end='2026-01-01'))
    assert reversed_period.status_code == 422 and reversed_period.get_json()['code'] == 'RANGE'
    bad_date = client.post(f'/api/m8_waiver/determinations/{det_id}/payments', json=dict(PAY, ref='BAD-4', paid_date='2026-13-01'))
    assert bad_date.status_code == 422 and bad_date.get_json()['code'] == 'DATE'

def test_settle_unknown_determination(client):
    assert client.post('/api/m8_waiver/determinations/W0000000000000/settle').status_code == 404
