"""Endpoints para gestionar archivos de datos (Tables.xlsx, CSVs, Excels)."""
import io
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, Response

from models.schemas import FileInfo, TablesStatus
from core.settings import settings

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_info(path: Path) -> FileInfo:
    stat = path.stat()
    return FileInfo(
        name=path.name,
        size_kb=round(stat.st_size / 1024, 2),
        modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    )


def _list_dir(directory: Path, pattern: str) -> list[FileInfo]:
    if not directory.exists():
        return []
    return [_file_info(f) for f in sorted(directory.glob(pattern))]


# ── Tables.xlsx ───────────────────────────────────────────────────────────────

@router.get("/tables/status", response_model=TablesStatus)
async def tables_status():
    """Estado actual de Tables.xlsx: si existe y cuántas tablas contiene."""
    if not settings.tables_file.exists():
        return TablesStatus(exists=False)
    try:
        df = pd.read_excel(settings.tables_file, sheet_name="Tables", engine="openpyxl")
        oids = df["Oid"].dropna().astype(int).tolist()
    except Exception:
        oids = []
    info = _file_info(settings.tables_file)
    return TablesStatus(exists=True, num_tables=len(oids), oids=oids, file_info=info)


@router.post("/tables/upload", response_model=TablesStatus, status_code=201)
async def upload_tables_file(file: UploadFile = File(...)):
    """
    Sube el Tables.xlsx con los OIDs de las tablas a descargar.
    Reemplaza el archivo actual. Valida que tenga hoja 'Tables' con columna 'Oid'.
    """
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Solo se acepta un archivo .xlsx")

    content = await file.read()

    # Validar estructura antes de guardar
    try:
        df = pd.read_excel(io.BytesIO(content), sheet_name="Tables", engine="openpyxl")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"No se pudo leer la hoja 'Tables': {exc}",
        )

    if "Oid" not in df.columns:
        raise HTTPException(
            status_code=422,
            detail="El archivo debe tener una hoja 'Tables' con una columna llamada 'Oid'.",
        )

    oids = df["Oid"].dropna().astype(int).tolist()
    if not oids:
        raise HTTPException(status_code=422, detail="La columna 'Oid' está vacía.")

    settings.tables_file.parent.mkdir(parents=True, exist_ok=True)
    settings.tables_file.write_bytes(content)

    info = _file_info(settings.tables_file)
    return TablesStatus(exists=True, num_tables=len(oids), oids=oids, file_info=info)


# ── CSVs de Alation (descargados) ────────────────────────────────────────────

@router.get("/alation", response_model=list[FileInfo])
async def list_alation_files():
    """Lista los CSV descargados desde Alation (data/alation/)."""
    return _list_dir(settings.source_alation, "*.csv")


@router.get("/alation/download-zip")
async def download_alation_zip(
    oids: str | None = Query(
        None,
        description="OIDs separados por coma para filtrar. Si se omite, empaqueta todos los CSVs.",
    ),
):
    """Empaqueta los CSVs de data/alation/ en un ZIP en memoria y lo devuelve."""
    directory = settings.source_alation
    if not directory.exists():
        raise HTTPException(status_code=404, detail="Carpeta 'alation' no existe.")

    files = sorted(directory.glob("*.csv"))

    if oids:
        oid_set = {o.strip() for o in oids.split(",") if o.strip()}
        files = [f for f in files if any(f.name.startswith(o) for o in oid_set)]

    if not files:
        raise HTTPException(status_code=404, detail="No hay archivos para empaquetar.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"alation_csvs_{timestamp}.zip"

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{archive_name}"'},
    )


@router.get("/alation/{filename}/download")
async def download_alation_file(filename: str):
    """Descarga un CSV de data/alation/."""
    target = settings.source_alation / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' no encontrado.")
    return FileResponse(path=str(target), media_type="text/csv", filename=filename)


@router.delete("/alation", status_code=200)
async def delete_alation_files(
    oids: str | None = Query(
        None,
        description="OIDs separados por coma. Solo se borran los CSVs cuyo nombre empieza con uno de estos OIDs. Si se omite, NO borra nada (defensa).",
    ),
):
    """Borra CSVs de `data/alation/` filtrados por OIDs.

    Pensado para el cleanup automático tras descargar el ZIP en el navegador:
    el frontend llama a este endpoint con los mismos OIDs que recién empaquetó.
    """
    if not oids:
        # Defensa: nunca borrar "todo" sin filtro explícito.
        return {"deleted": 0, "files": []}

    directory = settings.source_alation
    if not directory.exists():
        return {"deleted": 0, "files": []}

    oid_set = {o.strip() for o in oids.split(",") if o.strip()}
    if not oid_set:
        return {"deleted": 0, "files": []}

    deleted: list[str] = []
    for f in directory.glob("*.csv"):
        if any(f.name.startswith(o) for o in oid_set):
            try:
                f.unlink()
                deleted.append(f.name)
            except OSError:
                # Si un archivo está bloqueado (Windows) o falla, lo saltamos:
                # un cleanup parcial es mejor que un 500 que no limpia nada.
                continue

    return {"deleted": len(deleted), "files": deleted}


# ── Excels de formato (enriquecimiento) ───────────────────────────────────────

@router.get("/format", response_model=list[FileInfo])
async def list_format_files():
    """Lista los Excel de enriquecimiento (data/format/)."""
    return _list_dir(settings.source_format, "*.xlsx")


@router.post("/format/upload", response_model=FileInfo, status_code=201)
async def upload_format_file(file: UploadFile = File(...)):
    """Sube un Excel de enriquecimiento a data/format/."""
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx")

    settings.source_format.mkdir(parents=True, exist_ok=True)
    dest = settings.source_format / file.filename

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    return _file_info(dest)


@router.delete("/format/{filename}", status_code=204)
async def delete_format_file(filename: str):
    """Elimina un Excel de enriquecimiento de data/format/."""
    target = settings.source_format / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' no encontrado.")
    try:
        target.unlink()
    except PermissionError:
        # En Windows es frecuente que Excel/preview deje el archivo bloqueado.
        raise HTTPException(
            status_code=409,
            detail=f"No se puede eliminar '{filename}' porque está en uso. Cierra el archivo e intenta nuevamente.",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo eliminar '{filename}': {exc}",
        )


# ── CSVs de destino (listos para subir) ───────────────────────────────────────

@router.get("/destination", response_model=list[FileInfo])
async def list_destination_files():
    """Lista los CSV generados listos para subir (data/destination/)."""
    return _list_dir(settings.destination, "*.csv")


@router.get("/destination/{filename}/download")
async def download_destination_file(filename: str):
    """Descarga un CSV de data/destination/."""
    target = settings.destination / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"'{filename}' no encontrado.")
    return FileResponse(path=str(target), media_type="text/csv", filename=filename)
