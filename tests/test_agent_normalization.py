"""Tests para la normalización opcional de datos del agente."""

from src.main import normalize_invoice_data_for_xml


class TestAgentNormalization:
    def test_nullifies_nullish_values_when_no_fields_are_protected(self):
        data = {
            "MontoTotal": "0.00",
            "MontoExento": 0,
            "Texto": "none",
            "Detalles": [{"MontoItem": "0.00", "Descripcion": "Producto"}],
        }

        result = normalize_invoice_data_for_xml(data, set())

        assert result["MontoTotal"] is None
        assert result["MontoExento"] is None
        assert result["Texto"] is None
        assert result["Detalles"][0]["MontoItem"] is None
        assert result["Detalles"][0]["Descripcion"] == "Producto"

    def test_preserves_non_convertible_fields(self):
        data = {
            "MontoTotal": "0.00",
            "MontoExento": 0,
            "Texto": "none",
            "OtroCampo": "0.00",
            "Detalles": [{"MontoItem": "0.00", "Descripcion": "Producto"}],
        }

        result = normalize_invoice_data_for_xml(data, {"MontoTotal", "MontoExento", "MontoItem"})

        assert result["MontoTotal"] == "0.00"
        assert result["MontoExento"] == 0
        assert result["Texto"] is None
        assert result["OtroCampo"] is None
        assert result["Detalles"][0]["MontoItem"] == "0.00"
        assert result["Detalles"][0]["Descripcion"] == "Producto"