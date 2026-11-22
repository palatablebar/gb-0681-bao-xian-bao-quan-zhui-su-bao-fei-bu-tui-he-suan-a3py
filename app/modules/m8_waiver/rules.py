from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('.01')

def _days(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days

def waiver_period(det):
    # 豁免区间 = [max(失能日, 主险保单起始), 主险保单终止)，半开区间
    return max(det['disability_date'], det['policy_start']), det['policy_end']

def adjudicate(det, payments, receivables):
    # 唯一结算裁定：应退先抵扣已出账附加险应收（寄出日≤认定确认日），余额退现金；未出账应收不抵扣。
    w_start, w_end = waiver_period(det)
    dispositions = []
    refundable = Decimal('0')
    for p in sorted(payments, key=lambda x: (x['paid_date'], x['id'])):
        amount = Decimal(p['amount'])
        span = _days(p['period_start'], p['period_end'])
        left, right = max(p['period_start'], w_start), min(p['period_end'], w_end)
        waived = (amount * _days(left, right) / span).quantize(CENT, rounding=ROUND_HALF_UP) if left < right else Decimal('0.00')
        waived = min(waived, amount)
        dispositions.append({'payment_id': p['id'], 'ref': p['ref'], 'paid_date': p['paid_date'],
            'amount': amount, 'kept': amount - waived, 'waived': waived, 'offset': Decimal('0.00'), 'refund': Decimal('0.00')})
        refundable += waived
    confirm = det['confirm_date']
    billed = sorted((r for r in receivables if r['bill_date'] and r['bill_date'] <= confirm), key=lambda x: (x['due_date'], x['id']))
    deferred = sorted((r for r in receivables if not (r['bill_date'] and r['bill_date'] <= confirm)), key=lambda x: (x['due_date'], x['id']))
    pool = refundable
    lines = []
    for r in billed:
        amount = Decimal(r['amount'])
        take = min(amount, pool)
        pool -= take
        lines.append({'receivable_id': r['id'], 'ref': r['ref'], 'cover': r['cover'], 'billed': True,
                      'amount': amount, 'offset': take, 'remaining': amount - take})
    for r in deferred:
        amount = Decimal(r['amount'])
        lines.append({'receivable_id': r['id'], 'ref': r['ref'], 'cover': r['cover'], 'billed': False,
                      'amount': amount, 'offset': Decimal('0.00'), 'remaining': amount})
    offset_total = refundable - pool
    remaining_offset = offset_total
    for disp in dispositions:
        take = min(disp['waived'], remaining_offset)
        disp['offset'] = take
        disp['refund'] = disp['waived'] - take
        remaining_offset -= take
    return {'waiver_start': w_start, 'waiver_end': w_end, 'refundable': refundable,
            'offset_total': offset_total, 'refund_total': pool,
            'receivable_remaining': sum((line['remaining'] for line in lines), Decimal('0')),
            'dispositions': dispositions, 'receivables': lines}
