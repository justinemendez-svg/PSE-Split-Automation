"""AppFoundry dashboard with manual JSON uploads and PostgreSQL persistence."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, Response, jsonify, request
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from werkzeug.exceptions import HTTPException

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR.parent / "docs"
INDEX_HTML = (ASSET_DIR / "index.html").read_text(encoding="utf-8") if (ASSET_DIR / "index.html").exists() else ""
APP_JS = (ASSET_DIR / "app.js").read_text(encoding="utf-8") if (ASSET_DIR / "app.js").exists() else ""

import sys
sys.path.insert(0, str(APP_DIR.parent))
from snapshot import MAX_UPLOAD_BYTES, validate_snapshot
from workflow import CLOSED_STAGE, PARTNER_FIELD, SCOPE_VERSION

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES + 1024
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _database_url():
    for key in ('DATABASE_URL', 'POSTGRES_URL', 'POSTGRESQL_URL', 'PG_URL', 'DB_URL'):
        value = os.environ.get(key)
        if value:
            return value.replace('postgres://', 'postgresql://', 1) if value.startswith('postgres://') else value
    host = os.environ.get('DB_HOST') or os.environ.get('PGHOST') or os.environ.get('POSTGRES_HOST')
    user = os.environ.get('DB_USER') or os.environ.get('PGUSER') or os.environ.get('POSTGRES_USER')
    password = os.environ.get('DB_PASSWORD') or os.environ.get('PGPASSWORD') or os.environ.get('POSTGRES_PASSWORD', '')
    name = os.environ.get('DB_NAME') or os.environ.get('PGDATABASE') or os.environ.get('POSTGRES_DB')
    port = os.environ.get('DB_PORT') or os.environ.get('PGPORT', '5432')
    if host and user and name:
        return URL.create('postgresql', username=user, password=password or None, host=host,
                          port=int(port), database=name).render_as_string(hide_password=False)
    raise RuntimeError('AppFoundry Postgres is required. Attach a PostgreSQL database to this app.')


def db():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                engine = create_engine(_database_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)
                try:
                    with engine.begin() as cx:
                        cx.execute(text('''CREATE TABLE IF NOT EXISTS pse_split_snapshot (
                            id SMALLINT PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL,
                            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())'''))
                except Exception:
                    engine.dispose()
                    raise
                _ENGINE = engine
    return _ENGINE


def _load_snapshot():
    with db().begin() as cx:
        row = cx.execute(text('SELECT payload FROM pse_split_snapshot WHERE id=1')).first()
    return json.loads(row[0]) if row else {'generatedAt': None,
        'config': {'scopeVersion': SCOPE_VERSION, 'stage': CLOSED_STAGE, 'partnerField': PARTNER_FIELD},
        'summary': {'total': 0, 'tagged': 0, 'needsUpdate': 0}, 'rows': []}


@contextmanager
def snapshot_operation():
    with db().begin() as cx:
        cx.execute(text('SELECT pg_advisory_xact_lock(8773004)'))
        yield cx


def _save_snapshot(payload):
    with snapshot_operation() as cx:
        cx.execute(text('''INSERT INTO pse_split_snapshot (id, payload, refreshed_at) VALUES (1, :payload, NOW())
            ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, refreshed_at=NOW()'''),
            {'payload': json.dumps(payload, ensure_ascii=False, allow_nan=False)})


@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def index():
    return Response(INDEX_HTML, mimetype='text/html')


@app.get('/app.js')
def javascript():
    return Response(APP_JS, mimetype='application/javascript')


@app.get('/api/health')
def health():
    return jsonify({'uploadEnabled': True, 'refreshEnabled': False, 'writeEnabled': False,
                    'authMode': 'upload', 'loggedIn': False, 'scopeVersion': SCOPE_VERSION})


@app.get('/pse-data.json')
def data_json():
    return jsonify(_load_snapshot())


@app.post('/api/upload')
def upload():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get('manual') is not True:
        return jsonify({'error': 'Choose a data file and click Upload data to dashboard.'}), 400
    try:
        payload = validate_snapshot(body.get('snapshot'))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    payload['uploadedAt'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    _save_snapshot(payload)
    return jsonify({'ok': True, 'snapshot': payload})


@app.route('/api/refresh', methods=['POST'])
@app.route('/api/write', methods=['POST'])
@app.route('/api/write-all', methods=['POST'])
@app.route('/api/preview', methods=['POST'])
@app.route('/api/preview-all', methods=['POST'])
@app.route('/oauth/login')
@app.route('/oauth/callback')
def local_only():
    return jsonify({'error': 'This hosted dashboard uses uploaded data. Use the local app for Salesforce refreshes and updates.'}), 410


@app.errorhandler(Exception)
def app_error(error):
    if isinstance(error, HTTPException):
        message = 'Data file is too large. Maximum size is 25 MB.' if error.code == 413 else error.description
        return jsonify({'error': message}), error.code
    app.logger.exception('Dashboard request failed')
    return jsonify({'error': 'The saved data could not be accessed. Check the AppFoundry database configuration and runtime logs.'}), 503


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))
