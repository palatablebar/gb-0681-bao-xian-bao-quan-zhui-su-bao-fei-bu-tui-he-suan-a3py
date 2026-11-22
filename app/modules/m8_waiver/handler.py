from datetime import datetime, timezone
from flask import Blueprint, Response, jsonify, render_template, request
from app.config import EXTRA_NAV, MODULES, RULE_VERSION, SHORT, TITLE
from app.errors import DomainError
from app.modules.m8_waiver import service
from app.modules.m8_waiver.export_out import bundle

bp = Blueprint('m8_waiver', __name__)

def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict): raise DomainError('PAYLOAD', '请求必须是对象')
    return data

@bp.get('/m8_waiver')
def screen():
    det_id = request.args.get('determination')
    determinations = service.list_determinations()
    if not det_id and determinations: det_id = determinations[0]['id']
    detail = service.view(det_id) if det_id else None
    data = {'run_id': det_id or 'waiver', 'input_count': len(determinations), 'rows': [], 'quarantine': [], 'business': None,
        'manifest': {'generated_at': datetime.now(timezone.utc).isoformat(), 'rule_version': RULE_VERSION, 'parameters': {}}}
    run = {'decision': detail['determination']['status'] if detail else '豁免结算',
        'review_version': detail['settlement']['version'] if detail and detail['settlement'] else 0}
    return render_template('m8_waiver.html', title=TITLE, short=SHORT, modules=MODULES + EXTRA_NAV, current=8,
        data=data, run=run, history=[], determinations=determinations, detail=detail)

@bp.get('/api/m8_waiver')
def listing(): return jsonify(service.list_determinations())

@bp.post('/api/m8_waiver/determinations')
def create(): return jsonify(service.create_determination(_body()))

@bp.get('/api/m8_waiver/determinations/<det_id>')
def detail(det_id): return jsonify(service.view(det_id))

@bp.delete('/api/m8_waiver/determinations/<det_id>')
def drop_determination(det_id): return jsonify(service.remove_determination(det_id))

@bp.post('/api/m8_waiver/determinations/<det_id>/payments')
def pay(det_id): return jsonify(service.add_payment(det_id, _body()))

@bp.delete('/api/m8_waiver/payments/<payment_id>')
def drop_payment(payment_id): return jsonify(service.remove_payment(payment_id))

@bp.post('/api/m8_waiver/determinations/<det_id>/receivables')
def receive(det_id): return jsonify(service.add_receivable(det_id, _body()))

@bp.post('/api/m8_waiver/determinations/<det_id>/settle')
def do_settle(det_id): return jsonify(service.settle(det_id))

@bp.get('/api/m8_waiver/determinations/<det_id>/settlement.csv')
def download(det_id):
    return Response(bundle(service.view(det_id)), mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=waiver_{det_id}.csv'})
