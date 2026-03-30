"""Gestión de jobs en background para operaciones de descarga/carga."""
import threading
import uuid
from datetime import datetime
from enum import Enum
from typing import Callable


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job:
    def __init__(self, job_id: str, job_type: str):
        self.job_id = job_id
        self.job_type = job_type
        self.status = JobStatus.PENDING
        self.logs: list[str] = []
        self.created_at = datetime.now().isoformat()
        self.completed_at: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def add_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.logs.append(f"[{ts}] {message}")

    def complete(self) -> None:
        with self._lock:
            self.status = JobStatus.COMPLETED
            self.completed_at = datetime.now().isoformat()

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = JobStatus.FAILED
            self.error = error
            self.completed_at = datetime.now().isoformat()


# Almacén global de jobs (en memoria)
_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def create_job(job_type: str) -> Job:
    job_id = str(uuid.uuid4())
    job = Job(job_id, job_type)
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def list_jobs() -> list[Job]:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def run_job(job: Job, func: Callable, **kwargs) -> None:
    """Ejecuta una función en un thread daemon, capturando logs y estado."""

    def _run() -> None:
        with _jobs_lock:
            job.status = JobStatus.RUNNING
        job.add_log(f"Iniciando {job.job_type}...")
        try:
            func(log_callback=job.add_log, **kwargs)
            job.complete()
            job.add_log(f"✓ {job.job_type} finalizado correctamente.")
        except Exception as exc:
            job.fail(str(exc))
            job.add_log(f"❌ Error: {exc}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
