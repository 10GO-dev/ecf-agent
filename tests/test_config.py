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
  username: "test_user"
  password: "test_pass"
  
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
