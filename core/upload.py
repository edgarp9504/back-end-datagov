"""Sube diccionarios enriquecidos a Alation fusionando CSV con Excel."""
import logging
import re
import unicodedata
from pathlib import Path
from typing import Callable

import chardet
import pandas as pd
import requests

from core.settings import settings
from core.alation_client import validate_auth

logger = logging.getLogger(__name__)

CONFIG_ES = {
    "key_col": "Nombre del Campo",
    "mapping": {
        "description": "Descripción",
        '"admite nulos | allows nulls"': "Admite Nulos",
        '"longitud regulada | regulated length"': "Longitud Regulada",
        '"fórmula | formula"': "Fórmula",
        '"origen del dato | data origin"': "Origen del Dato",
        '"campo clave (llave) | unique key"': "Clave única de Registro",
        '"nivel confidencialidad | confidentiality level"': "Nivel Confidencialidad",
        '"clasificación datos sensibles | confidentiality level"': "Clasificación de Datos Sensibles",
    },
    "drop_cols": [
        "data custodian:groupprofile", '"política | policy":business_policy',
        "💼 proyecto:glossary_term", "KEY_ID", "sistema fuente",
        '"tipo de entidad | entity type"', "Unnamed: 0",
        "Origen del Dato", "Fórmula", "Nombre del Campo", "Descripción",
        "Longitud Regulada", "Clave única de Registro", "Tipo de Dato",
        "Admite Nulos", "Nivel Confidencialidad", "Clasificación de Datos Sensibles",
    ],
}

CONFIG_EN = {
    "key_col": "Column Name",
    "mapping": {
        "description": "Description",
        '"admite nulos | allows nulls"': "Allows Nulls",
        '"longitud regulada | regulated length"': "Regulated Length",
        '"fórmula | formula"': "Formula",
        '"origen del dato | data origin"': "Source",
        '"campo clave (llave) | unique key"': "Unique Key",
        '"nivel confidencialidad | confidentiality level"': "Privacy Level",
        '"clasificación datos sensibles | confidentiality level"': "Confidentiality Level",
    },
    "drop_cols": [
        "data custodian:groupprofile", "policy:business_policy",
        "💼project:glossary_term", "KEY_ID",
        "Unnamed: 0", "Source", "Formula", "Column Name",
        "Description", "Regulated Length", "Allows Nulls",
        "Data Type", "Unique Key", "Privacy Level",
        "Confidentiality Level", "Unnamed: 11",
    ],
}


def _detect_encoding(path: str) -> str:
    with open(path, "rb") as f:
        return chardet.detect(f.read()).get("encoding") or "utf-8"


def _upload_to_alation(url: str, csv_path: str, encoding: str, api_token: str) -> int:
    token = api_token or settings.alation_api_token
    headers = {"accept": "application/json", "TOKEN": token}
    with open(csv_path, "r", encoding=encoding) as f:
        r = requests.put(
            url,
            data={"overwrite_values": "true"},
            headers=headers,
            files={"file": (csv_path, f, "text/csv")},
        )
    return r.status_code


def _build_csv_index(csv_dir: Path) -> dict[str, Path]:
    """Indexa los CSV por OID. Formato de nombre: {oid}_{ruta_tabla}.csv"""
    index: dict[str, Path] = {}
    for csv_path in csv_dir.glob("*.csv"):
        oid = csv_path.name.split("_")[0]
        if oid.isdigit():
            index[oid] = csv_path
    return index


