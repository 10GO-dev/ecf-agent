"""Tests para el módulo de configuración."""

import os
import tempfile
from pathlib import Path

import pytest

from src.config import Config, ConfigError, load_config


class TestConfig:
    """Tests para la clase Config."""

    def test_load_valid_config(self, tmp_path):
        """Test carga de configuración válida."""
        config_content = """
agent:
  customer_rnc: "101010101"
  polling_interval_seconds: 30
  
api:
  base_url: "https://api.example.com"
  endpoint: "/private/ecf/dgii-send"
  api_key: "test_api_key_12345"
  
database:
  driver: "mysql"
  host: "localhost"
  port: 3306
  database: "test_db"
  query: "SELECT * FROM facturas LIMIT {batch_size}"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)
        
        config = Config(str(config_file))
        
        assert config.get("agent.customer_rnc") == "101010101"
        assert config.get("api.base_url") == "https://api.example.com"
        assert config.get("database.driver") == "mysql"

    def test_get_with_default(self, tmp_path):
        """Test obtención de valor con default."""
        config_content = """
agent:
  customer_rnc: "123"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)
        
        config = Config(str(config_file))
        
        assert config.get("agent.nonexistent", "default_value") == "default_value"

    def test_env_var_resolution(self, tmp_path, monkeypatch):
        """Test resolución de variables de entorno."""
        monkeypatch.setenv("TEST_PASSWORD", "secret123")
        
        config_content = """
database:
  password: "${TEST_PASSWORD}"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)
        
        config = Config(str(config_file))
        
        assert config.get("database.password") == "secret123"

    def test_missing_config_file(self):
        """Test error cuando no existe el archivo."""
        with pytest.raises(ConfigError):
            Config("/nonexistent/config.yaml")

    def test_validate_missing_required(self, tmp_path):
        """Test validación con campos faltantes."""
        config_content = """
agent:
  polling_interval_seconds: 30
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)
        
        config = Config(str(config_file))
        
        with pytest.raises(ConfigError) as exc_info:
            config.validate()
        
        assert "customer_rnc" in str(exc_info.value)

    def test_normalize_section_is_loaded(self, tmp_path):
        """Test carga de la sección normalize."""
        config_content = """
agent:
  customer_rnc: "101010101"
api:
  base_url: "https://api.example.com"
  endpoint: "/private/ecf/dgii-send"
database:
  driver: "sqlite"
  database: "./data/test.db"
  query: "SELECT 1"
normalize:
  xml_non_convertible_fields:
    - MontoTotal
    - MontoExento
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = Config(str(config_file))

        assert config.get("normalize.xml_non_convertible_fields") == ["MontoTotal", "MontoExento"]

    def test_api_defaults_when_omitted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "_load_env", lambda self: None)
        monkeypatch.delenv("ECF_API_URL", raising=False)
        monkeypatch.delenv("ECF_ENVIRONMENT", raising=False)
        config_content = """
agent:
  customer_rnc: "101010101"
database:
  driver: "sqlite"
  database: ":memory:"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = Config(str(config_file))

        assert config.get("api.base_url") == "https://api.tekservices.com.do"
        assert config.get("api.endpoint") == "/private/ecf/invoices"
        assert config.get("api.environment") == "DEV"
        assert config.api["base_url"] == "https://api.tekservices.com.do"
        assert config.api["endpoint"] == "/private/ecf/invoices"

    def test_customer_rnc_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "_load_env", lambda self: None)
        monkeypatch.setenv("CUSTOMER_RNC", "130099887")
        config_content = """
database:
  driver: "sqlite"
  database: ":memory:"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = Config(str(config_file))

        assert config.get("agent.customer_rnc") == "130099887"
        assert config.agent["customer_rnc"] == "130099887"

    def test_zero_yaml_env_hydration(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Config, "_resolve_config_path", lambda self, p=None: None)
        monkeypatch.setenv("CUSTOMER_RNC", "131222333")
        monkeypatch.setenv("ECF_API_KEY", "secret_key_abc")
        monkeypatch.setenv("ECF_ENVIRONMENT", "CERT")
        monkeypatch.setenv("DB_DRIVER", "mysql")
        monkeypatch.setenv("DB_HOST", "10.0.0.15")
        monkeypatch.setenv("DB_NAME", "erp_prod")
        monkeypatch.setenv("DB_USER", "ecf_reader")
        monkeypatch.setenv("DB_PASSWORD", "db_pass_999")

        config = Config()
        assert config.validate_bootstrap() is True
        assert config.get("agent.customer_rnc") == "131222333"
        assert config.api["api_key"] == "secret_key_abc"
        assert config.api["environment"] == "CERT"
        assert config.database["driver"] == "mysql"
        assert config.database["host"] == "10.0.0.15"
        assert config.database["port"] == 3306
        assert config.database["database"] == "erp_prod"
        assert config.database["username"] == "ecf_reader"
        assert config.database["password"] == "db_pass_999"

