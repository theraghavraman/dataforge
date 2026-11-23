import base64
import binascii
import csv
import hashlib
import io
import json
import re
from fastapi import HTTPException
from .db import canonical

FORBIDDEN = {'__proto__', 'prototype', 'constructor'}

def validate_document(doc, maximum):
    try: text = canonical(doc)
    except (ValueError, RecursionError): raise HTTPException(422, 'Invalid project document.')
    if len(text.encode()) > maximum: raise HTTPException(413, 'Project exceeds the 2 MiB document limit.')
    def visit(obj, depth=0):
        if depth > 40: raise HTTPException(422, 'Project nesting is too deep.')
        if isinstance(obj, dict):
            if FORBIDDEN.intersection(obj): raise HTTPException(422, 'Reserved property in project.')
            for v in obj.values(): visit(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj: visit(v, depth + 1)
    visit(doc)
    if doc:
        if doc.get('projectVersion') != '1.0': raise HTTPException(422, 'Expected a DataForge projectVersion 1.0 document, not a bundle or encrypted export.')
        if not isinstance(doc.get('project'), dict): raise HTTPException(422, 'Project metadata is required.')
        if not isinstance(doc.get('mappings', []), list): raise HTTPException(422, 'Mappings must be an array.')
        mapping_ids = set()
        for mapping in doc.get('mappings', []):
            if not isinstance(mapping, dict) or not isinstance(mapping.get('mapping'), dict): raise HTTPException(422, 'Invalid mapping.')
            mid = mapping['mapping'].get('id')
            if not isinstance(mid, str) or mid in mapping_ids: raise HTTPException(422, 'Missing or duplicate mapping ID.')
            mapping_ids.add(mid)
            nodes, links = mapping.get('nodes', []), mapping.get('links', [])
            if not isinstance(nodes, list) or not isinstance(links, list) or len(nodes) > 5000: raise HTTPException(422, 'Invalid mapping graph.')
            ids = set()
            for node in nodes:
                if not isinstance(node, dict) or not isinstance(node.get('id'), str) or node['id'] in ids: raise HTTPException(422, 'Missing or duplicate node ID.')
                ids.add(node['id'])
            for link in links:
                if not isinstance(link, dict): raise HTTPException(422, 'Invalid link.')
                # DataForge links use either string endpoints or endpoint objects.
                src = link.get('from'); dst = link.get('to')
                src = src.get('node') if isinstance(src, dict) else src
                dst = dst.get('node') if isinstance(dst, dict) else dst
                if src not in ids or dst not in ids: raise HTTPException(422, 'Mapping link references a missing node.')
    return text

def decode_csv(model, settings):
    if not re.fullmatch(r'[^/\\\x00-\x1f]{1,180}\.csv', model.name, re.I): raise HTTPException(422, 'Use a simple CSV filename without directory paths.')
    try: data = base64.b64decode(model.content_base64, validate=True)
    except (ValueError, binascii.Error): raise HTTPException(422, 'Invalid base64 file content.')
    if len(data) > settings.file_limit: raise HTTPException(413, 'CSV exceeds the 5 MiB file limit.')
    try:
        text = data.decode('utf-8-sig')
        if '\x00' in text: raise ValueError('NUL byte')
        rows = csv.reader(io.StringIO(text), strict=True)
        header = next(rows)
        if not header or len(header) > 128 or any(not h.strip() or len(h) > 128 for h in header): raise ValueError('Invalid headers')
        if len({h.casefold() for h in header}) != len(header): raise ValueError('Duplicate headers')
        count = 0
        for row in rows:
            if len(row) != len(header): raise ValueError('Column count mismatch')
            count += 1
            if count > 100000: raise ValueError('More than 100,000 rows')
    except (UnicodeError, ValueError, csv.Error, StopIteration):
        raise HTTPException(422, 'CSV must be UTF-8 with unique nonempty headers, consistent columns, and at most 100,000 rows.')
    return data, hashlib.sha256(data).hexdigest(), header, count

def validate_job(db, workspace, payload, enforce_approval=False):
    if enforce_approval and payload.get("pipeline"):
        from .pipeline import check_approval
        check_approval(db, workspace, payload["pipeline"])
    aliases = [s['table'].casefold() for s in payload['sources']]
    if len(set(aliases)) != len(aliases) or any(a.startswith('sqlite_') for a in aliases): raise HTTPException(422, 'Source table aliases must be unique and cannot start with sqlite_.')
    total = 0
    for s in payload['sources']:
        row = db.execute('SELECT size,sha256,content FROM files WHERE id=? AND workspace_id=?', (s['file_id'], workspace)).fetchone()
        if not row: raise HTTPException(404, 'A source file was not found in this workspace.')
        if hashlib.sha256(row['content']).hexdigest() != row['sha256'] or (s.get('sha256') and s['sha256'] != row['sha256']):
            raise HTTPException(409, 'Source checksum verification failed.')
        total += row['size']
    if total > 20 * 1024 * 1024: raise HTTPException(413, 'Job inputs exceed 20 MiB.')
