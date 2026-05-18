"""Schemas Pydantic para la API."""
from typing import Literal, Optional
from pydantic import BaseModel


class JobResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    logs: list[str]
    created_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None


class UploadRequest(BaseModel):
    # "datagov" mantiene el flujo legacy (Excel + merge + lang).
    # "alation" sube CSVs en formato nativo de Alation tal cual (sin merge,
    # `lang` se ignora).
    dict_type: Literal["datagov", "alation"] = "datagov"
    lang: str = "es"


class DownloadRequest(BaseModel):
    oids: Optional[list[int]] = None


class TableEntry(BaseModel):
    oid: int
    name: Optional[str] = None


class TableAdd(BaseModel):
    oid: int


class ConfigResponse(BaseModel):
    base_url: str
    auth_mode: str
    bearer_token_set: bool
    api_token_set: bool
    path_source_alation: str
    path_source_format: str
    path_destination: str
    path_tables: str
    csv_encoding: str


class ConfigUpdate(BaseModel):
    base_url: Optional[str] = None
    bearer_token: Optional[str] = None
    api_token: Optional[str] = None
    csv_encoding: Optional[str] = None


class FileInfo(BaseModel):
    name: str
    size_kb: float
    modified: str


class TablesStatus(BaseModel):
    exists: bool
    num_tables: int = 0
    oids: list[int] = []
    file_info: Optional[FileInfo] = None


class AuthValidateResponse(BaseModel):
    download_ok: bool
    download_msg: str
    upload_ok: bool
    upload_msg: str


class TableValidateRequest(BaseModel):
    oids: list[int]


class TableValidateResult(BaseModel):
    oid: int
    exists: bool
    name: Optional[str] = None
    schema_name: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None


class TableBulkSave(BaseModel):
    entries: list[TableEntry]


# ── Navegador de Alation (Database → Schema → Table) ─────────────────────────

class AlationDataSource(BaseModel):
    id: int
    title: Optional[str] = None
    dbtype: Optional[str] = None
    dbname: Optional[str] = None


class AlationSchemaInfo(BaseModel):
    id: int
    name: Optional[str] = None
    title: Optional[str] = None


class AlationTableInfo(BaseModel):
    id: int
    name: Optional[str] = None
    title: Optional[str] = None
    schema_name: Optional[str] = None
