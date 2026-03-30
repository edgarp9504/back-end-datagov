"""Endpoints para lanzar y consultar jobs de descarga/carga."""
from fastapi import APIRouter, Depends, HTTPException
from models.schemas import JobResponse, JobStatusResponse, UploadRequest, DownloadRequest
from services.job_manager import create_job, get_job, list_jobs, run_job
from core.download import download_all
from core.upload import upload_all
from dependencies import get_tokens

router = APIRouter()


def _job_to_response(job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        logs=list(job.logs),
        created_at=job.created_at,
        completed_at=job.completed_at,
        error=job.error,
    )


@router.post("/download", response_model=JobResponse)
async def start_download(request: DownloadRequest, tokens: dict = Depends(get_tokens)):
    """
    Inicia la descarga de diccionarios desde Alation.

    Si se pasan `oids` en el body, descarga solo esas tablas.
    Si no se pasan, usa los OIDs del Tables.xlsx del servidor.
    """
    job = create_job("Descarga")
    run_job(
        job,
        download_all,
        bearer_token=tokens["bearer_token"],
        api_token=tokens["api_token"],
        oids=request.oids,
    )
    return JobResponse(job_id=job.job_id, status=job.status)


@router.post("/upload", response_model=JobResponse)
async def start_upload(request: UploadRequest, tokens: dict = Depends(get_tokens)):
    """
    Inicia la carga de diccionarios hacia Alation.

    Usa el token del header X-Api-Token de la sesión del usuario.
    """
    job = create_job(f"Carga ({request.lang.upper()})")
    run_job(
        job,
        upload_all,
        api_token=tokens["api_token"],
        lang=request.lang,
    )
    return JobResponse(job_id=job.job_id, status=job.status)


@router.get("/", response_model=list[JobStatusResponse])
async def get_all_jobs():
    """Lista todos los jobs (más recientes primero)."""
    return [_job_to_response(j) for j in list_jobs()]


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Retorna el estado y logs de un job específico."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' no encontrado.")
    return _job_to_response(job)
