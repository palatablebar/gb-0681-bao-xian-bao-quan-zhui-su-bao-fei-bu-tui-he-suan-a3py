import csv, io, json

def bundle(view):
    det = view['determination']
    settlement = view['settlement']
    version = settlement['version'] if settlement else ''
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['记录类型', '认定', '版本', '单据', '项目', '金额', '明细'])
    writer.writerow(['资格认定', det['id'], version, det['policy_id'], '豁免资格', '', json.dumps(det, ensure_ascii=False, sort_keys=True)])
    if settlement:
        writer.writerow(['豁免区间', det['id'], version, '', '豁免区间', '', json.dumps(
            {'waiver_start': settlement['waiver_start'], 'waiver_end': settlement['waiver_end'],
             'disability_date': det['disability_date'], 'confirm_date': det['confirm_date']}, ensure_ascii=False, sort_keys=True)])
        for disp in settlement['dispositions']:
            writer.writerow(['已缴处置', det['id'], version, disp['ref'], '主险缴费', disp['amount'], json.dumps(
                {'维持已缴': disp['kept'], '抵扣附加险': disp['offset'], '退回': disp['refund']}, ensure_ascii=False, sort_keys=True)])
        for line in settlement['receivables']:
            writer.writerow(['后续应收', det['id'], version, line['ref'], line['cover'], line['remaining'], json.dumps(
                {'应收': line['amount'], '已抵扣': line['offset'], '已出账': line['billed']}, ensure_ascii=False, sort_keys=True)])
        writer.writerow(['结算汇总', det['id'], version, '', '汇总', settlement['refund_total'], json.dumps(
            {'应退合计': settlement['refundable'], '抵扣合计': settlement['offset_total'],
             '净退费': settlement['refund_total'], '后续应收合计': settlement['receivable_remaining']}, ensure_ascii=False, sort_keys=True)])
    else:
        writer.writerow(['结算状态', det['id'], '', '', '保费去向未定', '', json.dumps({'status': '资格已认定，保费去向未定'}, ensure_ascii=False, sort_keys=True)])
    return '﻿' + out.getvalue()  # 与既有导出一致，前置 BOM 便于表格软件识别
