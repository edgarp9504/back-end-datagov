"""Dependencias compartidas de FastAPI."""
from fastapi import Request
from core.settings import settings


def get_tokens(request: Request) -> dict[str, str]:
    """
    Extrae los tokens de autenticación del header de la request.

    Prioridad:
        1. Headers X-Bearer-Token / X-Api-Token  (tokens de sesión del usuario)
        2. Variables de entorno en .env           (fallback para uso CLI)
    """
    return {
        "bearer_token": request.headers.get("x-bearer-token") or settings.alation_bearer_token,
        "api_token":    request.headers.get("x-api-token")    or settings.alation_api_token,
    }
