"""
Módulo de configuración del agente ECF.
Carga configuración desde YAML y variables de entorno.
"""

import json
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
        self.config_version: Optional[str] = None
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
                load_dotenv(env_path, override=True)
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
            res = self._resolve_env_vars(value)
            if res is not None and res != "":
                return res
        except (KeyError, TypeError):
            pass

        if default is not None:
            return default

        if key == "api.base_url":
            return os.environ.get("ECF_API_URL", "https://api.tekservices.com.do")
        elif key == "api.endpoint":
            return "/private/ecf/invoices"
        elif key == "api.environment":
            return os.environ.get("ECF_ENVIRONMENT", "DEV")

        return None

    def __getitem__(self, key: str) -> Any:
        return self._config[key]

    @property
    def agent(self) -> Dict[str, Any]:
        return self._config.get("agent", {})

    @property
    def api(self) -> Dict[str, Any]:
        api_config = dict(self._config.get("api", {}))
        if not api_config.get("base_url"):
            api_config["base_url"] = os.environ.get("ECF_API_URL", "https://api.tekservices.com.do")
        if not api_config.get("endpoint"):
            api_config["endpoint"] = "/private/ecf/invoices"
        if not api_config.get("environment"):
            api_config["environment"] = os.environ.get("ECF_ENVIRONMENT", "DEV")
        if not api_config.get("api_key") and os.environ.get("ECF_API_KEY"):
            api_config["api_key"] = os.environ.get("ECF_API_KEY")
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

    def apply_remote_resources(self, resources: Dict[str, Any], version: Optional[str] = None) -> bool:
        changed = False

        if "config" in resources and resources["config"].get("content"):
            try:
                remote_cfg = yaml.safe_load(resources["config"]["content"]) or {}
                if isinstance(remote_cfg, dict):
                    for sec in ("agent", "api", "logging"):
                        if sec in remote_cfg and isinstance(remote_cfg[sec], dict):
                            self._config.setdefault(sec, {})
                            self._config[sec].update(remote_cfg[sec])
                            changed = True
            except Exception as e:
                logger.warning(f"Error parseando sección config remota: {e}")

        if "queries" in resources and resources["queries"].get("content"):
            try:
                queries_content = resources["queries"]["content"]
                parsed_queries = yaml.safe_load(queries_content)
                if isinstance(parsed_queries, dict):
                    self._config.setdefault("database", {})
                    self._config["database"].update(parsed_queries)
                    changed = True
            except Exception as e:
                logger.warning(f"Error parseando queries remotas: {e}")

        if "mapping" in resources and resources["mapping"].get("content"):
            try:
                mapping_content = resources["mapping"]["content"]
                parsed_mapping = json.loads(mapping_content)
                if isinstance(parsed_mapping, dict):
                    self._config.setdefault("database", {})
                    for field in ("id_field", "ecf_field", "type_field", "rnc_buyer_field", "total_field", "processed_field", "status_mapping"):
                        if field in parsed_mapping:
                            self._config["database"][field] = parsed_mapping[field]
                    if "xml_non_convertible_fields" in parsed_mapping:
                        self._config.setdefault("normalize", {})
                        self._config["normalize"]["xml_non_convertible_fields"] = parsed_mapping["xml_non_convertible_fields"]
                    changed = True
            except Exception as e:
                logger.warning(f"Error parseando mapping remoto: {e}")

        if version:
            self.config_version = version

        if changed:
            self._save_effective_cache()

        return changed

    def _save_effective_cache(self):
        try:
            cache_file = Path("./data/effective_config.json")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "version": self.config_version,
                "config": self._config,
            }
            cache_file.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"No se pudo guardar effective_config.json: {e}")

    def sync_remote(self, api_client: Any) -> bool:
        status, data, etag = api_client.fetch_remote_config(self.config_version)
        if status == 200 and data and isinstance(data, dict):
            resources = data.get("resources", {})
            version = data.get("version", etag)
            return self.apply_remote_resources(resources, version)
        return False

    def validate(self) -> bool:
        """
        Valida que la configuración tenga todos los campos requeridos.
        
        Returns:
            True si la configuración es válida
        
        Raises:
            ConfigError: Si falta algún campo requerido
        """
        # Campos siempre requeridos
        required = [
            ("agent.customer_rnc", "RNC del cliente"),
            ("api.base_url", "URL base de la API"),
            ("api.endpoint", "Endpoint de la API"),
            ("api.environment", "Entorno (DEV, CERT, PROD)"),
            ("database.driver", "Driver de base de datos"),
            ("database.database", "Nombre/ruta de base de datos"),
            ("database.query", "Query de consulta"),
        ]
        
        # database.host solo es requerido para drivers que no son SQLite
        driver = self.get("database.driver", "").lower()
        if driver not in ("sqlite", "sqlite3"):
            required.append(("database.host", "Host de base de datos"))
        
        # Validar auto-update
        if self.get("agent.auto_update", False):
            required.append(("agent.update_url", "URL de actualización"))
        
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
