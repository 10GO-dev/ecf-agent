"""
Utilidades de compresión para datos de facturas.
Soporta dos métodos:
- gzip: JSON + gzip + base64 (compatible, estándar)
- zstd: MessagePack + zstd + base64 (optimizado, rápido) [DEFAULT]
"""

import base64
import gzip
import json
from typing import Any, Dict, Literal, Union

from loguru import logger

# Tipos de compresión soportados
CompressionMethod = Literal["gzip", "zstd", "none"]

# Flags para verificar disponibilidad de librerías
_MSGPACK_AVAILABLE = False
_ZSTD_AVAILABLE = False

try:
    import msgpack
    _MSGPACK_AVAILABLE = True
except ImportError:
    pass

try:
    import zstandard as zstd
    _ZSTD_AVAILABLE = True
except ImportError:
    pass


def is_zstd_available() -> bool:
    """Verifica si el método zstd está disponible."""
    return _MSGPACK_AVAILABLE and _ZSTD_AVAILABLE


def compress_data(
    data: Union[Dict[str, Any], str],
    method: CompressionMethod = "zstd"
) -> str:
    """
    Comprime datos usando el método especificado.
    
    Args:
        data: Diccionario o string a comprimir
        method: Método de compresión ("gzip", "zstd", "none")
    
    Returns:
        String base64 con los datos comprimidos
    
    Example:
        >>> compressed = compress_data({"key": "value"}, method="zstd")
        >>> print(compressed)
        'KLUv/QBY...'
    """
    if method == "none":
        # Sin compresión, solo JSON
        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return data
    
    if method == "zstd":
        if not is_zstd_available():
            logger.warning("zstd no disponible, usando gzip como fallback")
            return _compress_gzip(data)
        return _compress_zstd(data)
    
    # gzip
    return _compress_gzip(data)


def decompress_data(
    compressed: str,
    method: CompressionMethod = "zstd"
) -> Dict[str, Any]:
    """
    Descomprime datos usando el método especificado.
    
    Args:
        compressed: String base64 con datos comprimidos
        method: Método de compresión usado
    
    Returns:
        Diccionario con los datos descomprimidos
    
    Raises:
        ValueError: Si los datos no se pueden descomprimir
    """
    if method == "none":
        return json.loads(compressed)
    
    if method == "zstd":
        if not is_zstd_available():
            raise ValueError("zstd no disponible. Instalar: pip install msgpack zstandard")
        return _decompress_zstd(compressed)
    
    return _decompress_gzip(compressed)


# =============== GZIP (JSON + gzip + base64) ===============

def _compress_gzip(data: Union[Dict[str, Any], str]) -> str:
    """Comprime usando JSON + gzip + base64."""
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
    final_size = len(encoded)
    
    # Log de compresión
    ratio = (1 - final_size / original_size) * 100 if original_size > 0 else 0
    logger.debug(f"Compresión gzip: {original_size} -> {final_size} bytes ({ratio:.1f}% reducción)")
    
    return encoded


def _decompress_gzip(compressed: str) -> Dict[str, Any]:
    """Descomprime datos gzip + base64."""
    try:
        # Decodificar base64
        compressed_bytes = base64.b64decode(compressed)
        
        # Descomprimir gzip
        decompressed = gzip.decompress(compressed_bytes)
        
        # Parsear JSON
        return json.loads(decompressed.decode("utf-8"))
        
    except Exception as e:
        raise ValueError(f"Error descomprimiendo gzip: {e}")


# =============== ZSTD (MessagePack + zstd + base64) ===============

def _compress_zstd(data: Union[Dict[str, Any], str]) -> str:
    """Comprime usando MessagePack + zstd + base64."""
    # Convertir string a dict si es necesario
    if isinstance(data, str):
        data = json.loads(data)
    
    # Serializar con MessagePack (más compacto que JSON)
    packed = msgpack.packb(data, use_bin_type=True)
    original_size = len(packed)
    
    # Comprimir con zstd (más rápido y eficiente que gzip)
    compressor = zstd.ZstdCompressor(level=3)  # Nivel 3 = buen balance velocidad/ratio
    compressed = compressor.compress(packed)
    compressed_size = len(compressed)
    
    # Codificar en base64
    encoded = base64.b64encode(compressed).decode("ascii")
    final_size = len(encoded)
    
    # Log de compresión
    ratio = (1 - final_size / original_size) * 100 if original_size > 0 else 0
    logger.debug(f"Compresión zstd: {original_size} -> {final_size} bytes ({ratio:.1f}% reducción)")
    
    return encoded


def _decompress_zstd(compressed: str) -> Dict[str, Any]:
    """Descomprime datos zstd + msgpack + base64."""
    try:
        # Decodificar base64
        compressed_bytes = base64.b64decode(compressed)
        
        # Descomprimir zstd
        decompressor = zstd.ZstdDecompressor()
        decompressed = decompressor.decompress(compressed_bytes)
        
        # Deserializar MessagePack
        return msgpack.unpackb(decompressed, raw=False)
        
    except Exception as e:
        raise ValueError(f"Error descomprimiendo zstd: {e}")


# =============== Utilidades ===============

def estimate_compression_ratio(
    data: Dict[str, Any],
    method: CompressionMethod = "zstd"
) -> float:
    """
    Estima el ratio de compresión para un conjunto de datos.
    
    Args:
        data: Datos a estimar
        method: Método de compresión
    
    Returns:
        Ratio de compresión (0.0 a 1.0, mayor es mejor)
    """
    if method == "zstd" and is_zstd_available():
        packed = msgpack.packb(data, use_bin_type=True)
        compressor = zstd.ZstdCompressor(level=3)
        compressed = compressor.compress(packed)
        return 1 - (len(compressed) / len(packed))
    
    # Default: gzip
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(json_bytes, compresslevel=9)
    
    return 1 - (len(compressed) / len(json_bytes))


def get_compression_info() -> Dict[str, Any]:
    """Retorna información sobre métodos de compresión disponibles."""
    return {
        "methods": {
            "zstd": {
                "available": is_zstd_available(),
                "description": "MessagePack + zstd + base64 (optimizado, rápido)",
                "default": True,
            },
            "gzip": {
                "available": True,
                "description": "JSON + gzip + base64 (compatible, estándar)",
                "default": False,
            },
            "none": {
                "available": True,
                "description": "Sin compresión (JSON directo)",
                "default": False,
            },
        },
        "default": "zstd",
        "zstd_available": is_zstd_available(),
    }
