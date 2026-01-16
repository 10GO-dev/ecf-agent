"""
Cliente HTTP para envío de comprobantes a la API de TekServices.
Soporta compresión gzip y zstd (default) con base64.
"""

import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from .compressor import compress_data


class APIError(Exception):
    """Error de comunicación con la API."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ECFApiClient:
    """
    Cliente para enviar comprobantes fiscales a la API de TekServices.
    
    Soporta autenticación por API Key (header x-api-key) y compresión de datos.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Inicializa el cliente API.
        
        Args:
            config: Configuración de la API (base_url, endpoint, api_key, timeout)
        """
        self.base_url = config.get("base_url", "").rstrip("/")
        self.endpoint = config.get("endpoint", "/private/ecf/dgii-send")
        self.api_key = config.get("api_key")
        self.timeout = config.get("timeout_seconds", 30)
        
        # Validar configuración
        if not self.base_url:
            raise ValueError("base_url es requerido")
        if not self.api_key:
            raise ValueError("api_key es requerido para autenticación")
        
        # Cliente HTTP
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Obtiene o crea el cliente HTTP."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "ECF-Agent/1.0",
                    "x-api-key": self.api_key,
                },
            )
        return self._client

    def close(self):
        """Cierra el cliente HTTP."""
        if self._client:
            self._client.close()
            self._client = None

    def send_batch(
        self,
        customer_rnc: str,
        invoices: List[Dict[str, Any]],
        compress: bool = True,
        compression_method: str = "zstd",
    ) -> Dict[str, Any]:
        """
        Envía un batch de facturas a la API.
        
        Args:
            customer_rnc: RNC del cliente (emisor)
            invoices: Lista de facturas con estructura:
                      {rnc_buyer, ecf, total_amount, invoice_data}
            compress: Si comprimir los datos de cada factura
            compression_method: Método de compresión ("gzip", "zstd", "none")
        
        Returns:
            Respuesta de la API
        
        Raises:
            APIError: Si hay error en el envío
        """
        # Determinar método efectivo
        effective_method = compression_method if compress else "none"
        
        # Construir payload según el formato del endpoint
        payload = {
            "rnc_customer": customer_rnc,
            "compression": effective_method,
            "invoices": [],
        }
        
        for invoice in invoices:
            invoice_data = invoice.get("invoice_data", {})
            
            invoice_payload = {
                "rnc_buyer": str(invoice.get("rnc_buyer", "")),
                "ecf": str(invoice.get("ecf", "")),
                "total_amount": str(invoice.get("total_amount", "0.00")),
                "compression": effective_method,
            }
            
            # Comprimir usando el método especificado
            invoice_payload["invoice_data"] = compress_data(invoice_data, method=effective_method)
            
            payload["invoices"].append(invoice_payload)
        
        logger.info(f"Enviando {len(invoices)} facturas para RNC {customer_rnc} (compresión: {effective_method})")
        logger.debug(f"URL: {self.base_url}{self.endpoint}")
        
        try:
            client = self._get_client()
            response = client.post(self.endpoint, json=payload)
            
            # Parsear respuesta
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw": response.text}
            
            if response.status_code == 200:
                logger.info(f"Batch enviado exitosamente")
                return response_data
            else:
                logger.error(f"Error en API: {response.status_code} - {response_data}")
                raise APIError(
                    f"Error {response.status_code}: {response_data}",
                    status_code=response.status_code,
                    response=response_data,
                )
                
        except httpx.TimeoutException:
            logger.error(f"Timeout al enviar batch ({self.timeout}s)")
            raise APIError(f"Timeout después de {self.timeout} segundos")
        except httpx.RequestError as e:
            logger.error(f"Error de conexión: {e}")
            raise APIError(f"Error de conexión: {e}")

    def send_single(
        self,
        customer_rnc: str,
        invoice: Dict[str, Any],
        compress: bool = True,
        compression_method: str = "zstd",
    ) -> Dict[str, Any]:
        """
        Envía una sola factura a la API.
        
        Args:
            customer_rnc: RNC del cliente (emisor)
            invoice: Factura con estructura {rnc_buyer, ecf, total_amount, invoice_data}
            compress: Si comprimir los datos
            compression_method: Método de compresión
        
        Returns:
            Respuesta de la API
        """
        return self.send_batch(customer_rnc, [invoice], compress, compression_method)

    def test_connection(self) -> bool:
        """
        Prueba la conexión a la API.
        
        Returns:
            True si la conexión es exitosa
        """
        try:
            client = self._get_client()
            response = client.get("/health")
            logger.info(f"Test de conexión API: {response.status_code}")
            return response.status_code in (200, 404)
        except Exception as e:
            logger.error(f"Error en test de conexión API: {e}")
            return False

    def __enter__(self):
        """Context manager: abrir cliente."""
        self._get_client()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: cerrar cliente."""
        self.close()
        return False
