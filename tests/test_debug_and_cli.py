from unittest.mock import MagicMock, patch
from click.testing import CliRunner
from loguru import logger
from src.config import Config
from src.main import ECFAgent, cli


def _create_mock_config(extra=None):
    cfg = Config()
    cfg._config = {
        "agent": {"customer_rnc": "101010101", "batch_size": 5},
        "api": {"base_url": "https://api.example.com", "endpoint": "/private/ecf/invoices", "api_key": "test_key"},
        "database": {"driver": "sqlite", "database": ":memory:", "query": "SELECT 1"},
        "logging": {"console": True, "level": "INFO"},
    }
    if extra:
        cfg._config.update(extra)
    return cfg


def test_setup_logging_respects_debug_level():
    cfg = _create_mock_config()
    agent = ECFAgent(cfg)
    agent.setup_logging(log_level="DEBUG")

    handlers = [h for h in logger._core.handlers.values()]
    assert any(h._levelno == 10 for h in handlers)


def test_debug_invoices_dry_run_does_not_modify_db(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
agent:
  customer_rnc: "101010101"
api:
  base_url: "https://api.example.com"
  endpoint: "/private/ecf/invoices"
  api_key: "test_key"
database:
  driver: "sqlite"
  database: ":memory:"
  query: "SELECT 1"
  id_field: "id"
  ecf_field: "ecf"
  type_field: "ecf_type"
  rnc_buyer_field: "rnc_buyer"
  total_field: "total"
""")
    cfg = Config(str(config_file))
    agent = ECFAgent(cfg)

    agent.db_connector = MagicMock()
    agent.db_connector.__enter__.return_value = agent.db_connector
    agent.api_client = MagicMock()

    agent._extract_and_prepare_batch = MagicMock(return_value=(
        [{
            "rnc_buyer": "131313131",
            "ecf": "E310000000001",
            "ecf_type": "31",
            "total_amount": "100.00",
            "invoice_data": {"test": 123}
        }],
        ["inv_1"],
        {"E310000000001": "inv_1"}
    ))

    result = agent.debug_invoices(limit=1, send=False)

    assert len(result) == 1
    agent.api_client.send_batch.assert_not_called()
    agent.db_connector.mark_as_processed.assert_not_called()


def test_debug_invoices_send_flag_sends_and_marks_processed(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
agent:
  customer_rnc: "101010101"
api:
  base_url: "https://api.example.com"
  endpoint: "/private/ecf/invoices"
  api_key: "test_key"
database:
  driver: "sqlite"
  database: ":memory:"
  query: "SELECT 1"
  id_field: "id"
  ecf_field: "ecf"
  type_field: "ecf_type"
  rnc_buyer_field: "rnc_buyer"
  total_field: "total"
""")
    cfg = Config(str(config_file))
    agent = ECFAgent(cfg)

    agent.db_connector = MagicMock()
    agent.db_connector.__enter__.return_value = agent.db_connector
    agent.api_client = MagicMock()
    agent.api_client.send_batch.return_value = {"results": []}

    agent._extract_and_prepare_batch = MagicMock(return_value=(
        [{
            "rnc_buyer": "131313131",
            "ecf": "E310000000001",
            "ecf_type": "31",
            "total_amount": "100.00",
            "invoice_data": {"test": 123}
        }],
        ["inv_1"],
        {"E310000000001": "inv_1"}
    ))

    result = agent.debug_invoices(limit=1, send=True)

    assert len(result) == 1
    agent.api_client.send_batch.assert_called_once()
    agent.db_connector.mark_as_processed.assert_called_once_with("inv_1")


def test_run_once_dry_run_delegates_to_debug_invoices():
    cfg = _create_mock_config()
    agent = ECFAgent(cfg)
    agent.debug_invoices = MagicMock()
    agent.poll_invoices = MagicMock()
    agent.retry_invoices = MagicMock()

    agent.run_once(dry_run=True)

    agent.debug_invoices.assert_called_once_with(limit=5, send=False)
    agent.poll_invoices.assert_not_called()
    agent.retry_invoices.assert_not_called()


def test_cli_debug_command():
    runner = CliRunner()
    with patch("src.main.load_config") as mock_load, patch("src.main.ECFAgent") as mock_agent_cls:
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        mock_agent = MagicMock()
        mock_agent_cls.return_value = mock_agent

        result = runner.invoke(cli, ["debug", "--limit", "3"])
        assert result.exit_code == 0
        mock_agent.debug_invoices.assert_called_once_with(limit=3, send=False)


def test_cli_once_dry_run_command():
    runner = CliRunner()
    with patch("src.main.load_config") as mock_load, patch("src.main.ECFAgent") as mock_agent_cls:
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg
        mock_agent = MagicMock()
        mock_agent_cls.return_value = mock_agent

        result = runner.invoke(cli, ["once", "--debug", "--dry-run"])
        assert result.exit_code == 0
        mock_agent.run_once.assert_called_once_with(debug=True, dry_run=True)


def test_cli_check_config_alias():
    runner = CliRunner()
    with patch("src.main.load_config") as mock_load, patch("src.main.create_connector") as mock_conn:
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda k, d=None: {
            "agent.customer_rnc": "101010101",
            "database.driver": "sqlite",
            "database.host": "localhost",
            "api.base_url": "https://api.tekservices.com.do"
        }.get(k, d)
        mock_load.return_value = mock_cfg
        
        connector_instance = MagicMock()
        connector_instance.test_connection.return_value = True
        mock_conn.return_value = connector_instance

        result = runner.invoke(cli, ["check-config"])
        assert result.exit_code == 0
        assert "Configuración válida" in result.output
        assert "Conexión a BD exitosa" in result.output
