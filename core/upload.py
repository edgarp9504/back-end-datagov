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
from core.download import _download_table

logger = logging.getLogger(__name__)

_ALLOWED_VALUES_ES = {
    "Admite Nulos": {"Si", "No"},
    "Clave única de Registro": {"Si", "No"},
    "Nivel Confidencialidad": {"Pública", "Uso interno", "Restringida", "Confidencial"},
    "Clasificación de Datos Sensibles": {"Nivel Estandar", "Nivel Sensible", "Nivel Especial"},
}

_ALLOWED_VALUES_EN = {
    "Allows Nulls": {"Si", "No"},
    "Unique Key": {"Si", "No"},
    "Privacy Level": {"Pública", "Uso interno", "Restringida", "Confidencial"},
    "Confidentiality Level": {"Nivel Estandar", "Nivel Sensible", "Nivel Especial"},
}

CONFIG_ES = {
    "key_col": "Nombre del Campo",
    "required_cols": [
        "Nombre del Campo",
        "Descripción",
        "Admite Nulos",
        "Longitud Regulada",
        "Fórmula",
        "Origen del Dato",
        "Clave única de Registro",
        "Nivel Confidencialidad",
        "Clasificación de Datos Sensibles",
    ],
    "allowed_values": _ALLOWED_VALUES_ES,
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
    "required_cols": [
        "Column Name",
        "Description",
        "Allows Nulls",
        "Regulated Length",
        "Formula",
        "Source",
        "Unique Key",
        "Privacy Level",
        "Confidentiality Level",
    ],
    "allowed_values": _ALLOWED_VALUES_EN,
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
    """
    Sube el CSV a Alation. Lanza requests.HTTPError si el servidor devuelve un
    status no-2xx, incluyendo el cuerpo de la respuesta en el mensaje de error.
    """
    token = api_token or settings.alation_api_token
    headers = {"accept": "application/json", "TOKEN": token}
    with open(csv_path, "r", encoding=encoding) as f:
        r = requests.put(
            url,
            data={"overwrite_values": "true"},
            headers=headers,
            files={"file": (csv_path, f, "text/csv")},
        )

    if not r.ok:
        detail = r.text[:300].strip() if r.text else "(sin detalle)"
        raise requests.HTTPError(
            f"HTTP {r.status_code} al subir a Alation: {detail}",
            response=r,
        )

    return r.status_code


def _validate_headers(
    df_data: pd.DataFrame,
    config: dict,
    _log: Callable[[str], None],
    sheet_name: str,
) -> bool:
    """
    Valida que la hoja tenga los encabezados del formato DataGov.

    - Columna clave faltante → retorna False (no se puede procesar).
    - Otras columnas faltantes → avisa pero continúa (retorna True).
    - Columnas extra → notifica (se subirán igual).
    """
    key_col = config["key_col"]
    required = config["required_cols"]
    actual = set(df_data.columns.tolist())

    # Columna clave es obligatoria: sin ella no se puede hacer el merge
    if key_col not in actual:
        _log(
            f"  ❌ Hoja '{sheet_name}': falta la columna clave '{key_col}'. "
            "No se puede procesar esta hoja."
        )
        return False

    # Columnas requeridas faltantes (no críticas, solo aviso)
    missing = [col for col in required if col not in actual and col != key_col]
    if missing:
        _log(
            f"  ⚠ Hoja '{sheet_name}': columnas faltantes del formato DataGov "
            f"({len(missing)}): {', '.join(missing)}"
        )

    # Columnas extra (se subirán igual, solo notificar)
    known = set(required)
    extra = [
        col for col in actual
        if col not in known and not str(col).startswith("Unnamed")
    ]
    if extra:
        _log(
            f"  ℹ Hoja '{sheet_name}': columnas adicionales detectadas "
            f"(se subirán igual): {', '.join(extra)}"
        )

    return True


def _validate_rows(
    df_data: pd.DataFrame,
    config: dict,
    _log: Callable[[str], None],
    sheet_name: str,
) -> int:
    """
    Valida los registros de la hoja e informa errores por fila.

    Checks:
    - Campo clave vacío (error crítico por fila).
    - Descripción vacía (advertencia por campo).

    Retorna el número total de registros con algún problema.
    """
    key_col = config["key_col"]
    desc_col = config["mapping"].get("description")
    total_errors = 0

    def _is_blank(series: pd.Series) -> pd.Series:
        return series.isna() | (series.astype(str).str.strip().isin(["", "nan", "NaN", "None"]))

    # Filas con campo clave vacío
    blank_key = _is_blank(df_data[key_col])
    if blank_key.any():
        row_nums = (blank_key[blank_key].index + 2).tolist()  # +2: fila 1=título, 1-indexed
        _log(
            f"  ❌ Hoja '{sheet_name}': {blank_key.sum()} fila(s) con "
            f"'{key_col}' vacío — filas del Excel: {row_nums}"
        )
        total_errors += int(blank_key.sum())

    # Campos con descripción vacía (solo en filas que sí tienen clave)
    if desc_col and desc_col in df_data.columns:
        valid_key = ~blank_key
        blank_desc = _is_blank(df_data[desc_col]) & valid_key
        if blank_desc.any():
            field_names = df_data.loc[blank_desc, key_col].astype(str).tolist()
            sample = field_names[:5]
            remainder = len(field_names) - len(sample)
            display = ", ".join(sample) + (f" (+{remainder} más)" if remainder else "")
            _log(
                f"  ⚠ Hoja '{sheet_name}': {len(field_names)} campo(s) sin descripción: "
                f"{display}"
            )
            total_errors += len(field_names)

    if total_errors == 0:
        _log(f"  ✓ Hoja '{sheet_name}': todos los registros validados correctamente.")

    return total_errors


def _normalize_for_domain_match(value: object) -> str:
    """Normaliza para comparar dominios: quita emojis/símbolos, colapsa espacios, minúsculas.

    Conserva letras acentuadas (\\w en Python 3 es unicode-aware) para que
    'Pública' siga validando. Strippea iconos tipo '🟡' que la plantilla usa
    como marcador visual de nivel de confidencialidad.
    """
    cleaned = re.sub(r"[^\w\s]", " ", str(value), flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _validate_column_values(
    df_data: pd.DataFrame,
    config: dict,
    _log: Callable[[str], None],
    sheet_name: str,
) -> int:
    """
    Valida que columnas con dominio fijo contengan solo valores permitidos.
    Retorna el número de celdas con valor inválido.
    """
    allowed_map = config.get("allowed_values", {})
    key_col = config["key_col"]
    total_errors = 0

    for col, valid_set in allowed_map.items():
        if col not in df_data.columns:
            continue

        valid_normalized = {_normalize_for_domain_match(v) for v in valid_set}
        normalized_col = df_data[col].astype(str).map(_normalize_for_domain_match)
        filled = df_data[col].notna() & (normalized_col != "")
        invalid = filled & ~normalized_col.isin(valid_normalized)

        if not invalid.any():
            continue

        allowed_str = ", ".join(sorted(valid_set))
        for _, row in df_data.loc[invalid, [key_col, col]].iterrows():
            field_name = str(row[key_col])
            bad_val = str(row[col]).strip()
            _log(
                f"  ❌ Hoja '{sheet_name}': campo '{field_name}' — "
                f"'{col}' = '{bad_val}' no es un valor permitido. "
                f"Permitidos: {allowed_str}"
            )
        total_errors += int(invalid.sum())

    return total_errors


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


def _title_from_key(value: object) -> str:
    """
    Construye un title legible a partir de key.

    Ejemplo:
      2.DBZPRD_MEX_OXXO.LAND_EBS_TRAN.AP_INVOICES_ALL.PAY_CURR_INVOICE_AMOUNT
      -> Pay curr invoice amount
    """
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip()
    if not text:
        return ""

    tail = text.split(".")[-1].strip()
    if not tail:
        return ""

    normalized = re.sub(r"\s+", " ", tail.replace("_", " ").lower()).strip()
    if not normalized:
        return ""

    return normalized[0].upper() + normalized[1:]


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


def upload_alation_format(
    api_token: str = "",
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Sube CSVs en formato nativo de Alation directo, sin merge ni lang.

    Lee los `.csv` que el usuario haya cargado en `data/format/`, deriva el
    OID del prefijo del nombre (`{oid}_…csv`) y los entrega tal cual al
    endpoint `data_dictionary/table/{oid}/upload/`.

    Pensado para el flujo "descargo → edito el CSV a mano → subo de vuelta":
    el archivo ya viene en el formato que Alation espera, no hace falta
    fusionarlo con un Excel ni traducirlo entre ES/EN.

    Args:
        api_token:    API Token (sesión). Si vacío, usa .env.
        log_callback: Función para emitir progreso.
    """
    _log = log_callback or logger.info

    ok, msg = validate_auth("upload", api_token=api_token)
    if not ok:
        _log(f"❌ Auth fallida: {msg}")
        raise RuntimeError(msg)
    _log(f"✓ {msg}")

    files_csv = sorted(settings.source_format.glob("*.csv"))
    if not files_csv:
        _log("⚠ No hay CSV en data/format/. Sube primero un archivo en formato Alation.")
        raise FileNotFoundError("No hay CSV en data/format/")

    _log(f"📦 {len(files_csv)} archivo(s) CSV listos para subir en formato Alation.")

    total_uploaded = 0
    total_failed = 0

    for path_csv in files_csv:
        _log(f"📄 Procesando: {path_csv.name}")

        oid_match = re.match(r"^(\d+)_", path_csv.name)
        if not oid_match:
            _log(
                f"  ❌ {path_csv.name}: no se pudo extraer OID del nombre. "
                "Debe empezar con dígitos seguidos de '_'."
            )
            total_failed += 1
            continue
        oid = oid_match.group(1)

        try:
            encoding = _detect_encoding(str(path_csv))
            url = (
                f"{settings.alation_base_url}"
                f"/integration/v1/data_dictionary/table/{oid}/upload/"
            )
            status = _upload_to_alation(url, str(path_csv), encoding, api_token)
            _log(f"    ✅ OID={oid} subido exitosamente · HTTP {status}")
            total_uploaded += 1

        except requests.HTTPError as http_err:
            _log(f"    ❌ OID={oid}: Alation rechazó la subida — {http_err}")
            logger.error("HTTPError OID=%s: %s", oid, http_err)
            total_failed += 1

        except Exception as exc:
            _log(f"    ❌ Error procesando {path_csv.name}: {exc}")
            logger.exception("Error procesando %s", path_csv.name)
            total_failed += 1

    parts = [f"✅ {total_uploaded} CSV(s) subidos a Alation"]
    if total_failed:
        parts.append(f"❌ {total_failed} fallido(s) (revisa los logs)")
    _log(f"\n{'  |  '.join(parts)}")

    if total_uploaded == 0 and total_failed > 0:
        raise RuntimeError(
            f"Ningún CSV se subió correctamente. {total_failed} error(es). Revisa los logs."
        )


def upload_all(
    api_token: str = "",
    bearer_token: str = "",
    lang: str = "es",
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """
    Fusiona los Excel de formato con los CSV de Alation y sube el resultado.

    El Excel debe tener una hoja por tabla, con el nombre = OID numérico de la tabla.
    Hojas con nombre no numérico (Guía, LISTAS, etc.) se ignoran automáticamente.

    Si el CSV de Alation para un OID no existe en `data/alation/`, se descarga
    automáticamente antes del merge.

    Args:
        api_token:    API Token del usuario (sesión). Si vacío, usa .env.
        bearer_token: Bearer Token del usuario (sesión). Necesario para
                      auto-descargar CSVs faltantes. Si vacío, usa .env.
        lang:         "es" o "en".
        log_callback: Función para emitir progreso.
    """
    _log = log_callback or logger.info

    ok, msg = validate_auth("upload", api_token=api_token)
    if not ok:
        _log(f"❌ Auth fallida (upload): {msg}")
        raise RuntimeError(msg)
    _log(f"✓ {msg}")

    ok, msg = validate_auth("download", bearer_token=bearer_token, api_token=api_token)
    if not ok:
        _log(f"❌ Auth fallida (download): {msg}")
        raise RuntimeError(msg)
    _log(f"✓ {msg}")

    settings.destination.mkdir(parents=True, exist_ok=True)
    settings.source_alation.mkdir(parents=True, exist_ok=True)

    config = CONFIG_ES if lang == "es" else CONFIG_EN
    files_xls = sorted(settings.source_format.glob("*.xlsx"))

    if not files_xls:
        _log("⚠ No hay Excel en data/format/.")
        raise FileNotFoundError("No hay Excel en data/format/")

    # Índice de CSVs disponibles: OID → Path. Puede estar vacío al inicio:
    # los CSVs faltantes se descargan dentro del loop.
    csv_index = _build_csv_index(settings.source_alation)

    total_uploaded = 0
    total_warned = 0
    total_failed = 0

    for path_xls in files_xls:
        _log(f"📄 Procesando Excel: {path_xls.name}")

        try:
            # Cerrar explícitamente el handle del xlsx para evitar bloqueos en Windows.
            with pd.ExcelFile(path_xls, engine="openpyxl") as xl:
                all_sheets = xl.sheet_names
        except Exception as exc:
            _log(f"  ❌ No se pudo leer {path_xls.name}: {exc}")
            total_failed += 1
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

        # Auto-descargar CSVs faltantes desde Alation antes del merge.
        oids_faltantes = [oid for oid, _ in table_sources if oid not in csv_index]
        if oids_faltantes:
            _log(f"  ⬇ Descargando CSV de Alation para {len(oids_faltantes)} OID(s) sin CSV local...")
            for oid in oids_faltantes:
                try:
                    _download_table(int(oid), bearer_token, api_token, _log)
                except Exception as exc:
                    _log(f"    ⚠ No se pudo descargar OID={oid}: {exc}")
                    logger.exception("Auto-descarga falló OID=%s", oid)
            # Re-indexar tras las descargas
            csv_index = _build_csv_index(settings.source_alation)

        # Detectar hojas sin CSV (tras intentar la auto-descarga)
        ids_con_csv = [(oid, sheet_name) for oid, sheet_name in table_sources if oid in csv_index]
        ids_sin_csv = [(oid, sheet_name) for oid, sheet_name in table_sources if oid not in csv_index]

        if ids_sin_csv:
            faltantes = ", ".join(f"{oid} (hoja: {sheet_name})" for oid, sheet_name in ids_sin_csv)
            _log(
                f"  ⚠ No se pudo obtener CSV para: {faltantes}. Esas hojas serán omitidas."
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

                # ── PUNTO 1: Validar encabezados DataGov ─────────────────────
                if not _validate_headers(df_data, config, _log, sheet_name):
                    total_failed += 1
                    continue

                # ── PUNTO 2: Validar errores por registro ─────────────────────
                _validate_rows(df_data, config, _log, sheet_name)

                # ── PUNTO 2B: Validar valores de dominio fijo ─────────────────
                if _validate_column_values(df_data, config, _log, sheet_name) > 0:
                    _log(
                        f"  ❌ Hoja '{sheet_name}': subida bloqueada por valores inválidos. "
                        "Corrige el Excel y vuelve a intentarlo."
                    )
                    total_failed += 1
                    continue

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

                if "title" in df_union.columns and "key" in df_union.columns:
                    missing_title = (
                        df_union["title"].isna()
                        | (df_union["title"].astype(str).str.strip() == "")
                    )
                    df_union.loc[missing_title, "title"] = (
                        df_union.loc[missing_title, "key"].apply(_title_from_key)
                    )

                path_dest = settings.destination / f"{oid}.csv"
                df_union.to_csv(path_dest, index=False, encoding=encoding)

                url = (
                    f"{settings.alation_base_url}"
                    f"/integration/v1/data_dictionary/table/{oid}/upload/"
                )

                # ── PUNTO 3: Subida con confirmación real de HTTP ─────────────
                status = _upload_to_alation(url, str(path_dest), encoding, api_token)
                _log(
                    f"    ✅ OID={oid} ({path_csv.stem}) subido exitosamente "
                    f"· HTTP {status}"
                )
                total_uploaded += 1

            except requests.HTTPError as http_err:
                _log(f"    ❌ OID={oid}: Alation rechazó la subida — {http_err}")
                logger.error("HTTPError OID=%s: %s", oid, http_err)
                total_failed += 1

            except Exception as exc:
                _log(f"    ❌ Error procesando OID={oid}: {exc}")
                logger.exception("Error procesando OID=%s", oid)
                total_failed += 1

    # ── Resumen final ─────────────────────────────────────────────────────────
    parts = [f"✅ {total_uploaded} tabla(s) subidas exitosamente a Alation"]
    if total_warned:
        parts.append(f"⚠ {total_warned} omitida(s) por falta de CSV")
    if total_failed:
        parts.append(f"❌ {total_failed} fallida(s) (revisa los logs)")
    _log(f"\n{'  |  '.join(parts)}")

    if total_uploaded == 0 and total_failed > 0:
        raise RuntimeError(
            f"Ninguna tabla se subió correctamente. {total_failed} error(es). Revisa los logs."
        )
