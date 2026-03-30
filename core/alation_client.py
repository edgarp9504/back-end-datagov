"""Cliente HTTP para la API de Alation con manejo de paginación y rate-limit."""
import logging
import time

import requests

from core.settings import settings

logger = logging.getLogger(__name__)


def validate_auth(
    auth_type: str,
    bearer_token: str = "",
    api_token: str = "",
) -> tuple[bool, str]:
    """
    Valida el token contra Alation.

    Args:
        auth_type:    "download" (Bearer token) | "upload" (API token)
        bearer_token: Token de sesión del usuario (sobreescribe .env si se provee)
        api_token:    Token de sesión del usuario (sobreescribe .env si se provee)

    Returns:
        (éxito, mensaje)
    """
    bt = bearer_token or settings.alation_bearer_token
    at = api_token or settings.alation_api_token

    if auth_type == "download":
        if not bt:
            return False, "Bearer Token no configurado. Ingresa tu token en la sesión."
        session = _build_session_bearer(bt)
    elif auth_type == "upload":
        if not at:
            return False, "API Token no configurado. Ingresa tu token en la sesión."
        session = _build_session_api_token(at)
    else:
        return False, f"auth_type inválido: '{auth_type}'"

    try:
        r = session.get(
            f"{settings.alation_base_url}/catalog/table/",
            params={"limit": 1},
            timeout=15,
        )
        if r.status_code == 200:
            return True, f"Conexión exitosa con {settings.alation_base_url}"
        if r.status_code == 401:
            return False, "Token inválido o expirado. Genera uno nuevo en Alation."
        if r.status_code == 403:
            return False, "Acceso denegado. Verifica los permisos del token."
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except requests.exceptions.ConnectionError:
        return False, f"No se pudo conectar con {settings.alation_base_url}. Verifica URL y red."
    except requests.exceptions.Timeout:
        return False, f"Tiempo de espera agotado al conectar con {settings.alation_base_url}."
    except Exception as exc:
        return False, f"Error inesperado: {exc}"


def get_all(
    url: str,
    params: dict,
    *,
    auth_type: str | None = None,
    bearer_token: str = "",
    api_token: str = "",
) -> list:
    """
    Obtiene todos los registros paginados de un endpoint de Alation.

    Args:
        url:          URL completa del endpoint.
        params:       Parámetros de consulta base.
        auth_type:    "download", "upload" o None.
        bearer_token: Token de sesión (sobreescribe .env).
        api_token:    Token de sesión (sobreescribe .env).

    Returns:
        Lista con todos los registros obtenidos.
    """
    bt = bearer_token or settings.alation_bearer_token
    at = api_token or settings.alation_api_token

    params = dict(params)
    params.setdefault("limit", 500)
    params["skip"] = 0

    if auth_type == "download":
        session = _build_session_bearer(bt)
    elif auth_type == "upload":
        session = _build_session_api_token(at)
    else:
        # Por defecto: Bearer si está disponible, sino API token
        session = _build_session_bearer(bt) if bt else _build_session_api_token(at)

    results: list = []

    while True:
        response = session.get(url, params=params)

        if response.status_code == 429:
            logger.warning("Rate limit alcanzado. Esperando 2 segundos...")
            time.sleep(2)
            continue

        response.raise_for_status()
        data = response.json()
        page = data if isinstance(data, list) else data.get("results", [])

        if not page:
            break

        results.extend(page)
        logger.debug("Obtenidos %d registros (total: %d)", len(page), len(results))

        if len(page) < params["limit"]:
            break

        params["skip"] += params["limit"]

    return results


# ── Builders de sesión ────────────────────────────────────────────────────────

def _build_session_bearer(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "accept": "application/json"})
    return s


def _build_session_api_token(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"TOKEN": token, "accept": "application/json"})
    return s
