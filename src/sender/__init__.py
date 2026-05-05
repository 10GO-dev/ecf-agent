"""
Módulo de envío de datos a la API.
"""

from .api_client import ECFApiClient, APIError
from .compressor import compress_data, decompress_data

__all__ = ["ECFApiClient", "APIError", "compress_data", "decompress_data"]
