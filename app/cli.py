import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path
from .config import Settings, trusted_proxies
from .db import initialize, transaction, verify_audit, audit
from .security import create_user, PH, validate_password, email


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='DataForge Team administration (single-host reference deployment).')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='Create an empty schema; does not add default credentials.')
    for command in ('create-admin', 'create-user'):
        p = sub.add_parser(command); p.add_argument('--email', required=True); p.add_argument('--name', required=True)
    p = sub.add_parser('reset-password'); p.add_argument('--email', required=True)
    p = sub.add_parser('serve'); p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=8080)
    p = sub.add_parser('worker'); p.add_argument('--once', action='store_true')
    p = sub.add_parser('keygen'); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('backup'); p.add_argument('--output', type=Path, required=True); p.add_argument('--key-file', type=Path, required=True)
    p = sub.add_parser('restore'); p.add_argument('--input', type=Path, required=True); p.add_argument('--destination', type=Path, required=True); p.add_argument('--key-file', type=Path, required=True)
    sub.add_parser('verify-audit')
    sub.add_parser('preflight', help='Check production gates; exits nonzero while any gate is unmet.')
    p = sub.add_parser('maintenance'); p.add_argument('--once', action='store_true')
    p = sub.add_parser('oidc-link'); p.add_argument('--email', required=True); p.add_argument('--issuer', required=True); p.add_argument('--subject', required=True)
    p = sub.add_parser('oidc-unlink'); p.add_argument('--issuer', required=True); p.add_argument('--subject', required=True)
    p = sub.add_parser('reset-mfa'); p.add_argument('--email', required=True); p.add_argument('--reason', required=True)

    p = sub.add_parser('prune'); p.add_argument('--days', type=int, default=90); p.add_argument('--apply', action='store_true')
    args = parser.parse_args(); settings = Settings.from_env()
    if args.command in ('keygen', 'restore'):
        from .backup import key_create, restore
        if args.command == 'keygen': key_create(args.output); print('Backup key created. Store it separately from backup archives.')
        else: print(json.dumps(restore(args.input, args.destination, args.key_file), indent=2))
        return
    initialize(settings)
    if settings.production and args.command in ('serve','worker','maintenance'):
        from .preflight import enforce
        enforce(settings)
    if args.command == 'preflight':
        from .preflight import check
        report=check(settings);print(json.dumps(report,indent=2))
        return 0 if report['ready'] else sys.exit(2)
    if args.command == 'maintenance':
        from .maintenance import once, run
        if args.once: print(json.dumps(once(settings),indent=2))
        else: run(settings)
        return
    if args.command in ('oidc-link','oidc-unlink'):
        from urllib.parse import urlsplit
        parsed=urlsplit(args.issuer)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or not 1<=len(args.subject)<=255:
            raise ValueError('Use the exact HTTPS issuer and a valid subject from your identity provider.')
        with transaction(settings,True) as db:
            if args.command=='oidc-link':
                user=db.execute('SELECT id FROM users WHERE email=? AND active=1',(email(args.email),)).fetchone()
                if not user: raise ValueError('Provision the account first.')
                db.execute('INSERT INTO oidc_links VALUES(?,?,?)',(args.issuer,args.subject,user['id']))
                audit(db,None,None,'auth.oidc_linked',user['id'])
            else:
                user=db.execute('SELECT user_id FROM oidc_links WHERE issuer=? AND subject=?',(args.issuer,args.subject)).fetchone()
                db.execute('DELETE FROM oidc_links WHERE issuer=? AND subject=?',(args.issuer,args.subject))
                if user:
                    db.execute('DELETE FROM sessions WHERE user_id=?',(user[0],))
                    db.execute('DELETE FROM oidc_states WHERE nonce=?',('mfa:'+user[0],))
                    audit(db,None,None,'auth.oidc_unlinked',user[0])
        print('Identity link updated.');return
    if args.command=='reset-mfa':
        if len(args.reason.strip())<10: raise ValueError('A meaningful verification/recovery reason is required.')
        with transaction(settings,True) as db:
            user=db.execute('SELECT id FROM users WHERE email=?',(email(args.email),)).fetchone()
            if not user: raise ValueError('User not found.')
            for table in ('mfa','recovery_codes','sessions','reset_tokens'):db.execute('DELETE FROM '+table+' WHERE user_id=?',(user[0],))
            db.execute('DELETE FROM oidc_states WHERE nonce=?',('mfa:'+user[0],))
            audit(db,None,None,'auth.admin_mfa_reset',user[0],{'reason':args.reason[:500]})
        print('MFA reset and sessions revoked. Independently verify identity before issuing recovery access.');return
    if args.command == 'init': print('Database initialized without default credentials.'); return
    if args.command in ('create-admin', 'create-user', 'reset-password'):
        password = getpass.getpass('Password (14 to 128 characters): ')
        if password != getpass.getpass('Confirm password: '): raise ValueError('Passwords do not match.')
        validate_password(password)
        with transaction(settings, True) as db:
            if args.command == 'reset-password':
                user = db.execute('SELECT id FROM users WHERE email=?', (email(args.email),)).fetchone()
                if not user: raise ValueError('User not found.')
                db.execute('UPDATE users SET password_hash=? WHERE id=?', (PH.hash(password), user['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
                db.execute('DELETE FROM reset_tokens WHERE user_id=?', (user['id'],))
                db.execute('DELETE FROM oidc_states WHERE nonce=?', ('mfa:'+user['id'],))
                audit(db, None, None, 'auth.admin_password_reset', user['id'])
                print('Password reset. All sessions revoked.')
            else: print('Created user: '+create_user(db, args.email, args.name, password, admin=args.command=='create-admin'))
    elif args.command == 'serve':
        import uvicorn
        from .api import create_app
        if args.host not in ('127.0.0.1', '::1', 'localhost') and not settings.secure_cookies:
            raise ValueError('Network-facing binds require an HTTPS DATAFORGE_ORIGIN and an authenticated deployment review.')
        trusted = trusted_proxies()
        # Without this, every request behind the reverse proxy shares the proxy's address and
        # per-IP sign-in throttles become one organization-wide bucket.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=bool(trusted),
                    forwarded_allow_ips=','.join(trusted) if trusted else None,
                    server_header=False, access_log=False, limit_concurrency=100)
    elif args.command == 'worker':
        from .worker import Worker
        logging.basicConfig(level=logging.INFO)
        worker = Worker(settings)
        if args.once: worker.once()
        else: worker.run()
    elif args.command == 'backup':
        from .backup import backup
        print(json.dumps(backup(settings, args.output, args.key_file), indent=2))
    elif args.command == 'verify-audit':
        with transaction(settings) as db: result = verify_audit(db)
        print(json.dumps(result, indent=2))
        if not result['valid']: sys.exit(1)
    elif args.command == 'prune':
        import time
        if args.days < 7: raise ValueError('Retention must be at least seven days.')
        with transaction(settings, args.apply) as db:
            cutoff = time.time()-args.days*86400
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('succeeded','failed','cancelled') AND finished<? AND result IS NOT NULL", (cutoff,)).fetchone()[0]
            if args.apply:
                db.execute("UPDATE jobs SET result=NULL,error=coalesce(error,'Output removed by retention policy.') WHERE status IN ('succeeded','failed','cancelled') AND finished<?", (cutoff,))
                audit(db, None, None, 'retention.applied', details={'outputs_removed': count, 'days': args.days})
            print(json.dumps({'dry_run': not args.apply, 'eligible_outputs': count, 'days': args.days}))

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('Error: '+str(exc), file=sys.stderr); sys.exit(1)
