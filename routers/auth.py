"""Validación de tokens de autenticación."""
from fastapi import APIRouter, Depends
from models.schemas import AuthValidateResponse
from core.alation_client import validate_auth
from dependencies import get_tokens

router = APIRouter()


@router.get("/validate", response_model=AuthValidateResponse)
async def validate_tokens(tokens: dict = Depends(get_tokens)):
    """
    Valida los tokens del usuario contra Alation.

    Lee los tokens del header X-Bearer-Token / X-Api-Token.
    Si no vienen en el header, usa los del .env como fallback.
    """
    download_ok, download_msg = validate_auth(
        "download", bearer_token=tokens["bearer_token"]
    )
    upload_ok, upload_msg = validate_auth(
        "upload", api_token=tokens["api_token"]
    )
    return AuthValidateResponse(
        download_ok=download_ok,
        download_msg=download_msg,
        upload_ok=upload_ok,
        upload_msg=upload_msg,
    )
