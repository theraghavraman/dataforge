"""Actual local TLS sockets; temporary CA/certificates, no public DNS or email provider."""
import contextlib
import datetime as dt
import ipaddress
from pathlib import Path
import secrets
import socket
import socketserver
import ssl
import threading
import time
from email.parser import BytesParser
from email import policy
import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from app.api import create_app
from app.config import Settings
from app.db import initialize,transaction
from app.security import create_user
from app.outbox import email_send


def certificate(directory):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DataForge local TLS QA')]);now=dt.datetime.now(dt.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-dt.timedelta(minutes=1)).not_valid_after(now+dt.timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
    certpath=directory/'qa-cert.pem';keypath=directory/'qa-key.pem'
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));keypath.chmod(0o600)
    return certpath,keypath


@pytest.fixture(scope='module')
def tls_api(tmp_path_factory):
    root=tmp_path_factory.mktemp('https');cert,key=certificate(root)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    settings=Settings(root/'data',origin=f'https://127.0.0.1:{port}',secure_cookies=True);initialize(settings);password=secrets.token_urlsafe(24)
    with transaction(settings,True) as db:create_user(db,'tls@example.test','TLS QA',password,True)
    server=uvicorn.Server(uvicorn.Config(create_app(settings),host='127.0.0.1',port=port,ssl_certfile=str(cert),ssl_keyfile=str(key),log_level='critical',access_log=False,server_header=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.03)
    assert server.started
    try:yield settings,password,cert
    finally:server.should_exit=True;thread.join(timeout=10);sock.close();assert not thread.is_alive()


def test_https_requires_trusted_certificate(tls_api):
    settings,password,cert=tls_api
    with pytest.raises(httpx.ConnectError):httpx.get(settings.origin+'/health/live',trust_env=False,timeout=3)
    with httpx.Client(verify=ssl.create_default_context(cafile=cert),trust_env=False) as client:
        assert client.get(settings.origin+'/health/live').status_code==200


def test_https_session_cookie_csrf_and_logout(tls_api):
    settings,password,cert=tls_api
    with httpx.Client(base_url=settings.origin,verify=ssl.create_default_context(cafile=cert),trust_env=False) as client:
        signed=client.post('/api/auth/login',json={'email':'tls@example.test','password':password})
        assert signed.status_code==200
        cookie=signed.headers['set-cookie'].lower()
        assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
        assert client.get('/api/auth/me').status_code==200
        assert client.post('/api/workspaces',json={'name':'Missing CSRF'}).status_code==403
        client.headers['X-CSRF-Token']=signed.json()['csrf']
        assert client.post('/api/workspaces',json={'name':'TLS workspace'},headers={'Origin':settings.origin}).status_code==201
        assert client.post('/api/workspaces',json={'name':'Cross origin'},headers={'Origin':'https://untrusted.example'}).status_code==403
        assert client.get('/').headers['strict-transport-security'].startswith('max-age=')
        assert client.post('/api/auth/logout').status_code==200
        assert client.get('/api/auth/me').status_code==401


class MailHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 localhost QA SMTP\r\n');payload=[]
        while True:
            line=self.rfile.readline(65536)
            if not line:return
            verb=line.split(b' ',1)[0].strip().upper()
            if verb in (b'EHLO',b'HELO'):self.wfile.write(b'250-localhost\r\n250 SIZE 1000000\r\n')
            elif verb in (b'MAIL',b'RCPT',b'RSET'):self.wfile.write(b'250 OK\r\n')
            elif verb==b'DATA':
                self.wfile.write(b'354 End with dot\r\n')
                while True:
                    body=self.rfile.readline(65536)
                    if body==b'.\r\n':break
                    if not body:return
                    payload.append(body[1:] if body.startswith(b'..') else body)
                self.server.messages.append(b''.join(payload));self.wfile.write(b'250 Accepted\r\n')
            elif verb==b'QUIT':self.wfile.write(b'221 Bye\r\n');return
            else:self.wfile.write(b'502 Unsupported\r\n')


class TLSSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def get_request(self):
        sock,addr=super().get_request()
        try:return self.context.wrap_socket(sock,server_side=True),addr
        except BaseException:sock.close();raise


@pytest.fixture
def tls_mail(tmp_path,monkeypatch):
    cert,key=certificate(tmp_path);server=TLSSMTP(('127.0.0.1',0),MailHandler);server.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.context.load_cert_chain(cert,key);server.messages=[]
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    for name,value in {'DATAFORGE_SMTP_HOST':'127.0.0.1','DATAFORGE_SMTP_PORT':str(server.server_address[1]),'DATAFORGE_SMTP_MODE':'tls','DATAFORGE_SMTP_FROM':'noreply@example.test','DATAFORGE_SMTP_CA_FILE':str(cert)}.items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('DATAFORGE_SMTP_USERNAME',raising=False)
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_real_smtp_tls_delivers_to_local_receiver(tls_mail):
    email_send({'to':'recipient@example.test','subject':'QA recovery notification','body':'Synthetic one-time link, not an active credential.'},'qa-id-123')
    assert len(tls_mail.messages)==1
    msg=BytesParser(policy=policy.default).parsebytes(tls_mail.messages[0])
    assert msg['To']=='recipient@example.test' and msg['Message-ID']=='<qa-id-123@dataforge.local>'
    assert 'Synthetic one-time link' in msg.get_content()


def test_smtp_certificate_verification_is_not_bypassed(tls_mail,monkeypatch):
    monkeypatch.delenv('DATAFORGE_SMTP_CA_FILE')
    with pytest.raises(ssl.SSLCertVerificationError):email_send({'to':'recipient@example.test','subject':'TLS rejection','body':'Must not deliver.'},'qa-untrusted')
    assert not tls_mail.messages
