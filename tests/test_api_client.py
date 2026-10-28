from unittest.mock import MagicMock, patch
import pytest
from src.sender.api_client import ECFApiClient, APIError


def test_send_batch_accepts_retry_pending():
    client = ECFApiClient({"base_url": "https://api.test", "api_key": "test-key"})
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "success",
        "results": [
            {
                "ecf": "E310000000001",
                "status": "retry_pending",
                "dgii_status": "not_sent"
            }
        ]
    }

    with patch.object(client, "_get_client") as mock_get_client:
        mock_http = MagicMock()
        mock_http.post.return_value = mock_response
        mock_get_client.return_value = mock_http

        result = client.send_batch("101010101", [{"ecf": "E310000000001", "invoice_data": {}}], compress=False)
        assert result["status"] == "success"
        assert result["results"][0]["status"] == "retry_pending"


def test_send_batch_rejects_unknown_failure_status():
    client = ECFApiClient({"base_url": "https://api.test", "api_key": "test-key"})
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "success",
        "results": [
            {
                "ecf": "E310000000001",
                "status": "unrecognized_error",
                "dgii_status": "error"
            }
        ]
    }

    with patch.object(client, "_get_client") as mock_get_client:
        mock_http = MagicMock()
        mock_http.post.return_value = mock_response
        mock_get_client.return_value = mock_http

        with pytest.raises(APIError):
            client.send_batch("101010101", [{"ecf": "E310000000001", "invoice_data": {}}], compress=False)
