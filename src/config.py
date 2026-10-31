"""
Módulo de configuración del agente ECF.
Carga configuración desde YAML (opcional), variables de entorno (.env) y sincronización remota.
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


SAFE_REMOTE_AGENT_KEYS = {
    "polling_interval_seconds",
    "status_sync_interval_seconds",
    "batch_size",
    "retry_interval_seconds",
    "max_retries",
    "auto_update",
}

SAFE_REMOTE_API_KEYS = {
    "compression",
    "timeout_seconds",
}

SAFE_REMOTE_LOGGING_KEYS = {
    "level",
    "retention_days",
    "max_size_mb",
    "backup_count",
}

SAFE_REMOTE_QUERY_KEYS = {
    "query",
    "details_query",
    "taxes_query",
    "payments_query",
    "reference_info_query",
    "update_query",
    "update_error_query",
    "pending_status_query",
    "status_pending_query",
    "update_status_query",
}

SAFE_REMOTE_MAPPING_KEYS = {
    "id_field",
    "ecf_field",
    "type_field",
    "rnc_buyer_field",
    "total_field",
    "processed_field",
    "status_mapping",
}

DEFAULT_PORTS_BY_DRIVER = {
    "mysql": 3306,
    "mariadb": 3306,
    "postgresql": 5432,
    "postgres": 5432,
    "sqlserver": 1433,
    "mssql": 1433,
    "oracle": 1521,
}


class Config:
    """Carga y gestiona la configuración del agente."""

    def __init__(self, config_path: Optional[str] = None):
        self._config: Dict[str, Any] = {}
        self.config_version: Optional[str] = None
        self._config_path = self._resolve_config_path(config_path)

        self._load_env()
        self._load_yaml()
        self._config = self._resolve_env_vars(self._config)
        self._hydrate_from_env()
        self._load_cached_resources()

        if self._config_path and self._config_path.exists():
            logger.info(f"Configuración cargada desde: {self._config_path}")
        else:
            logger.info("Modo Zero-YAML activo: configuración inicializada desde .env y caché remota")

    def _resolve_config_path(self, config_path: Optional[str]) -> Optional[Path]:
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise ConfigError(f"Archivo de configuración no encontrado: {path}")
            return path.resolve()

        candidates = [
            Path("./config/config.yaml"),
            Path("./config.yaml"),
            Path.home() / ".ecf-agent" / "config.yaml",
        ]
        found = next((p for p in candidates if p.exists()), None)
        return found.resolve() if found else None

    def _load_env(self):
        env_paths = [Path("./.env")]
        if self._config_path:
            env_paths.append(self._config_path.parent.parent / ".env")
            env_paths.append(self._config_path.parent / ".env")
        env_paths.append(Path.home() / ".ecf-agent" / ".env")

        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path, override=True)
                logger.debug(f"Variables de entorno cargadas desde: {env_path}")
                return

    def _load_yaml(self):
        if not self._config_path or not self._config_path.exists():
            self._config = {}
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Error parseando YAML: {e}")

    def _is_unresolved_or_empty(self, val: Any) -> bool:
        if val is None or val == "":
            return True
        if isinstance(val, str) and val.startswith("${") and val.endswith("}"):
            return True
        return False

    def _hydrate_from_env(self):
        agent_sec = self._config.setdefault("agent", {})
        rnc_env = os.environ.get("CUSTOMER_RNC") or os.environ.get("ECF_CUSTOMER_RNC")
        if rnc_env and self._is_unresolved_or_empty(agent_sec.get("customer_rnc")):
            agent_sec["customer_rnc"] = rnc_env

        api_sec = self._config.setdefault("api", {})
        if os.environ.get("ECF_API_URL") and self._is_unresolved_or_empty(api_sec.get("base_url")):
            api_sec["base_url"] = os.environ.get("ECF_API_URL")
        if os.environ.get("ECF_ENVIRONMENT") and self._is_unresolved_or_empty(api_sec.get("environment")):
            api_sec["environment"] = os.environ.get("ECF_ENVIRONMENT")
        if os.environ.get("ECF_API_KEY") and self._is_unresolved_or_empty(api_sec.get("api_key")):
            api_sec["api_key"] = os.environ.get("ECF_API_KEY")

        db_sec = self._config.setdefault("database", {})
        env_db_map = [
            ("driver", ("DB_DRIVER", "ECF_DB_DRIVER")),
            ("host", ("DB_HOST", "ECF_DB_HOST")),
            ("port", ("DB_PORT", "ECF_DB_PORT")),
            ("database", ("DB_NAME", "DB_DATABASE", "ECF_DB_NAME")),
            ("username", ("DB_USER", "DB_USERNAME", "ECF_DB_USER")),
            ("password", ("DB_PASSWORD", "ECF_DB_PASSWORD")),
            ("charset", ("DB_CHARSET",)),
            ("odbc_driver", ("ODBC_DRIVER", "DB_ODBC_DRIVER")),
        ]
        for key, env_vars in env_db_map:
            if self._is_unresolved_or_empty(db_sec.get(key)):
                for ev in env_vars:
                    val = os.environ.get(ev)
                    if val is not None and val != "":
                        db_sec[key] = int(val) if key == "port" and val.isdigit() else val
                        break

        driver = str(db_sec.get("driver", "")).lower()
        if driver in DEFAULT_PORTS_BY_DRIVER and self._is_unresolved_or_empty(db_sec.get("port")):
            db_sec["port"] = DEFAULT_PORTS_BY_DRIVER[driver]

    def _resolve_env_vars(self, obj: Any = None) -> Any:
        if obj is None:
            obj = self._config

        if isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            pattern = r"\$\{([^}:]+)(?::([^}]*))?\}"

            def replace(match):
                var_name = match.group(1)
                default = match.group(2)
                value = os.environ.get(var_name)

                if value is None and default is None:
                    logger.warning(f"Variable de entorno no definida: {var_name}")
                    return match.group(0)

                return value if value is not None else default

            return re.sub(pattern, replace, obj)
        else:
            return obj

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._config

        try:
            for k in keys:
                value = value[k]
            res = self._resolve_env_vars(value)
            if res is not None and res != "" and not self._is_unresolved_or_empty(res):
                return res
        except (KeyError, TypeError):
            pass

        if key == "agent.customer_rnc":
            env_rnc = os.environ.get("CUSTOMER_RNC") or os.environ.get("ECF_CUSTOMER_RNC")
            if env_rnc:
                return env_rnc
        elif key == "api.base_url":
            return os.environ.get("ECF_API_URL", default or "https://api.tekservices.com.do")
        elif key == "api.endpoint":
            return default or "/private/ecf/invoices"
        elif key == "api.environment":
            return os.environ.get("ECF_ENVIRONMENT", default or "DEV")

        if default is not None:
            return default

        return None

    def __getitem__(self, key: str) -> Any:
        return self._config[key]

    @property
    def agent(self) -> Dict[str, Any]:
        agent_config = dict(self._config.get("agent", {}))
        if self._is_unresolved_or_empty(agent_config.get("customer_rnc")):
            rnc = os.environ.get("CUSTOMER_RNC") or os.environ.get("ECF_CUSTOMER_RNC")
            if rnc:
                agent_config["customer_rnc"] = rnc
        return self._resolve_env_vars(agent_config)

    @property
    def api(self) -> Dict[str, Any]:
        api_config = dict(self._config.get("api", {}))
        if self._is_unresolved_or_empty(api_config.get("base_url")):
            api_config["base_url"] = os.environ.get("ECF_API_URL", "https://api.tekservices.com.do")
        if self._is_unresolved_or_empty(api_config.get("endpoint")):
            api_config["endpoint"] = "/private/ecf/invoices"
        if self._is_unresolved_or_empty(api_config.get("environment")):
            api_config["environment"] = os.environ.get("ECF_ENVIRONMENT", "DEV")
        if self._is_unresolved_or_empty(api_config.get("api_key")) and os.environ.get("ECF_API_KEY"):
            api_config["api_key"] = os.environ.get("ECF_API_KEY")
        return self._resolve_env_vars(api_config)

    @property
    def database(self) -> Dict[str, Any]:
        self._hydrate_from_env()
        db_config = dict(self._config.get("database", {}))
        return self._resolve_env_vars(db_config)

    @property
    def mappings(self) -> Dict[str, Any]:
        return self._config.get("mappings", {})

    @property
    def logging(self) -> Dict[str, Any]:
        return self._config.get("logging", {})

    def apply_remote_resources(self, resources: Dict[str, Any], version: Optional[str] = None, save_cache: bool = True) -> bool:
        changed = False
        lock_local_queries = os.environ.get("ECF_LOCK_LOCAL_QUERIES", "").strip().lower() in ("true", "1", "yes")

        if "config" in resources and resources["config"].get("content"):
            try:
                remote_cfg = yaml.safe_load(resources["config"]["content"]) or {}
                if isinstance(remote_cfg, dict):
                    if isinstance(remote_cfg.get("agent"), dict):
                        self._config.setdefault("agent", {})
                        for k, v in remote_cfg["agent"].items():
                            if k in SAFE_REMOTE_AGENT_KEYS:
                                self._config["agent"][k] = v
                                changed = True
                    if isinstance(remote_cfg.get("api"), dict):
                        self._config.setdefault("api", {})
                        for k, v in remote_cfg["api"].items():
                            if k in SAFE_REMOTE_API_KEYS:
                                self._config["api"][k] = v
                                changed = True
                    if isinstance(remote_cfg.get("logging"), dict):
                        self._config.setdefault("logging", {})
                        for k, v in remote_cfg["logging"].items():
                            if k in SAFE_REMOTE_LOGGING_KEYS:
                                self._config["logging"][k] = v
                                changed = True
            except Exception as e:
                logger.warning(f"Error parseando sección config remota: {e}")

        if "queries" in resources and resources["queries"].get("content") and not lock_local_queries:
            try:
                queries_content = resources["queries"]["content"]
                parsed_queries = yaml.safe_load(queries_content)
                if isinstance(parsed_queries, dict):
                    self._config.setdefault("database", {})
                    allowed_keys = SAFE_REMOTE_QUERY_KEYS | SAFE_REMOTE_MAPPING_KEYS
                    for k, v in parsed_queries.items():
                        if k in allowed_keys:
                            target_key = "pending_status_query" if k == "status_pending_query" else k
                            self._config["database"][target_key] = v
                            changed = True
            except Exception as e:
                logger.warning(f"Error parseando queries remotas: {e}")

        if "mapping" in resources and (resources["mapping"].get("content") or resources["mapping"].get("parsed")):
            try:
                mapping_content = resources["mapping"].get("content")
                parsed_mapping = None
                if isinstance(mapping_content, str) and mapping_content.strip():
                    try:
                        parsed_mapping = json.loads(mapping_content)
                    except Exception:
                        parsed_mapping = yaml.safe_load(mapping_content)
                if not isinstance(parsed_mapping, dict) and isinstance(resources["mapping"].get("parsed"), dict):
                    parsed_mapping = resources["mapping"]["parsed"]
                if isinstance(parsed_mapping, dict):
                    self._config.setdefault("database", {})
                    for field in SAFE_REMOTE_MAPPING_KEYS:
                        if field in parsed_mapping:
                            self._config["database"][field] = parsed_mapping[field]
                    if "xml_non_convertible_fields" in parsed_mapping:
                        raw_fields = parsed_mapping["xml_non_convertible_fields"]
                        if isinstance(raw_fields, list):
                            self._config.setdefault("normalize", {})
                            self._config["normalize"]["xml_non_convertible_fields"] = [
                                str(item).strip() for item in raw_fields if str(item).strip()
                            ]
                    changed = True
            except Exception as e:
                logger.warning(f"Error parseando mapping remoto: {e}")

        if version:
            self.config_version = version

        if changed and save_cache:
            self._save_cached_resources(resources, version)

        return changed

    def _get_cache_file(self) -> Path:
        if self._config_path:
            base_dir = self._config_path.parent
            if base_dir.name == "config":
                base_dir = base_dir.parent
        else:
            base_dir = Path(".")
        data_dir = base_dir / "data"
        return data_dir / "remote_config.json"

    def _save_cached_resources(self, resources: Dict[str, Any], version: Optional[str]):
        try:
            cache_file = self._get_cache_file()
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": version or self.config_version,
                "resources": resources,
            }
            cache_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.debug(f"Configuración remota cacheada en {cache_file}")
        except Exception as e:
            logger.debug(f"No se pudo guardar remote_config.json: {e}")

    def _load_cached_resources(self):
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return
            content = cache_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                resources = data.get("resources", {})
                version = data.get("version")
                if resources:
                    self.apply_remote_resources(resources, version, save_cache=False)
                    logger.info(f"Configuración remota restaurada desde caché ({cache_file.name}, versión: {version})")
        except Exception as e:
            logger.warning(f"Error cargando caché de configuración remota: {e}")

    def sync_remote(self, api_client: Any) -> bool:
        status, data, etag = api_client.fetch_remote_config(self.config_version)
        if status == 200 and data and isinstance(data, dict):
            resources = data.get("resources", {})
            version = data.get("version", etag)
            return self.apply_remote_resources(resources, version)
        return False

    def validate_bootstrap(self) -> bool:
        self._hydrate_from_env()
        required = [
            ("agent.customer_rnc", "RNC del cliente (CUSTOMER_RNC)"),
            ("api.base_url", "URL base de la API (ECF_API_URL)"),
            ("api.endpoint", "Endpoint de la API"),
            ("api.environment", "Entorno (ECF_ENVIRONMENT: DEV, CERT, PROD)"),
            ("database.driver", "Driver de base de datos (DB_DRIVER)"),
            ("database.database", "Nombre/ruta de base de datos (DB_NAME)"),
        ]

        driver = str(self.get("database.driver", "")).lower()
        if driver not in ("sqlite", "sqlite3"):
            required.append(("database.host", "Host de base de datos (DB_HOST)"))

        missing = []
        for key, description in required:
            if not self.get(key):
                missing.append(f"  - {key}: {description}")

        if missing:
            raise ConfigError(
                "Faltan campos requeridos en la configuración:\n" + "\n".join(missing)
            )

        return True

    def validate(self, require_query: bool = True) -> bool:
        self.validate_bootstrap()

        missing = []
        if require_query and not self.get("database.query"):
            missing.append("  - database.query: Query de consulta")

        if missing:
            raise ConfigError(
                "Faltan campos requeridos en la configuración:\n" + "\n".join(missing)
            )

        logger.info("Configuración validada correctamente")
        return True


def load_config(config_path: Optional[str] = None) -> Config:
    return Config(config_path)
