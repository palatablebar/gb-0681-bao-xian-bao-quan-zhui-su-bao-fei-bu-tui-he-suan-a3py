import json
from app import store

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS waiver_determinations(
        id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        main_cover TEXT NOT NULL,
        policy_start TEXT NOT NULL,
        policy_end TEXT NOT NULL,
        disability_date TEXT NOT NULL,
        confirm_date TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS waiver_payments(
        id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        ref TEXT NOT NULL,
        paid_date TEXT NOT NULL,
        amount TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(policy_id,ref))""",
    """CREATE TABLE IF NOT EXISTS waiver_receivables(
        id TEXT PRIMARY KEY,
        policy_id TEXT NOT NULL,
        ref TEXT NOT NULL,
        cover TEXT NOT NULL,
        amount TEXT NOT NULL,
        due_date TEXT NOT NULL,
        bill_date TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(policy_id,ref))""",
    """CREATE TABLE IF NOT EXISTS waiver_settlements(
        id TEXT PRIMARY KEY,
        determination_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        waiver_start TEXT NOT NULL,
        waiver_end TEXT NOT NULL,
        refundable TEXT NOT NULL,
        offset_total TEXT NOT NULL,
        refund_total TEXT NOT NULL,
        receivable_remaining TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(determination_id,version))""",
    """CREATE TABLE IF NOT EXISTS waiver_entries(
        settlement_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        detail TEXT NOT NULL,
        PRIMARY KEY(settlement_id,seq))""",
)

def connection():
    db = store.connection()
    for ddl in SCHEMA: db.execute(ddl)
    return db

def _dict(row): return dict(row) if row is not None else None

def get_determination(db, det_id):
    return _dict(db.execute('SELECT * FROM waiver_determinations WHERE id=?', (det_id,)).fetchone())

def get_payment(db, payment_id):
    return _dict(db.execute('SELECT * FROM waiver_payments WHERE id=?', (payment_id,)).fetchone())

def get_receivable(db, receivable_id):
    return _dict(db.execute('SELECT * FROM waiver_receivables WHERE id=?', (receivable_id,)).fetchone())

def payments_of(db, policy_id):
    return [dict(r) for r in db.execute('SELECT * FROM waiver_payments WHERE policy_id=? ORDER BY paid_date,id', (policy_id,)).fetchall()]

def receivables_of(db, policy_id):
    return [dict(r) for r in db.execute('SELECT * FROM waiver_receivables WHERE policy_id=? ORDER BY due_date,id', (policy_id,)).fetchall()]

def active_settlement(db, det_id):
    return _dict(db.execute("SELECT * FROM waiver_settlements WHERE determination_id=? AND status='ACTIVE'", (det_id,)).fetchone())

def entries_of(db, settlement_id):
    return [dict(r) for r in db.execute('SELECT * FROM waiver_entries WHERE settlement_id=? ORDER BY seq', (settlement_id,)).fetchall()]

def max_version(db, det_id):
    return db.execute('SELECT COALESCE(MAX(version),0) v FROM waiver_settlements WHERE determination_id=?', (det_id,)).fetchone()['v']

def settlement_count(db, det_id):
    return db.execute('SELECT COUNT(*) c FROM waiver_settlements WHERE determination_id=?', (det_id,)).fetchone()['c']

def payment_references(db, payment_id):
    return db.execute("SELECT COUNT(*) c FROM waiver_entries WHERE kind='PAYMENT' AND ref_id=?", (payment_id,)).fetchone()['c']

def count_determinations(db):
    return db.execute('SELECT COUNT(*) c FROM waiver_determinations').fetchone()['c']

def list_determinations(db):
    return [dict(r) for r in db.execute("""SELECT d.id,d.policy_id,d.disability_date,d.confirm_date,
        (SELECT version FROM waiver_settlements s WHERE s.determination_id=d.id AND s.status='ACTIVE') AS version
        FROM waiver_determinations d ORDER BY d.created_at,d.id""").fetchall()]

def insert_determination(db, det):
    db.execute('INSERT INTO waiver_determinations(id,policy_id,main_cover,policy_start,policy_end,disability_date,confirm_date,content_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (det['id'], det['policy_id'], det['main_cover'], det['policy_start'], det['policy_end'], det['disability_date'], det['confirm_date'], det['content_hash'], det['created_at']))

def insert_payment(db, payment):
    db.execute('INSERT INTO waiver_payments(id,policy_id,ref,paid_date,amount,period_start,period_end,created_at) VALUES (?,?,?,?,?,?,?,?)',
        (payment['id'], payment['policy_id'], payment['ref'], payment['paid_date'], payment['amount'], payment['period_start'], payment['period_end'], payment['created_at']))

def insert_receivable(db, receivable):
    db.execute('INSERT INTO waiver_receivables(id,policy_id,ref,cover,amount,due_date,bill_date,created_at) VALUES (?,?,?,?,?,?,?,?)',
        (receivable['id'], receivable['policy_id'], receivable['ref'], receivable['cover'], receivable['amount'], receivable['due_date'], receivable['bill_date'], receivable['created_at']))

def insert_settlement(db, settlement):
    db.execute('INSERT INTO waiver_settlements(id,determination_id,version,status,input_hash,waiver_start,waiver_end,refundable,offset_total,refund_total,receivable_remaining,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (settlement['id'], settlement['determination_id'], settlement['version'], settlement['status'], settlement['input_hash'],
         settlement['waiver_start'], settlement['waiver_end'], settlement['refundable'], settlement['offset_total'], settlement['refund_total'], settlement['receivable_remaining'], settlement['created_at']))

def insert_entries(db, settlement_id, plan):
    seq = 0
    for disp in plan['dispositions']:
        seq += 1
        db.execute('INSERT INTO waiver_entries(settlement_id,seq,kind,ref_id,detail) VALUES (?,?,?,?,?)',
            (settlement_id, seq, 'PAYMENT', disp['payment_id'], json.dumps(disp, ensure_ascii=False, sort_keys=True, default=str)))
    for line in plan['receivables']:
        seq += 1
        db.execute('INSERT INTO waiver_entries(settlement_id,seq,kind,ref_id,detail) VALUES (?,?,?,?,?)',
            (settlement_id, seq, 'RECEIVABLE', line['receivable_id'], json.dumps(line, ensure_ascii=False, sort_keys=True, default=str)))

def supersede(db, settlement_id):
    db.execute("UPDATE waiver_settlements SET status='SUPERSEDED' WHERE id=? AND status='ACTIVE'", (settlement_id,))

def delete_determination(db, det_id):
    db.execute('DELETE FROM waiver_determinations WHERE id=?', (det_id,))

def delete_payment(db, payment_id):
    db.execute('DELETE FROM waiver_payments WHERE id=?', (payment_id,))
