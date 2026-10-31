import json
from unittest.mock import MagicMock
import pytest
from src.config import Config
from src.remote.command_executor import CommandExecutor
from src.remote.heartbeat import HeartbeatManager


def test_apply_remote_resources(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
agent:
  customer_rnc: "101010101"
  polling_interval_seconds: 30
api:
  base_url: "https://api.example.com"
  endpoint: "/private/ecf/invoices"
  api_key: "key123"
database:
  driver: "sqlite"
  database: ":memory:"
  query: "SELECT 1"
""")
    config = Config(str(config_file))

    resources = {
        "config": {
            "content": "agent:\n  batch_size: 99\n  polling_interval_seconds: 15\n"
        },
        "queries": {
            "content": "query: 'SELECT * FROM remote_invoices'\nid_field: 'remote_id'\n"
        },
        "mapping": {
            "content": json.dumps({
                "ecf_field": "remote_ecf",
                "status_mapping": {"accepted": 9}
            })
        }
    }

    changed = config.apply_remote_resources(resources, version="v1.2.3")
    assert changed is True
    assert config.config_version == "v1.2.3"
    assert config.get("agent.batch_size") == 99
    assert config.get("agent.polling_interval_seconds") == 15
    assert config.get("database.query") == "SELECT * FROM remote_invoices"
    assert config.get("database.id_field") == "remote_id"
    assert config.get("database.ecf_field") == "remote_ecf"
    assert config.get("database.status_mapping") == {"accepted": 9}

    cache_file = tmp_path / "data" / "remote_config.json"
    assert cache_file.exists()

    config_restarted = Config(str(config_file))
    assert config_restarted.config_version == "v1.2.3"
    assert config_restarted.get("agent.batch_size") == 99
    assert config_restarted.get("database.query") == "SELECT * FROM remote_invoices"


def test_command_executor_pause_resume():
    agent = MagicMock()
    agent.paused = False
    executor = CommandExecutor(agent)

    res_pause = executor.execute("PAUSE", {})
    assert res_pause["success"] is True
    assert agent.paused is True

    res_resume = executor.execute("RESUME", {})
    assert res_resume["success"] is True
    assert agent.paused is False


def test_command_executor_test_db():
    agent = MagicMock()
    agent.db_connector.execute_scalar.return_value = 1
    agent.db_connector.driver = "sqlite"
    agent.db_connector.database = ":memory:"
    executor = CommandExecutor(agent)

    res = executor.execute("TEST_DB", {})
    assert res["success"] is True
    assert res["result"] == 1
    assert "latency_ms" in res


def test_heartbeat_manager_pulse():
    agent = MagicMock()
    agent.retry_queue.count.return_value = 3
    agent.paused = False
    agent.version = "1.2.5"
    agent.config_version = "hash123"
    agent.api_client.send_heartbeat.return_value = {
        "success": True,
        "config_outdated": True,
        "pending_commands": [{"id": "cmd-1", "command": "PAUSE", "params": {}}],
        "suggested_interval": 10,
    }

    manager = HeartbeatManager(agent)
    manager.pulse()

    agent.api_client.send_heartbeat.assert_called_once()
    payload = agent.api_client.send_heartbeat.call_args[0][0]
    assert payload["queue_size"] == 3
    assert payload["status"] == "ONLINE"
    assert payload["config_version"] == "hash123"

    agent.reload_config.assert_called_once()
    agent.api_client.send_command_result.assert_called_once()
    result_arg = agent.api_client.send_command_result.call_args[0][0]
    assert result_arg["command_id"] == "cmd-1"
    assert result_arg["status"] == "COMPLETED"


def test_command_executor_trigger_update():
    agent = MagicMock()
    agent.updater.check_and_update.return_value = {
        "success": True,
        "updated": True,
        "needs_restart": True,
        "old_version": "1.2.0",
        "new_version": "1.2.1",
    }
    executor = CommandExecutor(agent)

    res = executor.execute("TRIGGER_UPDATE", {"target_version": "1.2.1"})
    assert res["success"] is True
    assert res["updated"] is True
    agent.updater.check_and_update.assert_called_once_with(
        force=True,
        restart_immediately=False,
        target_version="1.2.1",
    )


def test_auto_updater_check_up_to_date(monkeypatch):
    from src.updater import AutoUpdater
    updater = AutoUpdater({"auto_update": True})
    updater.current_version = "1.2.0"

    mock_response = MagicMock()
    mock_response.json.return_value = {"tag_name": "v1.2.0"}
    mock_response.raise_for_status.return_value = None

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_response)

    res = updater.check_and_update()
    assert res["success"] is True
    assert res["updated"] is False
    assert res["new_version"] == "1.2.0"


def test_auto_updater_extract_archive(tmp_path):
    import tarfile
    from src.updater import AutoUpdater
    updater = AutoUpdater({"auto_update": True})

    sample_tar = tmp_path / "test.tar.gz"
    inner_file = tmp_path / "hello.txt"
    inner_file.write_text("world")

    with tarfile.open(sample_tar, "w:gz") as tar:
        tar.add(inner_file, arcname="hello.txt")

    out_dir = tmp_path / "extracted"
    out_dir.mkdir()
    updater._extract_archive(sample_tar, out_dir)

    assert (out_dir / "hello.txt").exists()
    assert (out_dir / "hello.txt").read_text() == "world"


def test_safe_remote_keys_blocks_credentials(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
agent:
  customer_rnc: "101010101"
api:
  base_url: "https://api.tekservices.com.do"
  api_key: "local_secret_key"
database:
  driver: "mysql"
  host: "127.0.0.1"
  password: "local_db_password"
  query: "SELECT 1"
logging:
  file: "./logs/ecf-agent.log"
""")
    config = Config(str(config_file))

    resources = {
        "config": {
            "content": json.dumps({
                "agent": {"batch_size": 75, "auto_update": True, "customer_rnc": "999999999"},
                "api": {"compression": "gzip", "api_key": "hacked_key", "base_url": "https://evil.com"},
                "logging": {"level": "DEBUG", "file": "/etc/passwd"},
            })
        },
        "queries": {
            "content": json.dumps({
                "query": "SELECT * FROM nuevas_facturas LIMIT {batch_size}",
                "status_pending_query": "SELECT encf FROM nuevas_facturas",
                "host": "evil.db.host",
                "password": "hacked",
            })
        },
    }

    config.apply_remote_resources(resources, version="v2.0")
    assert config.get("agent.batch_size") == 75
    assert config.get("agent.auto_update") is True
    assert config.get("agent.customer_rnc") == "101010101"
    assert config.api["api_key"] == "local_secret_key"
    assert config.api["base_url"] == "https://api.tekservices.com.do"
    assert config.get("logging.level") == "DEBUG"
    assert config.get("logging.file") == "./logs/ecf-agent.log"
    assert config.get("database.query") == "SELECT * FROM nuevas_facturas LIMIT {batch_size}"
    assert config.get("database.pending_status_query") == "SELECT encf FROM nuevas_facturas"
    assert config.get("database.host") == "127.0.0.1"
    assert config.get("database.password") == "local_db_password"


