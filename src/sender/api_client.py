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
        self.environment = config.get("environment", "DEV")
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
                    "x-environment": self.environment,
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
            
            # Interceptar y asimilar DUPLICATE_INVOICE como éxito
            if isinstance(response_data, dict):
                errors = response_data.get("errors", [])
                if isinstance(errors, list) and errors:
                    for i in range(len(errors) - 1, -1, -1):
                        err = errors[i]
                        if isinstance(err, dict) and err.get("error") == "DUPLICATE_INVOICE":
                            ecf = err.get("ecf")
                            logger.info(f"Interceptado DUPLICATE_INVOICE para {ecf}. Asimilando como éxito.")
                            if "results" not in response_data:
                                response_data["results"] = []
                            response_data["results"].append({
                                "ecf": ecf,
                                "status": "success",
                                "dgii_status": "pending" 
                            })
                            errors.pop(i)
                            
                            if isinstance(response_data.get("invoices_failed"), int):
                                response_data["invoices_failed"] = max(0, response_data["invoices_failed"] - 1)
                            if isinstance(response_data.get("invoices_processed"), int):
                                response_data["invoices_processed"] += 1
                                
                    if not errors and response.status_code == 422:
                        response.status_code = 200
                        response_data.pop("error", None)
                        response_data.pop("message", None)

            # Verificar condiciones de error incluso si HTTP 200
            is_error = False
            error_reason = None

            def extract_error_detail(data: Any) -> Optional[str]:
                if not isinstance(data, dict):
                    return None

                code = data.get("error") or data.get("code")
                message = data.get("message")
                if code or message:
                    return f"{code}: {message}" if code else str(message)

                errors = data.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        first_code = first.get("error") or first.get("code")
                        first_message = first.get("message")
                        if first_code or first_message:
                            return f"{first_code}: {first_message}" if first_code else str(first_message)

                return None

            if response.status_code != 200:
                is_error = True
                error_detail = extract_error_detail(response_data)
                error_reason = f"HTTP {response.status_code}"
                if error_detail:
                    error_reason = f"{error_reason} - {error_detail}"
            else:
                if isinstance(response_data, dict):
                    sc = response_data.get("statusCode") or response_data.get("status")
                    if isinstance(sc, int) and sc != 200:
                        is_error = True
                        error_reason = f"statusCode in body: {sc}"

                    invoices_failed = response_data.get("invoices_failed")
                    if isinstance(invoices_failed, int) and invoices_failed > 0:
                        is_error = True
                        error_reason = f"invoices_failed={invoices_failed}"

                    if response_data.get("error") or response_data.get("errors"):
                        is_error = True
                        if not error_reason:
                            error_reason = "error field in response body"

                    message = response_data.get("message")
                    if isinstance(message, str) and "error" in message.lower():
                        is_error = True
                        if not error_reason:
                            error_reason = "message indicates error"

                    results = response_data.get("results") or response_data.get("invoices") or response_data.get("invoice_results")
                    if isinstance(results, list):
                        for r in results:
                            if isinstance(r, dict):
                                status = r.get("status")
                                if isinstance(status, str) and status.lower() not in ("ok", "success", "processed", "200"):
                                    is_error = True
                                    error_reason = f"item status: {status}"
                                    break
                                if r.get("error") or r.get("errors"):
                                    is_error = True
                                    error_reason = "item error"
                                    break

            if is_error:
                logger.error(f"API returned error-like response: {response.status_code} - {response_data} (reason: {error_reason})")
                raise APIError(
                    f"Error {response.status_code}: {response_data}",
                    status_code=response.status_code,
                    response=response_data,
                )

            logger.info(f"Batch enviado exitosamente")
            return response_data
                
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

    def sync_status(self, customer_rnc: str, ecf_numbers: List[str]) -> Dict[str, Any]:
        """
        Consulta el estado de uno o más ECFs al backend.
        
        Args:
            customer_rnc: RNC del cliente (emisor)
            ecf_numbers: Lista de números de comprobantes (e-CFs)
        
        Returns:
            Respuesta de la API con los estados actuales
        """
        if not ecf_numbers:
            return {"ok": True, "results": []}

        payload = {
            "rnc_customer": customer_rnc,
            "ecfs": ecf_numbers,
        }
        
        # Construir el endpoint de status-sync desde la base URL directamente
        # para no depender de cómo esté configurado self.endpoint
        status_endpoint = "/private/ecf/status-sync"
        
        logger.info(f"Consultando estado de {len(ecf_numbers)} ECFs...")
        
        try:
            client = self._get_client()
            response = client.post(status_endpoint, json=payload)
            
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw": response.text}
            
            if response.status_code == 200:
                logger.info("Consulta de estados exitosa")
                return response_data
            else:
                logger.error(f"Error en API al consultar estados: {response.status_code} - {response_data}")
                raise APIError(
                    f"Error {response.status_code}: {response_data}",
                    status_code=response.status_code,
                    response=response_data,
                )
        except httpx.TimeoutException:
            logger.error(f"Timeout al consultar estados ({self.timeout}s)")
            raise APIError(f"Timeout al consultar estados")
        except httpx.RequestError as e:
            logger.error(f"Error de conexión al consultar estados: {e}")
            raise APIError(f"Error de conexión al consultar estados: {e}")

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
