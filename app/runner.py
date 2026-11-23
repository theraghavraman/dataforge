"""Restricted CSV/SQLite SELECT executor, launched in a disposable subprocess.

This is intentionally NOT a DuckDB compatibility layer or an arbitrary-code sandbox.
Only SQLite query bytecode is accepted after input tables have been constructed.
"""
import base64
import csv
import io
import json
import math
import datetime
import re
import sqlite3
import sys
import time


def quote_identifier(value): return '"' + value.replace('"', '""') + '"'

def convert_value(value, kind):
    if kind == 'TEXT': return value
    if value == '': return None
    if kind == 'INTEGER':
        if not re.fullmatch(r'[+-]?\d+', value.strip()): raise ValueError('Invalid integer in typed CSV input.')
        number = int(value)
        if not -(2**63) <= number < 2**63: raise ValueError('Integer is outside the signed 64-bit range.')
        return number
    if kind == 'REAL':
        number = float(value)
        if not math.isfinite(number): raise ValueError('Non-finite number in typed CSV input.')
        return number
    if kind == 'BOOLEAN':
        values = {'true':1,'false':0,'1':1,'0':0}
        if value.strip().lower() not in values: raise ValueError('Invalid boolean in typed CSV input.')
        return values[value.strip().lower()]
    if kind == 'DATE':
        parsed = datetime.date.fromisoformat(value)
        if parsed.isoformat() != value: raise ValueError('Dates must use YYYY-MM-DD.')
        return value
    if kind == 'TIMESTAMP':
        datetime.datetime.fromisoformat(value)
        return value
    raise ValueError('Unsupported source column type.')

def execute(payload):
    started = time.monotonic()
    deadline = started + payload.get('timeout', 30)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (35, 36))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except ImportError:
        pass  # Parent timeout still applies; OS memory/CPU hard limits require POSIX.
    db = sqlite3.connect(':memory:')
    db.enable_load_extension(False)
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('PRAGMA trusted_schema=OFF')
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
    db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
    db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
    db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 32)
    db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
    for source in payload.get('sources', []):
        text = base64.b64decode(source['content_base64'], validate=True).decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text), strict=True)
        headers = next(reader)
        types = source.get('types', {})
        if any(h not in headers for h in types): raise ValueError('Typed schema references an unknown source column.')
        kinds = [types.get(h, 'TEXT') for h in headers]
        if any(k not in ('TEXT','INTEGER','REAL','BOOLEAN','DATE','TIMESTAMP') for k in kinds): raise ValueError('Invalid typed schema.')
        affinities = {'TEXT':'TEXT','INTEGER':'INTEGER','REAL':'REAL','BOOLEAN':'INTEGER','DATE':'TEXT','TIMESTAMP':'TEXT'}
        cols = ','.join(quote_identifier(h) + ' ' + affinities[k] for h,k in zip(headers,kinds))
        table = quote_identifier(source['table'])
        db.execute(f'CREATE TABLE {table}({cols})')
        def rows():
            for row in reader:
                if len(row) != len(headers): raise ValueError('CSV row width does not match its schema.')
                yield [convert_value(v,k) for v,k in zip(row,kinds)]
        db.executemany(f'INSERT INTO {table} VALUES({",".join("?" for _ in headers)})', rows())
    db.commit()
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    def authorize(action, arg1, arg2, database, trigger):
        if action not in allowed: return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in {'load_extension', 'readfile', 'writefile', 'fts3_tokenizer'}:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and (database not in (None, 'main', 'temp') or str(arg1).lower().startswith('sqlite_')):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    steps = 0
    def progress():
        nonlocal steps
        steps += 10000
        return int(time.monotonic() > deadline or steps > 50_000_000)
    db.set_authorizer(authorize)
    db.set_progress_handler(progress, 10000)
    cursor = db.execute(payload['sql'])  # Python execute rejects more than one statement.
    if cursor.description is None: raise ValueError('Only a SELECT query returning rows is supported.')
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchmany(payload.get('max_rows', 10000) + 1)
    if len(rows) > payload.get('max_rows', 10000): raise ValueError('Result exceeds 10,000 rows. Add a LIMIT or aggregate; no partial result was saved.')
    normalized = []
    for row in rows:
        out = []
        for v in row:
            if isinstance(v, bytes): raise ValueError('Binary SQL outputs are not supported.')
            if isinstance(v, float) and not math.isfinite(v): raise ValueError('Non-finite numeric result.')
            out.append(v)
        normalized.append(out)
    result = {'engine': 'sqlite', 'engine_version': sqlite3.sqlite_version, 'columns': columns, 'rows': normalized,
              'row_count': len(rows), 'elapsed_seconds': round(time.monotonic()-started, 4), 'truncated': False}
    if len(json.dumps(result).encode()) > 5 * 1024 * 1024: raise ValueError('Result exceeds 5 MiB. Reduce the query output.')
    db.close()
    return result

if __name__ == '__main__':
    try:
        result = execute(json.load(sys.stdin))
        sys.stdout.write(json.dumps({'ok': True, 'result': result}, allow_nan=False))
    except Exception as exc:
        sys.stdout.write(json.dumps({'ok': False, 'error': str(exc)[:500]}))
        sys.exit(1)