def _normalize_text(value: str) -> str:
    """Normaliza texto para comparaciones tolerantes (acentos/símbolos)."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _infer_oid_from_filename(path_xls: Path, csv_index: dict[str, Path]) -> str | None:
    """
    Intenta inferir el OID cuando el Excel no trae hojas numéricas.

    Estrategia:
      1) Buscar IDs numéricos dentro del nombre de archivo y validar que exista CSV.
      2) Buscar un match por nombre de tabla (ej. ...DM_188_METRICAS_GENERALES...).
    """
    stem = path_xls.stem

    # 1) Si el nombre trae un número que coincide con un CSV, úsalo.
    for oid in re.findall(r"(?<!\d)(\d{3,})(?!\d)", stem):
        if oid in csv_index:
            return oid

    # 2) Match por nombre de tabla embebido en el filename.
    match = re.search(r"dm[\s_-]*\d+[\s_-]*(.+)$", stem, flags=re.IGNORECASE)
    lookup_key = _normalize_text(match.group(1) if match else stem)
    if not lookup_key:
        return None

    candidates: list[str] = []
    for oid, csv_path in csv_index.items():
        csv_stem = csv_path.stem.split("_", 1)[-1]  # quita prefijo "{oid}_"
        if lookup_key in _normalize_text(csv_stem):
            candidates.append(oid)

    return candidates[0] if len(candidates) == 1 else None


def _resolve_table_sources(
    path_xls: Path,
    all_sheets: list[str],
    csv_index: dict[str, Path],
    _log: Callable[[str], None],
) -> list[tuple[str, str]]:
    """
    Devuelve pares (oid, sheet_name) a procesar.

    - Caso normal: hojas con nombre numérico => ese nombre es el OID.
    - Fallback: plantilla "Mapeo de Campos" y OID inferido por filename.
    """
    numeric_sheets = [s for s in all_sheets if str(s).strip().isdigit()]
    if numeric_sheets:
        return [(str(s).strip(), str(s)) for s in numeric_sheets]

    normalized_to_original = { _normalize_text(s): s for s in all_sheets }
    template_sheet = (
        normalized_to_original.get("mapeodecampos")
        or normalized_to_original.get("mapeocampos")
        or normalized_to_original.get("diccionariodedatos")
        or normalized_to_original.get("diccionario")
    )

    if not template_sheet:
        return []

    inferred_oid = _infer_oid_from_filename(path_xls, csv_index)
    if not inferred_oid:
        _log(
            "  ⚠ Se detectó hoja de plantilla, pero no se pudo inferir el OID "
            "desde el nombre del archivo."
        )
        return []

    _log(
        f"  [info] Se detecto plantilla '{template_sheet}'. "
        f"OID inferido desde filename: {inferred_oid}."
    )
    return [(inferred_oid, template_sheet)]


def upload_all(
    api_token: str = "",
    lang: str = "es",
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """
    Fusiona los Excel de formato con los CSV de Alation y sube el resultado.

    El Excel debe tener una hoja por tabla, con el nombre = OID numérico de la tabla.
    Hojas con nombre no numérico (Guía, LISTAS, etc.) se ignoran automáticamente.

    Args:
        api_token:    API Token del usuario (sesión). Si vacío, usa .env.
        lang:         "es" o "en".
        log_callback: Función para emitir progreso.
    """
    _log = log_callback or logger.info

    ok, msg = validate_auth("upload", api_token=api_token)
    if not ok:
        _log(f"❌ Auth fallida: {msg}")
        raise RuntimeError(msg)
    _log(f"✓ {msg}")

    settings.destination.mkdir(parents=True, exist_ok=True)

    config = CONFIG_ES if lang == "es" else CONFIG_EN
    files_xls = sorted(settings.source_format.glob("*.xlsx"))

    if not files_xls:
        _log("⚠ No hay Excel en data/format/.")
        raise FileNotFoundError("No hay Excel en data/format/")

    # Índice de CSVs disponibles: OID → Path
    csv_index = _build_csv_index(settings.source_alation)
    if not csv_index:
        _log("⚠ No hay CSV en data/alation/. Ejecuta primero la descarga.")
        raise FileNotFoundError("No hay CSV en data/alation/")

    total_uploaded = 0
    total_warned = 0

    for path_xls in files_xls:
        _log(f"📄 Procesando Excel: {path_xls.name}")

        try:
            xl = pd.ExcelFile(path_xls, engine="openpyxl")
            all_sheets = xl.sheet_names
        except Exception as exc:
            _log(f"  ❌ No se pudo leer {path_xls.name}: {exc}")
            continue

        table_sources = _resolve_table_sources(path_xls, all_sheets, csv_index, _log)
        used_sheets = {sheet_name for _, sheet_name in table_sources}
        ignored_sheets = [s for s in all_sheets if s not in used_sheets]

        if ignored_sheets:
            _log(f"  ↷ Hojas ignoradas (no son IDs): {', '.join(str(s) for s in ignored_sheets)}")

        if not table_sources:
            _log(f"  ⚠ No se encontraron hojas con ID numérico en {path_xls.name}.")
            continue

        ids_detectados = [oid for oid, _ in table_sources]
        _log(f"  Trabajando con {len(ids_detectados)} ID(s): {', '.join(ids_detectados)}")

        # Detectar hojas sin CSV
        ids_con_csv = [(oid, sheet_name) for oid, sheet_name in table_sources if oid in csv_index]
        ids_sin_csv = [(oid, sheet_name) for oid, sheet_name in table_sources if oid not in csv_index]

        if ids_sin_csv:
            faltantes = ", ".join(f"{oid} (hoja: {sheet_name})" for oid, sheet_name in ids_sin_csv)
            _log(
                f"  ⚠ Estás trabajando con {len(table_sources)} ID(s). "
                f"Falta CSV descargado para: {faltantes}. "
                f"Esas hojas serán omitidas."
            )
            total_warned += len(ids_sin_csv)

        if not ids_con_csv:
            _log("  ⚠ Ninguna hoja tiene CSV disponible. Omitiendo este Excel.")
            continue

        for oid, sheet_name in ids_con_csv:
            path_csv = csv_index[oid]

            try:
                encoding = _detect_encoding(str(path_csv))
                df_csv = pd.read_csv(path_csv, encoding=encoding)

                df_atributos = df_csv[
                    df_csv["al_datadict_item_properties"].str.contains(
                        "attribute", case=False, na=False
                    )
                ]
                df_csv["KEY_ID"] = df_atributos["key"].apply(
                    lambda x: str(x).split(".")[-1]
                ).str.upper()
                primera_fila = df_csv.iloc[0].copy()

                # Leer la hoja del Excel (fila 1 = título decorativo, fila 2 = encabezados)
                df_data = pd.read_excel(
                    path_xls, sheet_name=sheet_name, skiprows=1, engine="openpyxl"
                )
                df_data["KEY_ID"] = df_data[config["key_col"]].astype(str).str.upper()

                df_union = pd.merge(df_csv, df_data, on="KEY_ID", how="left")
                for col_dest, col_src in config["mapping"].items():
                    if col_src in df_union.columns:
                        df_union[col_dest] = df_union[col_src]

                cols_csv = df_union.columns[: len(df_csv.columns)].tolist()
                df_union[cols_csv] = df_union[cols_csv].astype(object)
                df_union.iloc[0, : len(df_csv.columns)] = primera_fila.values

                drop_cols = [c for c in config["drop_cols"] if c in df_union.columns]
                df_union = df_union.drop(columns=drop_cols)

                path_dest = settings.destination / f"{oid}.csv"
                df_union.to_csv(path_dest, index=False, encoding=encoding)

                url = (
                    f"{settings.alation_base_url}"
                    f"/integration/v1/data_dictionary/table/{oid}/upload/"
                )
                status = _upload_to_alation(url, str(path_dest), encoding, api_token)
                _log(f"    ✓ OID={oid} ({path_csv.stem}) · HTTP {status}")
                total_uploaded += 1

            except Exception as exc:
                _log(f"    ❌ Error procesando OID={oid}: {exc}")
                logger.exception("Error procesando OID=%s", oid)

    omitidas = f", {total_warned} omitida(s) por falta de CSV." if total_warned else "."
    _log(f"\n✅ Finalizado: {total_uploaded} tabla(s) subidas exitosamente{omitidas}")
