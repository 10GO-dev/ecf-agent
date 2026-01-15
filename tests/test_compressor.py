"""Tests para el módulo de compresión."""

import json

import pytest

from src.sender.compressor import compress_data, decompress_data, estimate_compression_ratio


class TestCompressor:
    """Tests para funciones de compresión."""

    def test_compress_decompress_roundtrip(self):
        """Test que comprimir y descomprimir retorna los datos originales."""
        original_data = {
            "ECF": {
                "encf": "E310000000001",
                "RNCEmisor": "101010101",
                "MontoTotal": "1500.00",
            },
            "Detalles": [
                {"linea": 1, "NombreItem": "Producto A", "MontoItem": "1000.00"},
                {"linea": 2, "NombreItem": "Producto B", "MontoItem": "500.00"},
            ],
            "FormasPago": [
                {"formapago": "1", "montopago": 1500.00}
            ]
        }
        
        compressed = compress_data(original_data)
        decompressed = decompress_data(compressed)
        
        assert decompressed == original_data

    def test_compress_string_input(self):
        """Test compresión con string como entrada."""
        original_str = '{"key": "value", "number": 123}'
        
        compressed = compress_data(original_str)
        decompressed = decompress_data(compressed)
        
        assert decompressed == json.loads(original_str)

    def test_compression_reduces_size(self):
        """Test que la compresión reduce el tamaño."""
        large_data = {
            "items": [
                {"id": i, "description": f"Item description {i}" * 10}
                for i in range(100)
            ]
        }
        
        original_size = len(json.dumps(large_data).encode("utf-8"))
        compressed = compress_data(large_data)
        compressed_size = len(compressed.encode("utf-8"))
        
        # La compresión debería reducir significativamente
        assert compressed_size < original_size * 0.5

    def test_estimate_compression_ratio(self):
        """Test estimación del ratio de compresión."""
        data = {"repeated": "AAAAAAAAAA" * 1000}
        
        ratio = estimate_compression_ratio(data)
        
        # Datos muy repetitivos deberían comprimir muy bien
        assert ratio > 0.9

    def test_decompress_invalid_data(self):
        """Test error al descomprimir datos inválidos."""
        with pytest.raises(ValueError):
            decompress_data("invalid_base64_data!")
