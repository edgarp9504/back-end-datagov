"""Punto de entrada de la API FastAPI — DataDictionary Manager."""
import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.settings import settings
from routers import auth, jobs, tables, files, config_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: crear directorios de datos si no existen
    for directory in (
        settings.source_alation,
        settings.source_format,
        settings.destination,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    logger.info("DataDictionary API iniciada")
    logger.info("  Alation URL : %s", settings.alation_base_url)
    logger.info("  Data dir    : %s", settings.data_root)
    logger.info("  Docs        : http://localhost:8000/docs")
    yield
    logger.info("DataDictionary API detenida")


app = FastAPI(
    title="DataDictionary API",
    description=(
        "API interna para gestión del Diccionario de Datos con Alation.\n\n"
        "Permite descargar, enriquecer y subir metadatos de tablas y columnas."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

_ALLOWED_ORIGINS = [
    "https://datadictionary-frontend.vercel.app",
    "https://datadictionary-frontend-r9cm5ao3n-edgarp9504s-projects.vercel.app",
    "http://localhost:5173",  # dev local
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,          prefix="/api/v1/auth",   tags=["Autenticación"])
app.include_router(jobs.router,          prefix="/api/v1/jobs",   tags=["Jobs"])
app.include_router(tables.router,        prefix="/api/v1/tables", tags=["Tablas"])
app.include_router(files.router,         prefix="/api/v1/files",  tags=["Archivos"])
app.include_router(config_router.router, prefix="/api/v1/config", tags=["Configuración"])


@app.get("/", tags=["Root"])
async def root():
    return {"app": "DataDictionary API", "version": "1.0.0", "docs": "/docs"}
