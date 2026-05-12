"""Endpoints proxy al catálogo de Alation: navegación Database → Schema → Table."""
from fastapi import APIRouter, Depends, HTTPException

from core.alation_client import get_all
from core.settings import settings
from dependencies import get_tokens
from models.schemas import (
    AlationDataSource,
    AlationSchemaInfo,
    AlationTableInfo,
)

router = APIRouter()


def _safe_get(record: dict, *keys: str, default=None):
    for k in keys:
        if k in record and record[k] is not None:
            return record[k]
    return default


# ── Datasources ──────────────────────────────────────────────────────────────

@router.get("/datasources", response_model=list[AlationDataSource])
async def list_datasources(tokens: dict = Depends(get_tokens)):
    """Lista las bases de datos (datasources) registradas en Alation."""
    if not tokens["bearer_token"]:
        raise HTTPException(status_code=401, detail="Bearer token requerido.")

    try:
        records = get_all(
            f"{settings.alation_base_url}/integration/v2/datasource/",
            {"limit": 500},
            auth_type="download",
            bearer_token=tokens["bearer_token"],
            api_token=tokens["api_token"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error consultando Alation: {exc}",
        )

    # Prefijos permitidos (case-insensitive). Si la lista está vacía,
    # se aceptan todos los datasources.
    allowed_prefixes = tuple(
        p.strip().lower()
        for p in settings.alation_datasource_title_prefixes.split(",")
        if p.strip()
    )

    out: list[AlationDataSource] = []
    for r in records:
        # Alation marca como ocultos algunos datasources internos; los omitimos.
        if r.get("is_hidden") is True:
            continue
        if r.get("deleted") is True:
            continue

        title = (_safe_get(r, "title") or "").strip()
        if allowed_prefixes and not title.lower().startswith(allowed_prefixes):
            continue

        out.append(
            AlationDataSource(
                id=int(r["id"]),
                title=_safe_get(r, "title"),
                dbtype=_safe_get(r, "dbtype"),
                dbname=_safe_get(r, "dbname", "db_username"),
            )
        )
    out.sort(key=lambda d: ((d.title or d.dbname or "").lower(), d.id))
    return out


# ── Schemas de un datasource ─────────────────────────────────────────────────

@router.get(
    "/datasources/{ds_id}/schemas",
    response_model=list[AlationSchemaInfo],
)
async def list_schemas(ds_id: int, tokens: dict = Depends(get_tokens)):
    """Lista los schemas de la base de datos indicada."""
    if not tokens["bearer_token"]:
        raise HTTPException(status_code=401, detail="Bearer token requerido.")

    try:
        records = get_all(
            f"{settings.alation_base_url}/integration/v2/schema/",
            {"ds_id": ds_id, "limit": 500},
            auth_type="download",
            bearer_token=tokens["bearer_token"],
            api_token=tokens["api_token"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error consultando Alation: {exc}",
        )

    out = [
        AlationSchemaInfo(
            id=int(r["id"]),
            name=_safe_get(r, "name"),
            title=_safe_get(r, "title"),
        )
        for r in records
    ]
    out.sort(key=lambda s: ((s.name or s.title or "").lower(), s.id))
    return out


# ── Tablas de un schema ──────────────────────────────────────────────────────

@router.get(
    "/schemas/{schema_id}/tables",
    response_model=list[AlationTableInfo],
)
async def list_tables_in_schema(
    schema_id: int,
    tokens: dict = Depends(get_tokens),
):
    """Lista las tablas del schema indicado."""
    if not tokens["bearer_token"]:
        raise HTTPException(status_code=401, detail="Bearer token requerido.")

    try:
        records = get_all(
            f"{settings.alation_base_url}/integration/v2/table/",
            {"schema_id": schema_id, "limit": 500},
            auth_type="download",
            bearer_token=tokens["bearer_token"],
            api_token=tokens["api_token"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error consultando Alation: {exc}",
        )

    out = [
        AlationTableInfo(
            id=int(r["id"]),
            name=_safe_get(r, "name"),
            title=_safe_get(r, "title"),
            schema_name=_safe_get(r, "schema_name"),
        )
        for r in records
    ]
    out.sort(key=lambda t: ((t.name or t.title or "").lower(), t.id))
    return out
