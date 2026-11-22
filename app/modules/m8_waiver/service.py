import hashlib, json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from app.config import RULE_VERSION
from app.errors import DomainError
from app.modules.m2_mapping.coerce import number, day
from app.modules.m8_waiver import model, rules

CENT = Decimal('.01')

def _now(): return datetime.now(timezone.utc).isoformat()

def _required(data, field):
    value = data.get(field)
    if value is None or str(value).strip() == '': raise DomainError('MISSING', f'缺少必填值 {field}')
    return str(value).strip()

def _money(value):
    if value is None or str(value).strip() == '': raise DomainError('MISSING', '金额缺失')
    result = number(value)
    rounded = result.quantize(CENT, rounding=ROUND_HALF_UP)
    if result != rounded: raise DomainError('NUMBER', '金额须精确到分')
    return rounded

def _key(*parts): return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:14]

def _content_hash(fields): return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

def create_determination(data):
    det = {'policy_id': _required(data, 'policy_id'),
        'main_cover': str(data.get('main_cover') or '主险').strip() or '主险',
        'policy_start': day(_required(data, 'policy_start')),
        'policy_end': day(_required(data, 'policy_end')),
        'disability_date': day(_required(data, 'disability_date')),
        'confirm_date': day(_required(data, 'confirm_date'))}
    if det['policy_start'] >= det['policy_end']: raise DomainError('RANGE', '保单区间无效')
    if det['disability_date'] >= det['policy_end']: raise DomainError('RANGE', '失能日不在保单期间内')
    det['id'] = 'W' + _key(det['policy_id'], det['disability_date'], det['confirm_date'])
    det['content_hash'] = _content_hash(det)
    det['created_at'] = _now()
    with model.connection() as db:
        existing = model.get_determination(db, det['id'])
        if existing:
            if existing['content_hash'] != det['content_hash']: raise DomainError('CONFLICT', '同一认定键内容冲突', 409)
        else: model.insert_determination(db, det)
    return view(det['id'])

def add_payment(det_id, data):
    with model.connection() as db:
        det = model.get_determination(db, det_id)
        if not det: raise DomainError('NOT_FOUND', '认定不存在', 404)
        payment = {'policy_id': det['policy_id'], 'ref': _required(data, 'ref'),
            'paid_date': day(_required(data, 'paid_date')),
            'amount': str(_money(data.get('amount'))),
            'period_start': day(_required(data, 'period_start')),
            'period_end': day(_required(data, 'period_end'))}
        if Decimal(payment['amount']) <= 0: raise DomainError('RANGE', '缴费金额必须为正')
        if payment['period_start'] >= payment['period_end']: raise DomainError('RANGE', '缴费对应区间无效')
        payment['id'] = 'P' + _key(payment['policy_id'], payment['ref'])
        payment['created_at'] = _now()
        existing = model.get_payment(db, payment['id'])
        if existing:
            if any(existing[k] != payment[k] for k in ('paid_date', 'amount', 'period_start', 'period_end')):
                raise DomainError('CONFLICT', '同一缴费流水内容冲突', 409)
        else: model.insert_payment(db, payment)
    return view(det_id)

def add_receivable(det_id, data):
    with model.connection() as db:
        det = model.get_determination(db, det_id)
        if not det: raise DomainError('NOT_FOUND', '认定不存在', 404)
        raw_bill = data.get('bill_date')
        receivable = {'policy_id': det['policy_id'], 'ref': _required(data, 'ref'),
            'cover': str(data.get('cover') or '附加险').strip() or '附加险',
            'amount': str(_money(data.get('amount'))),
            'due_date': day(_required(data, 'due_date')),
            'bill_date': day(raw_bill) if raw_bill and str(raw_bill).strip() else None}
        if Decimal(receivable['amount']) <= 0: raise DomainError('RANGE', '应收金额必须为正')
        receivable['id'] = 'R' + _key(receivable['policy_id'], receivable['ref'])
        receivable['created_at'] = _now()
        existing = model.get_receivable(db, receivable['id'])
        if existing:
            if any(existing[k] != receivable[k] for k in ('cover', 'amount', 'due_date', 'bill_date')):
                raise DomainError('CONFLICT', '同一账单号内容冲突', 409)
        else: model.insert_receivable(db, receivable)
    return view(det_id)

def _input_hash(det, payments, receivables):
    canon = {'rule': RULE_VERSION,
        'determination': {k: det[k] for k in ('policy_id', 'main_cover', 'policy_start', 'policy_end', 'disability_date', 'confirm_date')},
        'payments': [{k: p[k] for k in ('id', 'paid_date', 'amount', 'period_start', 'period_end')} for p in sorted(payments, key=lambda x: x['id'])],
        'receivables': [{k: (r[k] or '') for k in ('id', 'cover', 'amount', 'due_date', 'bill_date')} for r in sorted(receivables, key=lambda x: x['id'])]}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

