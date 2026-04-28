"""Descarga diccionarios de datos desde Alation y los guarda como CSV."""
import logging
import re
from typing import Callable

import pandas as pd

from core.settings import settings
from core.alation_client import get_all, validate_auth

logger = logging.getLogger(__name__)

_CUSTOM_FIELD_MAP: dict[str, int] = {
    "admite nulos": 5,       "allows nulls": 5,
    "campo clave": 6,        "unique key": 6,
    # "confidentiality level" se omite aquí porque también aparece en el nombre
    # de "nivel confidencialidad | confidentiality level" causando falso match.
    "clasificación datos sensibles": 7,
    "contiene información sensible": 8, "contains sensible information": 8,
    "data custodian": 9,
    "data steward": 10,
    "fórmula": 12,           "formula": 12,
    "longitud regulada": 13, "regulated length": 13,
    "nivel confidencialidad": 14, "privacy level": 14,
    "origen del dato": 15,   "data origin": 15, "source": 15,
    "reglas de calidad": 16, "data quality rules": 16,
    "reutilizable": 17,      "reusable": 17,
}


def _strip_html(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def _get_custom_field_value(custom_fields: list, col_idx: int) -> str:
    if not custom_fields:
        return "" if col_idx == 8 else "_"
    for cf in custom_fields:
        field_name = (cf.get("field_name") or "").lower()
        for key, idx in _CUSTOM_FIELD_MAP.items():
            if key in field_name and idx == col_idx:
                val = cf.get("value")
                if val is None:
                    return ""
                out = str(val.get("title", "")) if isinstance(val, dict) and "title" in val else str(val)
                return _strip_html(out) if out else out
    return "" if col_idx == 8 else "_"


def download_all(
    bearer_token: str = "",
    api_token: str = "",
    log_callback: Callable[[str], None] | None = None,
    oids: list[int] | None = None,
) -> None:
    """
    Descarga los diccionarios de las tablas indicadas.

    Args:
        bearer_token: Token Bearer del usuario (sesión). Si vacío, usa .env.
        api_token:    API Token del usuario (sesión). Si vacío, usa .env.
        log_callback: Función para emitir progreso.
        oids:         Lista de OIDs a descargar. Si None, lee Tables.xlsx.
    """
    _log = log_callback or logger.info

    ok, msg = validate_auth("download", bearer_token=bearer_token, api_token=api_token)
    if not ok:
        _log(f"❌ Auth fallida: {msg}")
        raise RuntimeError(msg)
    _log(f"✓ {msg}")

    settings.source_alation.mkdir(parents=True, exist_ok=True)

    if oids is not None:
        table_ids = oids
    else:
        if not settings.tables_file.exists():
            raise FileNotFoundError(f"No se encontró Tables.xlsx en {settings.tables_file}")
        lista_tables = pd.read_excel(settings.tables_file, sheet_name="Tables")
        table_ids = [int(row["Oid"]) for _, row in lista_tables.iterrows()]

    total = len(table_ids)
    _log(f"Tablas a descargar: {total}")

    for i, table_id in enumerate(table_ids, start=1):
        _log(f"[{i}/{total}] Procesando tabla OID={table_id}...")
        try:
            _download_table(table_id, bearer_token, api_token, _log)
        except Exception as exc:
            _log(f"  ⚠ Error en OID={table_id}: {exc}")
            logger.exception("Error descargando tabla %s", table_id)


def _download_table(
    table_id: int,
    bearer_token: str,
    api_token: str,
    _log: Callable[[str], None],
) -> None:
    base = settings.alation_base_url
    _kw = {"bearer_token": bearer_token, "api_token": api_token}

    # 1. Metadata de la tabla
    tabla_data = get_all(f"{base}/catalog/table/", {"id": table_id, "limit": 2}, **_kw)
    if not tabla_data:
        _log(f"  ⚠ OID={table_id}: no encontrada en Alation.")
        return

    df_tabla = pd.DataFrame(tabla_data)
    df_tabla["0"] = "oid=" + df_tabla["id"].astype(str) + ";otype=table"
    df_tabla["1"] = ""
    df_tabla["2"] = df_tabla["schema_name"].astype(str) + "." + df_tabla["name"].astype(str)
    df_tabla["3"] = df_tabla["title"]
    df_tabla["4"] = df_tabla["description"].fillna("").astype(str).apply(_strip_html)
    for col, default in {
        "5": "_", "6": "_", "7": "_", "8": "_",
        "9": "", "10": "", "11": "",
        "12": "_", "13": "_", "14": "_", "15": "_", "16": "_", "17": "Si",
    }.items():
        df_tabla[col] = default

    df_tabla = df_tabla.drop(columns=[c for c in [
        "custom_fields", "description", "ds_id", "db_table_type", "id",
        "is_view", "is_synonym", "name", "schema_id", "schema_name",
        "title", "url", "synonyms", "base_table", "db_comment",
    ] if c in df_tabla.columns])

    # 2. Columnas (Integration v2)
    columnas = get_all(
        f"{base}/integration/v2/column/",
        {"table_id": table_id, "fields": "id,name,title,key,column_type", "limit": 500},
        **_kw,
    )
    df_columnas = pd.DataFrame(columnas)

    # 3. Descripciones y custom fields (Catalog API — intenta ambos tokens)
    catalog_by_id: dict[int, dict] = {}
    for auth in ("download", "upload"):
        try:
            catalog_cols = get_all(
                f"{base}/catalog/column/",
                {"table_id": table_id, "limit": 500},
                auth_type=auth,
                bearer_token=bearer_token,
                api_token=api_token,
            )
            catalog_by_id = {c["id"]: c for c in catalog_cols}
            break
        except Exception:
            continue

    def _desc(col_id: int) -> str:
        raw = catalog_by_id.get(col_id, {}).get("description") or ""
        return _strip_html(str(raw)) if raw else ""

    def _cf(col_id: int, idx: int) -> str:
        return _get_custom_field_value(
            catalog_by_id.get(col_id, {}).get("custom_fields") or [], idx
        )

    def _nullable(col_id: int) -> str:
        n = catalog_by_id.get(col_id, {}).get("nullable")
        if n is True: return "Si"
        if n is False: return "No"
        return _cf(col_id, 5) or ""

    def _pk(col_id: int) -> str:
        pk = catalog_by_id.get(col_id, {}).get("is_primary_key")
        if pk is True: return "Si"
        if pk is False: return "No"
        return _cf(col_id, 6) or ""

    df_columnas["0"] = "oid=" + df_columnas["id"].astype(str) + ";otype=attribute"
    df_columnas["1"] = df_columnas["column_type"]
    df_columnas["2"] = df_columnas["key"].astype(str).replace("34.", "")
    df_columnas["3"] = df_columnas["title"]
    df_columnas["4"] = df_columnas["id"].apply(_desc)
    df_columnas["5"] = df_columnas["id"].apply(_nullable)
    df_columnas["6"] = df_columnas["id"].apply(_pk)
    for slot in range(7, 18):
        df_columnas[str(slot)] = df_columnas["id"].apply(lambda i, s=slot: _cf(i, s))
    df_columnas["17"] = df_columnas["id"].apply(lambda i: _cf(i, 17) or "_")

    df_columnas = df_columnas.drop(columns=[c for c in [
        "id", "column_type", "key", "title", "name"
    ] if c in df_columnas.columns])

    # 4. Combinar y renombrar
    df_final = pd.concat([df_tabla, df_columnas], ignore_index=True)
    df_final = df_final.rename(columns={
        "0": "al_datadict_item_properties",
        "1": "al_datadict_item_column_data_type",
        "2": "key", "3": "title", "4": "description",
        "5": '"admite nulos | allows nulls"',
        "6": '"campo clave (llave) | unique key"',
        "7": '"clasificación datos sensibles | confidentiality level"',
        "8": '"contiene información sensible | contains sensible information"',
        "9": "data custodian:user",
        "10": "data steward:groupprofile",
        "11": "data steward:user",
        "12": '"fórmula | formula"',
        "13": '"longitud regulada | regulated length"',
        "14": '"nivel confidencialidad | confidentiality level"',
        "15": '"origen del dato | data origin"',
        "16": '"reglas de calidad de datos | data quality rules"',
        "17": '"reutilizable | reusable ♻️"',
    })

    # 5. Guardar CSV
    table_path = str(df_tabla["2"].iloc[0]).replace("dbzprd_usa_oxxo.", "")
    filename = f"{table_id}_{table_path}.csv"
    df_final.to_csv(settings.source_alation / filename, index=False, encoding=settings.csv_encoding)
    _log(f"  ✓ Guardado: {filename}")
