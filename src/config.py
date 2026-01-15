"""
Módulo de configuración del agente ECF.
Carga configuración desde YAML y variables de entorno.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from loguru import logger


class ConfigError(Exception):
    """Error en la configuración."""
    pass


class Config:
    """Carga y gestiona la configuración del agente."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Inicializa la configuración.
        
        Args:
            config_path: Ruta al archivo de configuración YAML.
                        Si no se especifica, busca en ./config/config.yaml
        """
        self._config: Dict[str, Any] = {}
        self._config_path = self._resolve_config_path(config_path)
        
        # Cargar variables de entorno desde .env
        self._load_env()
        
        # Cargar configuración YAML
        self._load_yaml()
        
        # Resolver variables de entorno en la configuración
        self._resolve_env_vars()
        
        logger.info(f"Configuración cargada desde: {self._config_path}")

    def _resolve_config_path(self, config_path: Optional[str]) -> Path:
        """Resuelve la ruta del archivo de configuración."""
        if config_path:
            path = Path(config_path)
        else:
            # Buscar en ubicaciones por defecto
            candidates = [
                Path("./config/config.yaml"),
                Path("./config.yaml"),
                Path.home() / ".ecf-agent" / "config.yaml",
            ]
            path = next((p for p in candidates if p.exists()), candidates[0])
        
        if not path.exists():
            raise ConfigError(f"Archivo de configuración no encontrado: {path}")
        
        return path.resolve()

    def _load_env(self):
        """Carga variables de entorno desde .env."""
        env_paths = [
            Path("./.env"),
            self._config_path.parent.parent / ".env",
            Path.home() / ".ecf-agent" / ".env",
        ]
        
        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path)
                logger.debug(f"Variables de entorno cargadas desde: {env_path}")
                return

    def _load_yaml(self):
        """Carga el archivo YAML de configuración."""
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Error parseando YAML: {e}")

    def _resolve_env_vars(self, obj: Any = None) -> Any:
        """
        Resuelve variables de entorno en formato ${VAR_NAME}.
        
        Args:
            obj: Objeto a procesar (dict, list, str)
        
        Returns:
            Objeto con variables resueltas
        """
        if obj is None:
            obj = self._config
        
        if isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # Patrón: ${VAR_NAME} o ${VAR_NAME:default}
            pattern = r"\$\{([^}:]+)(?::([^}]*))?\}"
            
            def replace(match):
                var_name = match.group(1)
                default = match.group(2)
                value = os.environ.get(var_name)
                
                if value is None and default is None:
                    logger.warning(f"Variable de entorno no definida: {var_name}")
                    return match.group(0)  # Mantener el placeholder
                
                return value if value is not None else default
            
            return re.sub(pattern, replace, obj)
        else:
            return obj

    def get(self, key: str, default: Any = None) -> Any:
        """
        Obtiene un valor de configuración usando notación de puntos.
        
        Args:
            key: Clave en formato "section.subsection.key"
            default: Valor por defecto si no existe
        
        Returns:
            Valor de configuración
        
        Example:
            config.get("database.host", "localhost")
        """
        keys = key.split(".")
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return self._resolve_env_vars(value)
        except (KeyError, TypeError):
            return default

    def __getitem__(self, key: str) -> Any:
        """Acceso por corchetes: config["database"]["host"]."""
        return self._config[key]

    @property
    def agent(self) -> Dict[str, Any]:
        """Configuración del agente."""
        return self._config.get("agent", {})

    @property
    def api(self) -> Dict[str, Any]:
        """Configuración de la API."""
        api_config = self._config.get("api", {})
        return self._resolve_env_vars(api_config)

    @property
    def database(self) -> Dict[str, Any]:
        """Configuración de la base de datos."""
        db_config = self._config.get("database", {})
        return self._resolve_env_vars(db_config)

    @property
    def mappings(self) -> Dict[str, Any]:
        """Configuración de mappings."""
        return self._config.get("mappings", {})

    @property
    def logging(self) -> Dict[str, Any]:
        """Configuración de logging."""
        return self._config.get("logging", {})

    def validate(self) -> bool:
        """
        Valida que la configuración tenga todos los campos requeridos.
        
        Returns:
            True si la configuración es válida
        
        Raises:
            ConfigError: Si falta algún campo requerido
        """
        required = [
            ("agent.customer_rnc", "RNC del cliente"),
            ("api.base_url", "URL base de la API"),
            ("api.endpoint", "Endpoint de la API"),
            ("database.driver", "Driver de base de datos"),
            ("database.host", "Host de base de datos"),
            ("database.database", "Nombre de base de datos"),
            ("database.query", "Query de consulta"),
        ]
        
        missing = []
        for key, description in required:
            if not self.get(key):
                missing.append(f"  - {key}: {description}")
        
        if missing:
            raise ConfigError(
                "Faltan campos requeridos en la configuración:\n" + "\n".join(missing)
            )
        
        logger.info("Configuración validada correctamente")
        return True


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Función helper para cargar configuración.
    
    Args:
        config_path: Ruta opcional al archivo de configuración
    
    Returns:
        Instancia de Config
    """
    return Config(config_path)
