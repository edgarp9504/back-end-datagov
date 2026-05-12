"""Intercambio Refresh Token → API Access Token con cache en memoria.

Alation entrega a cada usuario un "refresh token" (largo, ~80 chars) desde el
panel de Authentication. Para hablarle a la API hay que cambiarlo primero por
un "API Access Token" (~43 chars, vida ~24h) vía:

    POST {ALATION_BASE_URL}/integration/v1/createAPIAccessToken/
    body: { "refresh_token": "...", "user_id": <int> }

Este módulo encapsula ese intercambio y cachea el access token hasta poco
antes de su expiración para no golpear el endpoint en cada request.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from core.settings import settings

logger = logging.getLogger(__name__)

# Margen de seguridad antes de la expiración real (renovamos antes de que caduque)
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)

# Si Alation no manda token_expires_at, asumimos 23h
_DEFAULT_TTL = timedelta(hours=23)


@dataclass(frozen=True)
class _CacheKey:
    base_url: str
    user_id: int
    refresh_token: str


@dataclass
class _CachedToken:
    access_token: str
    expires_at: datetime  # ya con el margen aplicado


_cache: dict[_CacheKey, _CachedToken] = {}
_lock = threading.Lock()


def _parse_expiry(raw: str | None) -> datetime:
    """Convierte el `token_expires_at` de Alation a datetime aware UTC."""
    if not raw:
        return datetime.now(timezone.utc) + _DEFAULT_TTL
    try:
        # Alation devuelve formato ISO: "2026-05-12T23:10:29.914955Z"
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        logger.warning("token_expires_at con formato inesperado: %s", raw)
        return datetime.now(timezone.utc) + _DEFAULT_TTL


def exchange_to_access_token(
    refresh_token: str,
    user_id: int,
    base_url: str | None = None,
) -> str:
    """
    Devuelve un API Access Token válido para el par (refresh_token, user_id).

    Usa el cache si el token aún no expira; si no, llama a Alation.
    Lanza requests.HTTPError si Alation rechaza el intercambio.
    """
    if not refresh_token or not user_id:
        raise ValueError("refresh_token y user_id son obligatorios")

    base = (base_url or settings.alation_base_url).rstrip("/")
    key = _CacheKey(base_url=base, user_id=int(user_id), refresh_token=refresh_token)

    now = datetime.now(timezone.utc)
    with _lock:
        cached = _cache.get(key)
        if cached and cached.expires_at > now:
            return cached.access_token

    # Fuera del lock para no serializar requests concurrentes a Alation.
    response = requests.post(
        f"{base}/integration/v1/createAPIAccessToken/",
        json={"refresh_token": refresh_token, "user_id": int(user_id)},
        headers={"accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    access_token = payload.get("api_access_token")
    if not access_token:
        raise RuntimeError(
            "Respuesta de Alation sin api_access_token: "
            f"{list(payload.keys())}"
        )

    expires_at = _parse_expiry(payload.get("token_expires_at")) - _EXPIRY_SAFETY_MARGIN

    with _lock:
        _cache[key] = _CachedToken(access_token=access_token, expires_at=expires_at)

    logger.info(
        "Access token nuevo para user_id=%s (expira %s)",
        user_id,
        expires_at.isoformat(timespec="seconds"),
    )
    return access_token


def invalidate(refresh_token: str, user_id: int, base_url: str | None = None) -> None:
    """Borra una entrada del cache (útil cuando Alation responde 401)."""
    base = (base_url or settings.alation_base_url).rstrip("/")
    key = _CacheKey(base_url=base, user_id=int(user_id), refresh_token=refresh_token)
    with _lock:
        _cache.pop(key, None)
