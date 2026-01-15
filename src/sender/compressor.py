"""
Utilidades de compresión para datos de facturas.
Usa gzip + base64 para transmisión eficiente.
"""

import base64
import gzip
import json
from typing import Any, Dict, Union

from loguru import logger


def compress_data(data: Union[Dict[str, Any], str]) -> str:
    """
    Comprime datos usando gzip y los codifica en base64.
    
    Args:
        data: Diccionario o string a comprimir
    
    Returns:
        String base64 con los datos comprimidos
    
    Example:
        >>> compressed = compress_data({"key": "value"})
        >>> print(compressed)
        'H4sIAAAAAAAA...'
    """
    # Convertir a JSON si es diccionario
    if isinstance(data, dict):
        json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        json_str = data
    
    # Convertir a bytes
    json_bytes = json_str.encode("utf-8")
    original_size = len(json_bytes)
    
    # Comprimir con gzip
    compressed = gzip.compress(json_bytes, compresslevel=9)
    compressed_size = len(compressed)
    
    # Codificar en base64
    encoded = base64.b64encode(compressed).decode("ascii")
    
    # Log de compresión
    ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
    logger.debug(
        f"Compresión: {original_size} -> {compressed_size} bytes ({ratio:.1f}% reducción)"
    )
    
    return encoded


def decompress_data(compressed: str) -> Dict[str, Any]:
    """
    Descomprime datos codificados en base64 + gzip.
    
    Args:
        compressed: String base64 con datos comprimidos
    
    Returns:
        Diccionario con los datos descomprimidos
    
    Raises:
        ValueError: Si los datos no se pueden descomprimir
    """
    try:
        # Decodificar base64
        compressed_bytes = base64.b64decode(compressed)
        
        # Descomprimir gzip
        decompressed = gzip.decompress(compressed_bytes)
        
        # Parsear JSON
        return json.loads(decompressed.decode("utf-8"))
        
    except Exception as e:
        raise ValueError(f"Error descomprimiendo datos: {e}")


def estimate_compression_ratio(data: Dict[str, Any]) -> float:
    """
    Estima el ratio de compresión para un conjunto de datos.
    
    Args:
        data: Datos a estimar
    
    Returns:
        Ratio de compresión (0.0 a 1.0, mayor es mejor)
    """
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(json_bytes, compresslevel=9)
    
    return 1 - (len(compressed) / len(json_bytes))