def test_command_executor_preview_extraction():
    agent = MagicMock()
    agent._extract_and_prepare_batch.return_value = (
        [
            {
                "ecf": "E320000000025",
                "ecf_type": "32",
                "rnc_buyer": "101010101",
                "total_amount": "1500.00",
                "invoice_data": {
                    "encf": "E320000000025",
                    "Detalles": [{"NombreItem": "Servicio", "MontoItem": "1500.00"}],
                    "FormasPago": [{"FormaPago": 1, "MontoPago": "1500.00"}],
                },
            }
        ],
        [42],
        {"E320000000025": "42"},
    )
    executor = CommandExecutor(agent)
    res = executor.execute("PREVIEW_EXTRACTION", {"limit": 1})

    assert res["success"] is True
    assert res["invoices_count"] == 1
    assert len(res["preview_invoices"]) == 1
    first = res["preview_invoices"][0]
    assert first["id"] == "42"
    assert first["ecf"] == "E320000000025"
    assert first["subquery_counts"]["details"] == 1
    assert first["subquery_counts"]["payments"] == 1


def test_apply_remote_resources_handles_unquoted_mapping_arrays(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
agent:
  customer_rnc: "101010101"
api:
  base_url: "https://api.tekservices.com.do"
  api_key: "secret"
database:
  driver: "mysql"
  host: "127.0.0.1"
  database: "erp"
""",
        encoding="utf-8",
    )
    config = Config(str(cfg_file))

    resources = {
        "mapping": {
            "content": '{\n  "id_field": "id",\n  "ecf_field": "ecf_number",\n  "xml_non_convertible_fields": [MontoGravadoTotal,MontoGravadoI3,MontoExento]\n}'
        }
    }

    changed = config.apply_remote_resources(resources, version="v3.0", save_cache=False)
    assert changed is True
    assert config.get("database.id_field") == "id"
    assert config.get("normalize.xml_non_convertible_fields") == [
        "MontoGravadoTotal",
        "MontoGravadoI3",
        "MontoExento",
    ]

