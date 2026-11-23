from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Login(Model):
    otp: str = Field(default='', max_length=128)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

class PasswordChange(Model):
    otp: str = Field(default='', max_length=128)
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=14, max_length=128)

class UserCreate(Model):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=14, max_length=128)

class UserState(Model):
    active: bool

class Named(Model):
    name: str = Field(min_length=1, max_length=120)

class Member(Model):
    email: str = Field(max_length=254)
    role: Literal['owner', 'editor', 'viewer']

class ProjectCreate(Named):
    document: dict[str, Any] = Field(default_factory=dict)

class ProjectSave(ProjectCreate):
    revision: int = Field(ge=1)

class RestoreRevision(Model):
    revision: int = Field(ge=1)
    restore_revision: int = Field(ge=1)

class FileCreate(Model):
    name: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(max_length=7 * 1024 * 1024)

class Source(Model):
    file_id: str = Field(min_length=1, max_length=80)
    table: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,47}$')

class Job(Model):
    name: str = Field(default='SQL job', min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=65536)
    sources: list[Source] = Field(default_factory=list, max_length=8)

class Schedule(Model):
    name: str = Field(min_length=1, max_length=120)
    job: Job
    interval_seconds: int = Field(ge=60, le=2592000)
    enabled: bool = False

class ScheduleState(Model):
    enabled: bool


class VerifyFactor(Model):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(default='', max_length=128)

class FactorConfirm(Model):
    code: str = Field(min_length=6, max_length=128)

class RecoveryRequest(Model):
    email: str = Field(max_length=254)

class RecoveryComplete(Model):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=14, max_length=128)

class PipelineRun(Model):
    revision: int = Field(ge=1)
    mapping_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    bindings: dict[str, str] = Field(default_factory=dict, max_length=8)
    interval_seconds: int | None = Field(default=None, ge=60, le=2592000)

class ApprovalPolicy(Model):
    approval_required: bool
