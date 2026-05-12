"""Configuración del backend usando Pydantic Settings."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Directorio raíz del backend (donde vive este archivo)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env siempre relativo a la raíz del backend
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Alation API
    alation_base_url: str = "https://proximidad.alationcloud.com"
    alation_auth_mode: str = "oauth"
    alation_bearer_token: str = ""
    alation_api_token: str = ""

    # Cuenta de servicio (refresh token + user_id). Si están presentes, el
    # backend funciona sin que el usuario tenga que pegar nada en la UI:
    # cada request los intercambia automáticamente por un API access token.
    alation_refresh_token: str = ""
    alation_user_id: int = 0

    # Sólo se exponen al navegador los datasources cuyo título empiece con
    # alguno de estos prefijos (case-insensitive). Valor por defecto:
    # "Snowflake" — oculta Alation Analytics, Databricks, Data Factory, etc.
    # Lista separada por comas. Vacío = mostrar todos.
    alation_datasource_title_prefixes: str = "Snowflake"

    # Directorio de datos (relativo a la raíz del backend)
    path_data: str = "data"

    # Encoding para los CSV
    csv_encoding: str = "UTF-8-SIG"

    # ── Rutas derivadas ──────────────────────────────────────────────────────
    @property
    def data_root(self) -> Path:
        return (_BACKEND_ROOT / self.path_data).resolve()

    @property
    def source_alation(self) -> Path:
        """Carpeta donde se guardan los CSV descargados desde Alation."""
        return self.data_root / "alation"

    @property
    def source_format(self) -> Path:
        """Carpeta donde el usuario deposita los Excel de enriquecimiento."""
        return self.data_root / "format"

    @property
    def destination(self) -> Path:
        """Carpeta de salida con los CSV listos para subir a Alation."""
        return self.data_root / "destination"

    @property
    def tables_file(self) -> Path:
        """Archivo Excel con los OIDs de las tablas a procesar."""
        return self.data_root / "Tables.xlsx"

    @property
    def bearer_token_configured(self) -> bool:
        return bool(self.alation_bearer_token and self.alation_bearer_token != "tu_bearer_token_aqui")

    @property
    def api_token_configured(self) -> bool:
        return bool(self.alation_api_token and self.alation_api_token != "tu_api_token_aqui")


# Instancia global — se crea una vez al importar el módulo
settings = Settings()
