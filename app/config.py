from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlsplit

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    origin: str = 'http://127.0.0.1:8080'
    secure_cookies: bool = False
    session_seconds: int = 8 * 3600
    idle_seconds: int = 30 * 60
    request_limit: int = 8 * 1024 * 1024
    project_limit: int = 2 * 1024 * 1024
    file_limit: int = 5 * 1024 * 1024
    workspace_limit: int = 100 * 1024 * 1024
    job_timeout: int = 30
    lease_seconds: int = 90
    max_result_rows: int = 10000
    require_mfa: bool = False
    secret_key_file: Path | None = None
    production: bool = False

    @property
    def encryption_key_path(self) -> Path:
        return self.secret_key_file or self.data_dir / 'application.key' 

    @property
    def db_path(self) -> Path:
        return self.data_dir / 'dataforge.sqlite3'

    @classmethod
    def from_env(cls):
        origin = os.getenv('DATAFORGE_ORIGIN', 'http://127.0.0.1:8080').rstrip('/')
        parts = urlsplit(origin)
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError('DATAFORGE_ORIGIN must be an absolute origin without a path or credentials.')
        secure = parts.scheme == 'https'
        if not secure and parts.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Non-loopback deployments require an HTTPS DATAFORGE_ORIGIN.')
        return cls(Path(os.getenv('DATAFORGE_DATA_DIR', './var')).resolve(), origin, secure,
                   require_mfa=os.getenv('DATAFORGE_REQUIRE_MFA', 'false').lower() == 'true',
                   secret_key_file=Path(os.environ['DATAFORGE_SECRET_KEY_FILE']).resolve() if os.getenv('DATAFORGE_SECRET_KEY_FILE') else None,
                   production=os.getenv('DATAFORGE_ENV', 'development') == 'production')


def trusted_proxies():
    """Exact reverse-proxy addresses allowed to supply X-Forwarded-For.

    Wildcards are refused: they would let any client choose its own address and
    evade the per-IP sign-in throttles.
    """
    import ipaddress
    values = [v.strip() for v in os.getenv('DATAFORGE_TRUSTED_PROXIES', '').split(',') if v.strip()]
    for value in values:
        try: ipaddress.ip_network(value, strict=False)
        except ValueError: raise ValueError('DATAFORGE_TRUSTED_PROXIES must list proxy IP addresses or networks; wildcards are not allowed.')
    return values
