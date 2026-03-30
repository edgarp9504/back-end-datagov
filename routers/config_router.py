"""Endpoints para consultar y actualizar la configuración (.env)."""
import importlib
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from models.schemas import ConfigResponse, ConfigUpdate
import core.settings as settings_module
from core.settings import settings

router = APIRouter()

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

_KEY_MAP = {
    "base_url":     "ALATION_BASE_URL",
    "bearer_token": "ALATION_BEARER_TOKEN",
    "api_token":    "ALATION_API_TOKEN",
    "csv_encoding": "CSV_ENCODING",
}


def _read_config() -> dict:
    return {
        "base_url":           settings.alation_base_url,
        "auth_mode":          settings.alation_auth_mode,
        "bearer_token_set":   settings.bearer_token_configured,
        "api_token_set":      settings.api_token_configured,
        "path_source_alation": str(settings.source_alation),
        "path_source_format":  str(settings.source_format),
        "path_destination":    str(settings.destination),
        "path_tables":         str(settings.tables_file),
        "csv_encoding":        settings.csv_encoding,
    }


def _write_env(updates: dict) -> None:
    """Actualiza claves específicas en el .env del backend."""
    if not _ENV_PATH.exists():
        # Crear archivo vacío si no existe
        _ENV_PATH.touch()

    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated_keys: set[str] = set()
    new_lines = []

    for line in lines:
        written = False
        for field, env_key in _KEY_MAP.items():
            if field in updates and updates[field] is not None:
                if line.startswith(f"{env_key}=") or line.startswith(f"{env_key} ="):
                    new_lines.append(f"{env_key}={updates[field]}")
                    updated_keys.add(field)
                    written = True
                    break
        if not written:
            new_lines.append(line)

    # Agregar claves que no existían aún
    for field, env_key in _KEY_MAP.items():
        if field in updates and updates[field] is not None and field not in updated_keys:
            new_lines.append(f"{env_key}={updates[field]}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@router.get("/", response_model=ConfigResponse)
async def get_config():
    """Retorna la configuración activa (tokens ocultos, solo indica si están seteados)."""
    return _read_config()


@router.put("/", response_model=ConfigResponse)
async def update_config(body: ConfigUpdate):
    """Actualiza el archivo .env y recarga la configuración en memoria."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No se proporcionaron campos a actualizar.")

    _write_env(updates)

    # Recargar .env y el módulo de settings
    load_dotenv(_ENV_PATH, override=True)
    importlib.reload(settings_module)

    return _read_config()
