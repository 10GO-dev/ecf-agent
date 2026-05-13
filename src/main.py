"""
Entry point principal del Agente ECF.
Versión simplificada: solo extrae datos de BD y los envía comprimidos.
"""

import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import datetime
from decimal import Decimal

import click
from loguru import logger

from . import __version__
from .config import Config, ConfigError, load_config
from .database import create_connector
from .queue import RetryQueue
from .scheduler import JobManager
from .sender import ECFApiClient, APIError
from .updater import AutoUpdater


def sanitize_for_serialization(obj: Any) -> Any:
    """Recursively converts Decimals and DateTimes to base types to allow JSON/MsgPack serialization."""
    if isinstance(obj, dict):
        return {k: sanitize_for_serialization(v) for k, v in obj.items()}
    elif isinstance(obj, list) or isinstance(obj, tuple):
        return [sanitize_for_serialization(v) for v in obj]
    elif isinstance(obj, Decimal):
        # Es vital usar str() en lugar de float() para dinero, para no perder precisión
        return str(obj)
    elif isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


NULLISH_STRINGS = {"", "nl", "nll", "null", "none"}


def _is_zero_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, Decimal):
        return value == 0
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"0", "0.0", "0.00"}
    return False


def _should_nullify(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NULLISH_STRINGS or _is_zero_like(value)
    return _is_zero_like(value)


def normalize_invoice_data_for_xml(
    obj: Any,
    fields_not_convertible: set[str],
) -> Any:
    """Convierte valores nullish a None, salvo en campos marcados como no convertibles."""
    if isinstance(obj, dict):
        result: Dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                result[key] = normalize_invoice_data_for_xml(value, fields_not_convertible)
                continue

            if key in fields_not_convertible:
                result[key] = value
            elif _should_nullify(value):
                result[key] = None
            else:
                result[key] = value
        return result

    if isinstance(obj, list):
        return [normalize_invoice_data_for_xml(item, fields_not_convertible) for item in obj]

    return obj


class ECFAgent:
    """
    Agente de recolección de comprobantes fiscales.
    
    Flujo simplificado:
    1. Ejecuta query SQL configurada → obtiene JSON de facturas
    2. Comprime cada factura
    3. Envía al servidor en batches
    """

    def __init__(self, config: Config):
        """
        Inicializa el agente.
        
        Args:
            config: Configuración cargada
        """
        self.config = config
        self.running = False
        
        # Componentes
        self.db_connector = create_connector(config.database)
        self.api_client = ECFApiClient(config.api)
        self.retry_queue = RetryQueue(
            db_path=str(Path(config.get("queue.db_path", "./data/queue.db")))
        )
        self.job_manager = JobManager()
        self.updater = AutoUpdater(config.agent)
        
        # Configuración del agente
        self.customer_rnc = config.get("agent.customer_rnc")
        self.batch_size = config.get("agent.batch_size", 50)
        self.max_retries = config.get("agent.max_retries", 5)
        self.compression_method = config.get("api.compression", "zstd")

        # Validacion de errores no reintentables
        default_validation_codes = {
            "INVALID_ECF_TYPE",
            "MISSING_JSONATA",
            "MISSING_CERTIFICATE",
            "NO_BUYER_RNC",
            "INVALID_ECF_FORMAT",
            "INVALID_FORMAT",
            "INVALID_COMPRESSION",
            "INVALID_PAYLOAD",
            "FORBIDDEN",
            "UNAUTHORIZED",
        }
        extra_validation_codes = config.get("agent.validation_error_codes", [])
        if not isinstance(extra_validation_codes, list):
            extra_validation_codes = []
        self.validation_error_codes = {
            str(code).upper() for code in default_validation_codes
        }
        self.validation_error_codes.update(
            str(code).upper() for code in extra_validation_codes
        )
        self.validation_error_status = str(
            config.get("database.validation_error_status", "2")
        )
        
        # Nombres de campos configurables para mapping
        self.id_field = config.get("database.id_field", "id")
        self.ecf_field = config.get("database.ecf_field", "ecf_number")
        self.type_field = config.get("database.type_field", "ecf_type")
        self.rnc_buyer_field = config.get("database.rnc_buyer_field", "rnc_buyer")
        self.total_field = config.get("database.total_field", "total_amount")
        self.xml_non_convertible_fields = {
            str(field).strip()
            for field in config.get("database.xml_non_convertible_fields", [])
            if str(field).strip()
        }

    def setup_logging(self):
        """Configura el sistema de logging."""
        log_config = self.config.logging
        
        # Remover handler por defecto
        logger.remove()
        
        # Consola
        if log_config.get("console", True):
            logger.add(
                sys.stderr,
                level=log_config.get("level", "INFO"),
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
            )
        
        # Archivo
        log_file = log_config.get("file")
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.add(
                str(log_path),
                level=log_config.get("level", "INFO"),
                rotation=f"{log_config.get('max_size_mb', 10)} MB",
                retention=log_config.get("backup_count", 5),
                compression="zip",
            )

    def _is_validation_error(self, error_item: Dict[str, Any]) -> bool:
        code = str(error_item.get("error") or error_item.get("code") or "").strip().upper()
        if code:
            if code in self.validation_error_codes:
                return True
            if code.startswith(("INVALID_", "MISSING_")):
                return True

        message = str(error_item.get("message") or "").lower()
        if "mismatch" in message or "invalid" in message:
            return True

        return False

    def _handle_validation_errors(
        self,
        errors_list: Any,
        ecf_to_id: Dict[str, str],
        remove_from_retry: bool = False,
    ) -> set:
        validation_ecfs = set()

        if not isinstance(errors_list, list):
            return validation_ecfs

        for err in errors_list:
            if not isinstance(err, dict):
                continue

            ecf = err.get("ecf")
            if not ecf:
                continue

            if not self._is_validation_error(err):
                continue

            validation_ecfs.add(ecf)
            invoice_id = ecf_to_id.get(ecf)
            error_message = err.get("message") or err.get("error") or "Validation error"

            updated = self.db_connector.mark_as_failed(
                ecf,
                error_message,
                invoice_id,
                status_override=self.validation_error_status,
            )
            if not updated:
                self.db_connector.update_invoice_status(
                    ecf,
                    self.validation_error_status,
                    error_message=error_message,
                )

            if remove_from_retry and invoice_id:
                self.retry_queue.remove(invoice_id)

        if validation_ecfs:
            logger.warning(
                f"{len(validation_ecfs)} facturas con error de validacion. "
                f"Marcadas con estado {self.validation_error_status} para no reintentar."
            )

        return validation_ecfs

    def poll_invoices(self):
        """
        Trabajo principal: obtiene facturas de la BD y las envía al servidor.
        """
        logger.info("Iniciando polling de facturas...")
        
        try:
            with self.db_connector:
                # Obtener facturas pendientes
                invoices = self.db_connector.get_pending_invoices(self.batch_size)
                
                if not invoices:
                    logger.debug("No hay facturas pendientes")
                    return
                
                # Filtrar facturas que ya están en la cola de reintentos (evitar doble envío)
                filtered_invoices = []
                for row in invoices:
                    invoice_id = str(row.get(self.id_field))
                    if self.retry_queue.exists(invoice_id):
                        continue
                    filtered_invoices.append(row)
                
                invoices = filtered_invoices
                if not invoices:
                    logger.debug("Todas las facturas pendientes están bajo gestión de reintentos")
                    return
                
                logger.info(f"Obtenidas {len(invoices)} facturas pendientes")
                
                # Preparar datos para envío
                invoices_to_send = []
                invoice_ids = []
                
                # Para evitar N+1 queries, primero recopilamos todos los IDs
                for row in invoices:
                    invoice_ids.append(row.get(self.id_field))

                # Mapeo ECF -> ID para actualizaciones puntuales
                ecf_to_id = {}
                for row in invoices:
                    ecf_value = str(row.get(self.ecf_field, ""))
                    if ecf_value:
                        ecf_to_id[ecf_value] = str(row.get(self.id_field))
                
                # Mapeos para guardar resultados agrupados por transaccionid
                grouped_details = {str(id_): [] for id_ in invoice_ids}
                grouped_taxes = {str(id_): [] for id_ in invoice_ids}
                grouped_payments = {str(id_): [] for id_ in invoice_ids}
                
                # Traer subqueries usando cláusula IN
                ids_str = ",".join(str(id_) for id_ in invoice_ids)
                
                details_query = self.config.get("database.details_query")
                if details_query:
                    query = details_query.format(ids=ids_str)
                    logger.debug(f"Ejecutando subquery details: {query}")
                    try:
                        details_res = self.db_connector.execute_query(query)
                        for d in details_res:
                            tid = str(d.get("transaccionid"))
                            if tid in grouped_details:
                                grouped_details[tid].append(d)
                    except Exception as e:
                        logger.error(f"Error en subquery details: {e}")

                taxes_query = self.config.get("database.taxes_query")
                if taxes_query:
                    query = taxes_query.format(ids=ids_str)
                    logger.debug(f"Ejecutando subquery taxes: {query}")
                    try:
                        taxes_res = self.db_connector.execute_query(query)
                        for t in taxes_res:
                            tid = str(t.get("transaccionid"))
                            if tid in grouped_taxes:
                                grouped_taxes[tid].append(t)
                    except Exception as e:
                        logger.error(f"Error en subquery taxes: {e}")

                payments_query = self.config.get("database.payments_query")
                if payments_query:
                    query = payments_query.format(ids=ids_str)
                    logger.debug(f"Ejecutando subquery payments: {query}")
                    try:
                        pay_res = self.db_connector.execute_query(query)
                        for p in pay_res:
                            tid = str(p.get("transaccionid"))
                            if tid in grouped_payments:
                                grouped_payments[tid].append(p)
                    except Exception as e:
                        logger.error(f"Error en subquery payments: {e}")

                for row in invoices:
                    try:
                        row_id = str(row.get(self.id_field))
                        
                        # Ensamblamos el JSON nativamente
                        invoice_data = dict(row)
                        
                        if self.config.get("database.details_query"):
                            invoice_data["Detalles"] = grouped_details.get(row_id, [])
                        
                        if self.config.get("database.taxes_query"):
                            invoice_data["ImpuestosAdicionales"] = grouped_taxes.get(row_id, [])
                            
                        if self.config.get("database.payments_query"):
                            invoice_data["FormasPago"] = grouped_payments.get(row_id, [])

                        invoice_data = normalize_invoice_data_for_xml(
                            invoice_data,
                            self.xml_non_convertible_fields,
                        )
                        
                        # Sanitizar datos (Decimal -> float, datetime -> str) para serialización JSON/MsgPack
                        invoice_data = sanitize_for_serialization(invoice_data)
                        
                        # Preparar estructura para envío
                        invoice_payload = {
                            "rnc_buyer": str(row.get(self.rnc_buyer_field, "")),
                            "ecf": str(row.get(self.ecf_field, "")),
                            "ecf_type": str(row.get(self.type_field, "")),
                            "total_amount": str(row.get(self.total_field, "0.00")),
                            "invoice_data": invoice_data,  # Se comprimirá en el sender
                        }
                        
                        logger.debug(f"Factura procesada: {json.dumps(invoice_data, ensure_ascii=False, indent=2)}")
                        
                        invoices_to_send.append(invoice_payload)
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"Error parseando JSON de factura: {e}")
                        continue
                    except Exception as e:
                        logger.error(f"Error procesando factura: {e}")
                        continue
                
                if not invoices_to_send:
                    logger.warning("No se pudieron procesar facturas")
                    return
                
                # Enviar batch
                try:
                    response = self.api_client.send_batch(
                        self.customer_rnc,
                        invoices_to_send,
                        compress=True,
                        compression_method=self.compression_method,
                    )
                    
                    # Marcar como procesadas
                    for invoice_id in invoice_ids:
                        self.db_connector.mark_as_processed(invoice_id)
                    
                    logger.info(f"Batch enviado exitosamente: {len(invoices_to_send)} facturas")
                    
                    # PROCESAMIENTO SÍNCRONO DE ESTADOS (Hot-Sync)
                    # El backend debe retornar un arreglo "results" con el estado de cada ECF
                    results = response.get("results", [])
                    ecf_to_id = {
                        invoice.get("ecf"): str(invoice_ids[i])
                        for i, invoice in enumerate(invoices_to_send)
                        if invoice.get("ecf")
                    }
                    if results:
                        logger.info(f"Procesando {len(results)} estados recibidos sincrónicamente")
                        for res in results:
                            ecf = res.get("ecf")
                            status = res.get("dgii_status") or res.get("status")
                            track_id = res.get("dgii_track_id", "")
                            error_msg = res.get("dgii_error", "")
                            
                            if ecf and status:
                                mapping = self.config.get("database.status_mapping", {})
                                status_key = str(status).lower()
                                if status_key not in mapping:
                                    if status_key == "error" and error_msg:
                                        invoice_id = ecf_to_id.get(ecf)
                                        self.db_connector.mark_as_failed(ecf, error_msg, invoice_id)
                                    else:
                                        logger.info(
                                            f"Estado no-DGII '{status}' para {ecf}; se omite actualizar procesadadgii"
                                        )
                                    continue
                                local_status = str(mapping.get(status_key))
                                if error_msg:
                                    logger.warning(f"e-CF {ecf} aceptado condicional. Mensajes DGII: {error_msg}")
                                self.db_connector.update_invoice_status(ecf, local_status, track_id)
                    # Fin de procesamiento de batch exitoso
                except APIError as e:
                    logger.error(f"Error de API enviando batch: {e}")

                    response_data = getattr(e, "response", {}) or {}
                    errors_list = (
                        response_data.get("errors", []) if isinstance(response_data, dict) else []
                    )
                    validation_ecfs = self._handle_validation_errors(errors_list, ecf_to_id)

                    failed_ecfs = {err.get("ecf") for err in errors_list if isinstance(err, dict) and "ecf" in err}
                    results_list = (
                        response_data.get("results", []) or response_data.get("invoices", [])
                    ) if isinstance(response_data, dict) else []
                    success_ecfs = {
                        res.get("ecf")
                        for res in results_list
                        if isinstance(res, dict) and res.get("status") in ("ok", "success", "processed", "200")
                    }

                    if validation_ecfs:
                        failed_ecfs = {ecf for ecf in failed_ecfs if ecf not in validation_ecfs}
                    
                    if not failed_ecfs and not success_ecfs:
                        # Fallo completo (ej. Timeout, 500)
                        for i, invoice in enumerate(invoices_to_send):
                            self.retry_queue.add(
                                invoice_id=str(invoice_ids[i]),
                                customer_rnc=self.customer_rnc,
                                payload=invoice,
                                ecf_type=invoice.get("ecf_type"),
                                ecf_number=invoice.get("ecf"),
                                error_message=str(e),
                            )
                        logger.info(f"{len(invoices_to_send)} facturas agregadas a cola de reintentos por fallo general")
                    else:
                        # Fallo parcial
                        added_to_retry = 0
                        for i, invoice in enumerate(invoices_to_send):
                            ecf_num = invoice.get("ecf")
                            invoice_id = str(invoice_ids[i])

                            if ecf_num in validation_ecfs:
                                continue
                            
                            if ecf_num in failed_ecfs or (ecf_num not in success_ecfs):
                                # Asumir fallo si explícitamente falló o no vino en results
                                self.retry_queue.add(
                                    invoice_id=invoice_id,
                                    customer_rnc=self.customer_rnc,
                                    payload=invoice,
                                    ecf_type=invoice.get("ecf_type"),
                                    ecf_number=ecf_num,
                                    error_message="Fallo parcial en batch",
                                )
                                added_to_retry += 1
                            else:
                                # Tuvo éxito a pesar del error general
                                self.db_connector.mark_as_processed(invoice_id)
                        logger.info(f"{added_to_retry} facturas enviadas a cola de reintentos por fallo parcial")
                
                except Exception as e:
                    logger.error(f"Error genérico enviando batch o actualizando estados: {e}")
                    
                    # Agregar a cola de reintentos (Fallo general)
                    for i, invoice in enumerate(invoices_to_send):
                        self.retry_queue.add(
                            invoice_id=str(invoice_ids[i]),
                            customer_rnc=self.customer_rnc,
                            payload=invoice,
                            ecf_type=invoice.get("ecf_type"),
                            ecf_number=invoice.get("ecf"),
                            error_message=str(e),
                        )
                    logger.info(f"{len(invoices_to_send)} facturas agregadas a cola de reintentos")
                
        except Exception as e:
            logger.error(f"Error en polling: {e}")

    def sync_statuses(self):
        """
        Trabajo de recuperación (Fallback Sync): 
        Consulta al backend por el estado de las facturas que quedaron 'Pendientes'.
        """
        logger.debug("Verificando facturas pendientes de estado (Fallback Polling)...")
        
        try:
            with self.db_connector:
                pending_invoices = self.db_connector.get_pending_status_invoices(self.batch_size)
                
                if not pending_invoices:
                    return
                
                # Extraemos solo los números de ECF
                ecf_field_name = self.config.get("database.ecf_field", "encf")
                ecfs_to_sync = [row.get(ecf_field_name) for row in pending_invoices if row.get(ecf_field_name)]
                ecf_to_id = {
                    row.get(ecf_field_name): row.get("id")
                    for row in pending_invoices
                    if row.get(ecf_field_name) and row.get("id")
                }
                
                if not ecfs_to_sync:
                    logger.warning("Query de pendientes no retornó el campo ECF correcto.")
                    return
                
                response = self.api_client.sync_status(self.customer_rnc, ecfs_to_sync)
                
                results = response.get("results", [])
                if results:
                    logger.info(f"Actualizando {len(results)} facturas desde sincronización asíncrona")
                    for res in results:
                        ecf = res.get("ecf")
                        status = res.get("dgii_status") or res.get("status")
                        track_id = res.get("dgii_track_id", "")
                        error_msg = res.get("dgii_error", "")
                        
                        if ecf and status:
                            mapping = self.config.get("database.status_mapping", {})
                            status_key = str(status).lower()
                            if status_key not in mapping:
                                if status_key == "error" and error_msg:
                                    invoice_id = ecf_to_id.get(ecf)
                                    self.db_connector.mark_as_failed(ecf, error_msg, invoice_id)
                                else:
                                    logger.info(
                                        f"Estado no-DGII '{status}' para {ecf}; se omite actualizar procesadadgii"
                                    )
                                continue
                            local_status = str(mapping.get(status_key))
                            if error_msg:
                                logger.warning(f"e-CF {ecf} aceptado condicional. Mensajes DGII: {error_msg}")
                            self.db_connector.update_invoice_status(ecf, local_status, track_id)
                            
        except Exception as e:
            logger.error(f"Error en sincronización de estados (Fallback): {e}")

    def retry_invoices(self):
        """
        Trabajo de reintentos: reenvía facturas fallidas.
        """
        logger.debug("Verificando cola de reintentos...")
        
        pending = self.retry_queue.get_pending(
            max_retries=self.max_retries,
            limit=self.batch_size,
        )
        
        if not pending:
            return
        
        logger.info(f"Reintentando {len(pending)} facturas")
        
        try:
            with self.db_connector:
                for item in pending:
                    try:
                        self.api_client.send_single(
                            item["customer_rnc"],
                            item["payload"],
                            compress=True,
                            compression_method=self.compression_method,
                        )
                        
                        # Éxito: actualizar BD y eliminar de cola
                        self.db_connector.mark_as_processed(item["invoice_id"])
                        self.retry_queue.remove(item["invoice_id"])
                        logger.info(f"Factura {item.get('ecf_number', item['invoice_id'])} (ID: {item['invoice_id']}) reenviada exitosamente y actualizada en ERP")

                    except APIError as e:
                        response_data = getattr(e, "response", {}) or {}
                        errors_list = (
                            response_data.get("errors", []) if isinstance(response_data, dict) else []
                        )
                        ecf_number = item.get("ecf_number") or item["invoice_id"]
                        validation_ecfs = self._handle_validation_errors(
                            errors_list,
                            {ecf_number: item["invoice_id"]},
                            remove_from_retry=True,
                        )
                        if ecf_number in validation_ecfs:
                            logger.warning(
                                f"Factura {ecf_number} marcada como no reintentable por validacion"
                            )
                            continue

                        # Actualizar contador de intentos
                        self.retry_queue.update_attempt(item["invoice_id"], str(e))
                        new_attempts = item["attempts"] + 1
                        logger.warning(
                            f"Reintento fallido para {item.get('ecf_number', item['invoice_id'])} "
                            f"(intento {new_attempts}): {e}"
                        )
                        if new_attempts >= self.max_retries:
                            logger.error(f"Factura {item.get('ecf_number', item['invoice_id'])} superó max_retries ({self.max_retries}). Marcando como fallida en ERP.")
                            self.db_connector.mark_as_failed(
                                item.get("ecf_number") or item["invoice_id"],
                                str(e),
                                item["invoice_id"]
                            )
                            self.retry_queue.remove(item["invoice_id"])

                    except Exception as e:
                        # Actualizar contador de intentos
                        self.retry_queue.update_attempt(item["invoice_id"], str(e))
                        new_attempts = item["attempts"] + 1
                        logger.warning(
                            f"Reintento fallido para {item.get('ecf_number', item['invoice_id'])} "
                            f"(intento {new_attempts}): {e}"
                        )
                        if new_attempts >= self.max_retries:
                            logger.error(f"Factura {item.get('ecf_number', item['invoice_id'])} superó max_retries ({self.max_retries}). Marcando como fallida en ERP.")
                            self.db_connector.mark_as_failed(
                                item.get("ecf_number") or item["invoice_id"],
                                str(e),
                                item["invoice_id"]
                            )
                            self.retry_queue.remove(item["invoice_id"])
        except Exception as e:
            logger.error(f"Error general en hilo de reintentos: {e}")

    def cleanup(self):
        """Trabajo de limpieza: elimina facturas muy antiguas de la cola."""
        cleaned = self.retry_queue.cleanup_old(max_age_days=7)
        if cleaned > 0:
            logger.info(f"Limpiadas {cleaned} facturas antiguas")

    def start(self):
        """Inicia el agente en modo servicio continuo."""
        self.setup_logging()
        logger.info(f"ECF Agent v{__version__} iniciando...")
        logger.info(f"RNC Cliente: {self.customer_rnc}")
        
        # Configurar trabajos
        polling_interval = self.config.get("agent.polling_interval_seconds", 30)
        retry_interval = self.config.get("agent.retry_interval_seconds", 300)
        status_sync_interval = self.config.get("agent.status_sync_interval_seconds", 300)
        
        self.job_manager.add_polling_job(self.poll_invoices, polling_interval)
        self.job_manager.add_retry_job(self.retry_invoices, retry_interval)
        self.job_manager.add_job(self.sync_statuses, interval_seconds=status_sync_interval)
        self.job_manager.add_cleanup_job(self.cleanup, interval_hours=24)
        
        # Configurar auto-actualización (check cada 4 horas)
        # Siempre agendamos el check para poder notificar, aunque auto_update sea False
        self.job_manager.add_job(
             self.updater.check_and_update, 
             interval_seconds=4 * 3600
        )
        
        # Configurar señales
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.running = True
        self.job_manager.start()
        
        # Chequeo inicial de actualización
        logger.info("Verificando actualizaciones al inicio...")
        if self.updater.check_and_update():
            return  # Si se actualizó, salir/reiniciar (solo pasa si enabled=True)

        # Ejecutar polling inicial
        self.poll_invoices()
        
        logger.info("Agente ECF en ejecución. Ctrl+C para detener.")
        
        # Loop principal
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def run_once(self):
        """Ejecuta un solo ciclo de polling (para testing/debug)."""
        self.setup_logging()
        logger.info(f"ECF Agent v{__version__} - Ejecución única")
        
        self.poll_invoices()
        self.retry_invoices()
        
        logger.info("Ejecución completada")

    def stop(self):
        """Detiene el agente."""
        logger.info("Deteniendo agente ECF...")
        self.running = False
        self.job_manager.stop()
        self.api_client.close()
        logger.info("Agente ECF detenido")

    def _signal_handler(self, signum, frame):
        """Maneja señales de sistema."""
        logger.info(f"Señal {signum} recibida, deteniendo...")
        self.running = False