def settle(det_id):
    with model.connection() as db:
        det = model.get_determination(db, det_id)
        if not det: raise DomainError('NOT_FOUND', '认定不存在', 404)
        payments = model.payments_of(db, det['policy_id'])
        receivables = model.receivables_of(db, det['policy_id'])
        digest = _input_hash(det, payments, receivables)
        active = model.active_settlement(db, det_id)
        if active and active['input_hash'] == digest:
            return view(det_id, db)  # 同一认定重跑幂等，不新增退回或抵扣
        plan = rules.adjudicate(det, payments, receivables)
        version = model.max_version(db, det_id) + 1
        settlement = {'id': f'{det_id}-v{version}', 'determination_id': det_id, 'version': version, 'status': 'ACTIVE',
            'input_hash': digest, 'waiver_start': plan['waiver_start'], 'waiver_end': plan['waiver_end'],
            'refundable': str(plan['refundable']), 'offset_total': str(plan['offset_total']),
            'refund_total': str(plan['refund_total']), 'receivable_remaining': str(plan['receivable_remaining']),
            'created_at': _now()}
        # 单事务换版：中断即回滚，旧版保持 ACTIVE 且完整
        model.insert_settlement(db, settlement)
        model.insert_entries(db, settlement['id'], plan)
        if active: model.supersede(db, active['id'])
    return view(det_id)

def remove_determination(det_id):
    with model.connection() as db:
        if not model.get_determination(db, det_id): raise DomainError('NOT_FOUND', '认定不存在', 404)
        if model.settlement_count(db, det_id): raise DomainError('LOCKED', '认定已被结算引用，删除将使结算无法解释', 409)
        model.delete_determination(db, det_id)
    return {'deleted': det_id}

def remove_payment(payment_id):
    with model.connection() as db:
        if not model.get_payment(db, payment_id): raise DomainError('NOT_FOUND', '缴费证据不存在', 404)
        if model.payment_references(db, payment_id): raise DomainError('LOCKED', '缴费证据已被结算引用，删除将使结算无法解释', 409)
        model.delete_payment(db, payment_id)
    return {'deleted': payment_id}

def list_determinations():
    with model.connection() as db: rows = model.list_determinations(db)
    return [{'id': r['id'], 'policy_id': r['policy_id'], 'disability_date': r['disability_date'],
        'confirm_date': r['confirm_date'], 'status': '已结算' if r['version'] else '已认定', 'version': r['version']} for r in rows]

def view(det_id, db=None):
    if db is None:
        with model.connection() as conn: return view(det_id, conn)
    det = model.get_determination(db, det_id)
    if not det: raise DomainError('NOT_FOUND', '认定不存在', 404)
    active = model.active_settlement(db, det_id)
    settlement = None
    if active:
        entries = model.entries_of(db, active['id'])
        settlement = {'id': active['id'], 'version': active['version'],
            'waiver_start': active['waiver_start'], 'waiver_end': active['waiver_end'],
            'refundable': active['refundable'], 'offset_total': active['offset_total'],
            'refund_total': active['refund_total'], 'receivable_remaining': active['receivable_remaining'],
            'dispositions': [json.loads(e['detail']) for e in entries if e['kind'] == 'PAYMENT'],
            'receivables': [json.loads(e['detail']) for e in entries if e['kind'] == 'RECEIVABLE']}
    determination = {k: det[k] for k in ('id', 'policy_id', 'main_cover', 'policy_start', 'policy_end', 'disability_date', 'confirm_date')}
    determination['status'] = '已结算' if active else '已认定'
    return {'determination': determination, 'payments': model.payments_of(db, det['policy_id']),
        'receivables_master': model.receivables_of(db, det['policy_id']), 'settlement': settlement}

DEMO_DET = {'policy_id': 'P100', 'main_cover': '主险（重疾）', 'policy_start': '2026-01-01', 'policy_end': '2027-01-01',
    'disability_date': '2026-03-01', 'confirm_date': '2026-08-15'}
DEMO_PAYMENTS = [{'ref': 'PAY-2026-001', 'paid_date': '2026-01-10', 'amount': '3650.00', 'period_start': '2026-01-01', 'period_end': '2027-01-01'}]
DEMO_RECEIVABLES = [
    {'ref': 'BILL-2026-0701', 'cover': '附加医疗险', 'amount': '600.00', 'due_date': '2026-09-01', 'bill_date': '2026-07-01'},
    {'ref': 'BILL-2026-0810', 'cover': '附加意外险', 'amount': '400.00', 'due_date': '2026-12-01', 'bill_date': '2026-08-10'},
    {'ref': 'BILL-2027-0301', 'cover': '附加医疗险', 'amount': '500.00', 'due_date': '2027-03-01'}]

def seed_demo():
    # 演示案件：资格已认定但保费去向未定，等待执行结算
    with model.connection() as db:
        if model.count_determinations(db): return
    det_id = create_determination(DEMO_DET)['determination']['id']
    for payment in DEMO_PAYMENTS: add_payment(det_id, payment)
    for receivable in DEMO_RECEIVABLES: add_receivable(det_id, receivable)
