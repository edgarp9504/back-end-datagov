"""Dependencias compartidas de FastAPI."""
import logging

from fastapi import Request

from core.settings import settings
from services.alation_auth import exchange_to_access_token

logger = logging.getLogger(__name__)


def get_tokens(request: Request) -> dict[str, str]:
    """
    Resuelve los tokens de autenticación para hablar con Alation.

    Orden de resolución:
        1. Headers X-Refresh-Token + X-User-Id  →
           Refresh token + user_id del usuario. Se intercambian por un
           API access token cacheado y se devuelven.

        2. settings.alation_refresh_token + settings.alation_user_id →
           **Modo cuenta de servicio.** Mismo intercambio pero con los
           valores del .env / variables de entorno de Railway. Pensado
           para que el usuario final no tenga que pegar nada en la UI.

        3. Headers X-Bearer-Token / X-Api-Token →
           Tokens directos (modo legado). Se usan tal cual.

        4. settings.alation_bearer_token / settings.alation_api_token →
           Fallback final para CLI o scripts antiguos.

    El access token obtenido vía OAuth sirve tanto para `Authorization: Bearer …`
    (catálogo) como para `TOKEN: …` (integración), así que rellenamos ambos
    campos con el mismo valor cuando venimos del flujo OAuth.
    """
    # 1) Headers de usuario con refresh token
    refresh_token = request.headers.get("x-refresh-token")
    raw_user_id = request.headers.get("x-user-id")
    if refresh_token and raw_user_id:
        try:
            user_id = int(raw_user_id)
        except ValueError:
            logger.warning("X-User-Id no numérico: %r", raw_user_id)
        else:
            access = _try_exchange(refresh_token, user_id)
            if access:
                return {"bearer_token": access, "api_token": access}

    # 2) Cuenta de servicio (env vars)
    if settings.alation_refresh_token and settings.alation_user_id:
        access = _try_exchange(
            settings.alation_refresh_token, settings.alation_user_id
        )
        if access:
            return {"bearer_token": access, "api_token": access}

    # 3) y 4) Tokens directos (headers o env vars legados)
    return {
        "bearer_token": request.headers.get("x-bearer-token") or settings.alation_bearer_token,
        "api_token":    request.headers.get("x-api-token")    or settings.alation_api_token,
    }


def _try_exchange(refresh_token: str, user_id: int) -> str | None:
    """Intercambio robusto: nunca lanza, sólo registra el error."""
    try:
        return exchange_to_access_token(refresh_token, user_id)
    except Exception as exc:
        logger.warning(
            "Fallo al intercambiar refresh token (user_id=%s): %s",
            user_id, exc,
        )
        return None