# ============================================================
# CLI
# ============================================================

@click.group()
@click.version_option(__version__, prog_name="ECF Agent")
def cli():
    """Agente de recolección de comprobantes fiscales electrónicos."""
    pass


@cli.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="Ruta al archivo de configuración YAML",
)
def run(config: Optional[str]):
    """Ejecuta el agente en modo servicio continuo."""
    try:
        cfg = load_config(config)
        cfg.validate()
        
        agent = ECFAgent(cfg)
        agent.start()
        
    except ConfigError as e:
        click.echo(f"Error de configuración: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="Ruta al archivo de configuración YAML",
)
@click.option("--debug", is_flag=True, help="Habilitar modo debug")
def once(config: Optional[str], debug: bool):
    """Ejecuta un solo ciclo de polling (para testing)."""
    try:
        cfg = load_config(config)
        cfg.validate()
        
        if debug:
            logger.add(sys.stderr, level="DEBUG")
        
        agent = ECFAgent(cfg)
        agent.run_once()
        
    except ConfigError as e:
        click.echo(f"Error de configuración: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="Ruta al archivo de configuración YAML",
)
def validate(config: Optional[str]):
    """Valida la configuración y prueba la conexión a BD."""
    try:
        cfg = load_config(config)
        cfg.validate()
        
        click.echo("✓ Configuración válida")
        click.echo(f"  RNC Cliente: {cfg.get('agent.customer_rnc')}")
        click.echo(f"  Base de datos: {cfg.get('database.driver')}://{cfg.get('database.host')}")
        click.echo(f"  API: {cfg.get('api.base_url')}")
        
        # Test conexión BD
        click.echo("\nProbando conexión a base de datos...")
        connector = create_connector(cfg.database)
        if connector.test_connection():
            click.echo("✓ Conexión a BD exitosa")
        else:
            click.echo("✗ Error de conexión a BD", err=True)
            sys.exit(1)
        
    except ConfigError as e:
        click.echo(f"✗ Error de configuración: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="Ruta al archivo de configuración YAML",
)
def status(config: Optional[str]):
    """Muestra estadísticas de la cola de reintentos."""
    try:
        cfg = load_config(config)
        queue = RetryQueue(
            db_path=str(Path(cfg.get("queue.db_path", "./data/queue.db")))
        )
        
        stats = queue.get_stats()
        
        click.echo("Cola de Reintentos:")
        click.echo(f"  Total pendientes: {stats['total_pending']}")
        click.echo(f"  Por intentos: {stats['by_attempts']}")
        click.echo(f"  Más antigua: {stats['oldest_pending'] or 'N/A'}")
        
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
