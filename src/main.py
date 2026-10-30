"""
Entry point principal del Agente ECF.
Versión simplificada: solo extrae datos de BD y los envía comprimidos.
"""

import os
import signal
import threading
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
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
from .remote.heartbeat import HeartbeatManager


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


def _extract_record_tid(record: Any, fallback_field: Optional[str] = None) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    for k in ("transaccionid", "transaccion_id", "transaction_id", "id"):
        if k in record and record[k] is not None:
            return str(record[k]).strip()
    if fallback_field and fallback_field in record and record[fallback_field] is not None:
        return str(record[fallback_field]).strip()
    lower_map = {str(k).lower(): v for k, v in record.items()}
    for k in ("transaccionid", "transaccion_id", "transaction_id", "id"):
        if k in lower_map and lower_map[k] is not None:
            return str(lower_map[k]).strip()
    if fallback_field and fallback_field.lower() in lower_map and lower_map[fallback_field.lower()] is not None:
        return str(lower_map[fallback_field.lower()]).strip()
    return None


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
        return normalized in {"0.0", "0.00"}
    return False


def _should_nullify(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in NULLISH_STRINGS or _is_zero_like(value)
    return _is_zero_like(value)


def normalize_invoice_data_for_xml(
    obj: Any,
    fields_not_convertible: Set[str],
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
        self.paused = False
        self.version = __version__
        self.last_poll_count = 0
        
        # Componentes
        self.db_connector = create_connector(config.database)
        self.api_client = ECFApiClient(config.api)
        self.retry_queue = RetryQueue(
            db_path=str(Path(config.get("queue.db_path", "./data/queue.db")))
        )
        self.job_manager = JobManager()
        self.updater = AutoUpdater(config.agent)
        self.heartbeat_manager = HeartbeatManager(self)
        self.db_lock = threading.RLock()
        
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
        non_convertible_fields = config.get("normalize.xml_non_convertible_fields", [])

        if isinstance(non_convertible_fields, str):
            non_convertible_fields = [non_convertible_fields]

        if not isinstance(non_convertible_fields, list):
            non_convertible_fields = []

        self.xml_non_convertible_fields = {
            str(field).strip()
            for field in non_convertible_fields
            if str(field).strip()
        }

    def setup_logging(self, log_level: Optional[str] = None):
        """Configura el sistema de logging."""
        log_config = self.config.logging
        level = log_level or os.getenv("LOG_LEVEL") or log_config.get("level", "INFO")
        
        logger.remove()
        
        if log_config.get("console", True):
            logger.add(
                sys.stderr,
                level=level,
                format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
            )
        
        log_file = log_config.get("file")
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.add(
                str(log_path),
                level=level,
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
            )
            if not updated:
                self.db_connector.update_invoice_status(
                    ecf,
                    self.validation_error_status,
                    error_message=error_message,
                )

            if invoice_id:
                self.retry_queue.remove(invoice_id)

        if validation_ecfs:
            logger.warning(
                f"{len(validation_ecfs)} facturas con error de validacion. "
                f"Marcadas con estado {self.validation_error_status} para no reintentar."
            )

        return validation_ecfs

    def _extract_and_prepare_batch(
        self, batch_size: int, filter_retry: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[Any], Dict[str, str]]:
        invoices = self.db_connector.get_pending_invoices(batch_size)
        self.last_poll_count = len(invoices) if invoices else 0

        if not invoices:
            logger.debug("No hay facturas pendientes")
            return [], [], {}

        if filter_retry:
            filtered_invoices = []
            for row in invoices:
                invoice_id = str(row.get(self.id_field))
                if self.retry_queue.exists(invoice_id):
                    continue
                filtered_invoices.append(row)
            invoices = filtered_invoices

        if not invoices:
            logger.info("Todas las facturas pendientes están bajo gestión de reintentos")
            return [], [], {}

        logger.info(f"Obtenidas {len(invoices)} facturas pendientes")

        raw_invoice_ids = [
            str(row.get(self.id_field) if row.get(self.id_field) is not None else _extract_record_tid(row))
            for row in invoices
        ]
        grouped_details = {str(id_): [] for id_ in raw_invoice_ids}
        grouped_taxes = {str(id_): [] for id_ in raw_invoice_ids}
        grouped_payments = {str(id_): [] for id_ in raw_invoice_ids}
        grouped_reference_info = {str(id_): None for id_ in raw_invoice_ids}

        ids_str = ",".join(str(id_) for id_ in raw_invoice_ids)

        details_query = self.config.get("database.details_query")
        if details_query:
            query = details_query.format(ids=ids_str)
            logger.debug(f"Ejecutando subquery details: {query}")
            try:
                details_res = self.db_connector.execute_query(query)
                for d in details_res:
                    tid = _extract_record_tid(d, self.id_field)
                    if tid in grouped_details:
                        grouped_details[tid].append(d)
                if details_res and not any(grouped_details.values()):
                    logger.warning(
                        f"Subquery details retornó {len(details_res)} filas pero ninguna coincidió con IDs {list(grouped_details.keys())}. "
                        f"Columnas en resultado: {list(details_res[0].keys())}"
                    )
                elif not details_res:
                    logger.warning(f"Subquery details retornó 0 filas para IDs ({ids_str})")
            except Exception as e:
                logger.error(f"Error en subquery details: {e}")
        else:
            logger.warning("No se ha configurado 'database.details_query'. Facturas se procesarán sin ítems ('Detalles').")

        taxes_query = self.config.get("database.taxes_query")
        if taxes_query:
            query = taxes_query.format(ids=ids_str)
            logger.debug(f"Ejecutando subquery taxes: {query}")
            try:
                taxes_res = self.db_connector.execute_query(query)
                for t in taxes_res:
                    tid = _extract_record_tid(t, self.id_field)
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
                    tid = _extract_record_tid(p, self.id_field)
                    if tid in grouped_payments:
                        grouped_payments[tid].append(p)
            except Exception as e:
                logger.error(f"Error en subquery payments: {e}")

        reference_info_query = self.config.get("database.reference_info_query")
        if reference_info_query:
            query = reference_info_query.format(ids=ids_str)
            logger.debug(f"Ejecutando subquery reference_info: {query}")
            try:
                reference_res = self.db_connector.execute_query(query)
                for ref in reference_res:
                    tid = _extract_record_tid(ref, self.id_field)
                    if tid in grouped_reference_info and grouped_reference_info[tid] is None:
                        grouped_reference_info[tid] = ref
            except Exception as e:
                logger.error(f"Error en subquery reference_info: {e}")

        invoices_to_send = []
        valid_invoice_ids = []
        ecf_to_id = {}

        for row in invoices:
            try:
                row_id = str(row.get(self.id_field) if row.get(self.id_field) is not None else _extract_record_tid(row))
                invoice_data = dict(row)

                if self.config.get("database.details_query"):
                    details_list = grouped_details.get(row_id, [])
                    invoice_data["Detalles"] = details_list
                    if not details_list:
                        logger.warning(
                            f"Factura ID {row_id} (e-CF: {row.get(self.ecf_field)}) no tiene detalles/ítems en 'Detalles'."
                        )

                if self.config.get("database.taxes_query"):
                    invoice_data["ImpuestosAdicionales"] = grouped_taxes.get(row_id, [])

                if self.config.get("database.payments_query"):
                    invoice_data["FormasPago"] = grouped_payments.get(row_id, [])

                if self.config.get("database.reference_info_query"):
                    reference_info = grouped_reference_info.get(row_id)
                    if reference_info:
                        invoice_data["InformacionReferencia"] = reference_info

                invoice_data = normalize_invoice_data_for_xml(
                    invoice_data,
                    self.xml_non_convertible_fields,
                )

                invoice_data = sanitize_for_serialization(invoice_data)

                ecf_num = str(row.get(self.ecf_field, ""))
                invoice_payload = {
                    "rnc_buyer": str(row.get(self.rnc_buyer_field, "")),
                    "ecf": ecf_num,
                    "ecf_type": str(row.get(self.type_field, "")),
                    "total_amount": str(row.get(self.total_field, "0.00")),
                    "invoice_data": invoice_data,
                }

                logger.debug(f"Factura procesada: {json.dumps(invoice_data, ensure_ascii=False, indent=2)}")
                invoices_to_send.append(invoice_payload)
                inv_id = row.get(self.id_field)
                valid_invoice_ids.append(inv_id)
                if ecf_num:
                    ecf_to_id[ecf_num] = str(inv_id)

            except json.JSONDecodeError as e:
                logger.error(f"Error parseando JSON de factura: {e}")
                continue
            except Exception as e:
                logger.error(f"Error procesando factura: {e}")
                continue

        return invoices_to_send, valid_invoice_ids, ecf_to_id

    def poll_invoices(self):
        """
        Trabajo principal: obtiene facturas de la BD y las envía al servidor.
        """
        if getattr(self, "paused", False):
            logger.debug("Agente pausado remotamente, omitiendo polling.")
            return 0

        logger.info("Iniciando polling de facturas...")
        try:
            with self.db_lock:
                with self.db_connector:
                    invoices_to_send, invoice_ids, ecf_to_id = self._extract_and_prepare_batch(
                        self.batch_size, filter_retry=True
                    )

                    if not invoices_to_send:
                        if self.last_poll_count > 0:
                            logger.warning("No se pudieron procesar facturas")
                        return

                    try:
                        response = self.api_client.send_batch(
                            self.customer_rnc,
                            invoices_to_send,
                            compress=True,
                            compression_method=self.compression_method,
                        )

                        for invoice_id in invoice_ids:
                            self.db_connector.mark_as_processed(invoice_id)

                        logger.info(f"Batch enviado exitosamente: {len(invoices_to_send)} facturas")

                        results = response.get("results", [])
                        if results:
                            logger.info(f"Procesando {len(results)} estados recibidos sincrónicamente")
                            for res in results:
                                ecf = res.get("ecf")
                                if ecf not in ecf_to_id:
                                    continue
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
                                    if status_key == "conditional_accepted" and error_msg:
                                        logger.warning(f"e-CF {ecf} aceptado condicional. Mensajes DGII: {error_msg}")
                                    self.db_connector.update_invoice_status(ecf, local_status, track_id)

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
                            if isinstance(res, dict) and str(res.get("status", "")).lower() in ("ok", "success", "processed", "200", "retry_pending")
                        }

                        if validation_ecfs:
                            failed_ecfs = {ecf for ecf in failed_ecfs if ecf not in validation_ecfs}

                        if not failed_ecfs and not success_ecfs and not validation_ecfs:
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
                            added_to_retry = 0
                            for i, invoice in enumerate(invoices_to_send):
                                ecf_num = invoice.get("ecf")
                                invoice_id = str(invoice_ids[i])

                                if ecf_num in validation_ecfs:
                                    continue

                                if ecf_num in failed_ecfs or (ecf_num not in success_ecfs):
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
                                    self.db_connector.mark_as_processed(invoice_id)
                            if added_to_retry > 0:
                                logger.info(f"{added_to_retry} facturas enviadas a cola de reintentos por fallo parcial")

                    except Exception as e:
                        logger.error(f"Error genérico enviando batch o actualizando estados: {e}")

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
            with self.db_lock:
                with self.db_connector:
                    pending_invoices = self.db_connector.get_pending_status_invoices(self.batch_size)
                    
                    if not pending_invoices:
                        return
                    
                    # Extraemos solo los números de ECF
                    ecf_field_name = self.config.get("database.ecf_field", "encf")
                    ecfs_to_sync = [str(row.get(ecf_field_name)) for row in pending_invoices if row.get(ecf_field_name)]
                    ecf_to_id = {
                        str(row.get(ecf_field_name)): row.get("id")
                        for row in pending_invoices
                        if row.get(ecf_field_name)
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
                            if ecf not in ecf_to_id:
                                continue
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
                                if status_key == "conditional_accepted" and error_msg:
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
            with self.db_lock:
                with self.db_connector:
                    for item in pending:
                        try:
                            self.api_client.send_single(
                                item["customer_rnc"],
                                item["payload"],
                                compress=True,
                                compression_method=self.compression_method,
                            )

                            self.db_connector.mark_as_processed(item["invoice_id"])
                            self.retry_queue.remove(item["invoice_id"])
                            logger.info(
                                f"Factura {item.get('ecf_number', item['invoice_id'])} "
                                f"(ID: {item['invoice_id']}) reenviada exitosamente y actualizada en ERP"
                            )

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

                            self.retry_queue.update_attempt(item["invoice_id"], str(e))
                            new_attempts = item["attempts"] + 1
                            logger.warning(
                                f"Reintento fallido para {item.get('ecf_number', item['invoice_id'])} "
                                f"(intento {new_attempts}): {e}"
                            )
                            if new_attempts >= self.max_retries:
                                logger.error(
                                    f"Factura {item.get('ecf_number', item['invoice_id'])} superó max_retries ({self.max_retries}). Marcando como fallida en ERP."
                                )
                                self.db_connector.mark_as_failed(
                                    item.get("ecf_number") or item["invoice_id"],
                                    str(e),
                                    item["invoice_id"],
                                )
                                self.retry_queue.remove(item["invoice_id"])

                        except Exception as e:
                            self.retry_queue.update_attempt(item["invoice_id"], str(e))
                            new_attempts = item["attempts"] + 1
                            logger.warning(
                                f"Reintento fallido para {item.get('ecf_number', item['invoice_id'])} "
                                f"(intento {new_attempts}): {e}"
                            )
                            if new_attempts >= self.max_retries:
                                logger.error(
                                    f"Factura {item.get('ecf_number', item['invoice_id'])} superó max_retries ({self.max_retries}). Marcando como fallida en ERP."
                                )
                                self.db_connector.mark_as_failed(
                                    item.get("ecf_number") or item["invoice_id"],
                                    str(e),
                                    item["invoice_id"],
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
        
        # Sincronización remota inicial
        try:
            if self.config.sync_remote(self.api_client):
                self._reapply_config_fields()
                logger.info("Configuración remota sincronizada.")
        except Exception as e:
            logger.debug(f"Sincronización remota inicial omitida: {e}")

        # Configurar trabajos
        polling_interval = self.config.get("agent.polling_interval_seconds", 30)
        retry_interval = self.config.get("agent.retry_interval_seconds", 300)
        status_sync_interval = self.config.get("agent.status_sync_interval_seconds", 300)
        
        self.job_manager.add_polling_job(self.poll_invoices, polling_interval)
        self.job_manager.add_retry_job(self.retry_invoices, retry_interval)
        self.job_manager.add_job(self.sync_statuses, interval_seconds=status_sync_interval)
        self.job_manager.add_cleanup_job(self.cleanup, interval_hours=24)
        
        # Configurar heartbeat remoto
        self.job_manager.add_job(
            self.heartbeat_manager.pulse,
            interval_seconds=self.heartbeat_manager.current_interval,
            name="heartbeat",
        )

        # Configurar auto-actualización (check cada 4 horas)
        self.job_manager.add_job(
             self.updater.check_and_update, 
             interval_seconds=4 * 3600
        )
        
        # Configurar señales
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.running = True
        self.job_manager.start()
        
        # Pulso inicial de telemetría
        self.heartbeat_manager.pulse()

        # Chequeo inicial de actualización
        logger.info("Verificando actualizaciones al inicio...")
        update_res = self.updater.check_and_update()
        if (isinstance(update_res, dict) and update_res.get("updated")) or update_res is True:
            return

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

    def reload_config(self) -> bool:
        logger.info("Recargando configuración remota...")
        try:
            changed = self.config.sync_remote(self.api_client)
            if changed:
                self._reapply_config_fields()
                with self.db_lock:
                    if hasattr(self.db_connector, "close"):
                        try:
                            self.db_connector.close()
                        except Exception:
                            pass
                    self.db_connector = create_connector(self.config.database)
                logger.info("Configuración remota recargada y conector BD actualizado exitosamente.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error recargando configuración remota: {e}")
            return False

    def _reapply_config_fields(self):
        self.customer_rnc = self.config.get("agent.customer_rnc", self.customer_rnc)
        self.batch_size = self.config.get("agent.batch_size", self.batch_size)
        self.max_retries = self.config.get("agent.max_retries", self.max_retries)
        self.compression_method = self.config.get("api.compression", self.compression_method)
        self.id_field = self.config.get("database.id_field", self.id_field)
        self.ecf_field = self.config.get("database.ecf_field", self.ecf_field)
        self.type_field = self.config.get("database.type_field", self.type_field)
        self.rnc_buyer_field = self.config.get("database.rnc_buyer_field", self.rnc_buyer_field)
        self.total_field = self.config.get("database.total_field", self.total_field)

        non_convertible = self.config.get("normalize.xml_non_convertible_fields", [])
        if isinstance(non_convertible, str):
            non_convertible = [non_convertible]
        if isinstance(non_convertible, list):
            self.xml_non_convertible_fields = {str(f).strip() for f in non_convertible if str(f).strip()}

    def run_once(self, debug: bool = False, dry_run: bool = False):
        """Ejecuta un solo ciclo de polling (para testing/debug)."""
        self.setup_logging(log_level="DEBUG" if debug else None)
        logger.info(f"ECF Agent v{__version__} - Ejecución única{' (DRY-RUN)' if dry_run else ''}")

        try:
            if self.config.sync_remote(self.api_client):
                self._reapply_config_fields()
                logger.info("Configuración remota sincronizada.")
        except Exception as e:
            logger.debug(f"Sincronización remota inicial omitida: {e}")

        if dry_run:
            self.debug_invoices(limit=self.batch_size, send=False)
        else:
            self.poll_invoices()
            self.retry_invoices()

        logger.info("Ejecución completada")

    def debug_invoices(self, limit: int = 1, send: bool = False) -> List[Dict[str, Any]]:
        """Inspecciona y valida la extracción y mapeo de facturas sin alterar la BD."""
        self.setup_logging(log_level="DEBUG")
        logger.info(f"ECF Agent v{__version__} - Modo Debug / Inspección")

        try:
            if self.config.sync_remote(self.api_client):
                self._reapply_config_fields()
                logger.info("Configuración remota sincronizada.")
        except Exception as e:
            logger.debug(f"Sincronización remota inicial omitida: {e}")

        with self.db_lock:
            with self.db_connector:
                invoices_to_send, invoice_ids, _ = self._extract_and_prepare_batch(
                    limit, filter_retry=False
                )

                if not invoices_to_send:
                    logger.warning("No se encontraron facturas pendientes para inspeccionar")
                    return []

                logger.info(f"Se extrajeron {len(invoices_to_send)} factura(s) para inspección:")
                for idx, inv in enumerate(invoices_to_send, 1):
                    click.echo(f"\n================ [ FACTURA {idx}/{len(invoices_to_send)} ] ================")
                    click.echo(f"ID Local: {invoice_ids[idx-1]}")
                    click.echo(f"e-CF: {inv.get('ecf')}")
                    click.echo(f"Tipo e-CF: {inv.get('ecf_type')}")
                    click.echo(f"RNC Comprador: {inv.get('rnc_buyer')}")
                    click.echo(f"Monto Total: {inv.get('total_amount')}")
                    click.echo("---------------- Payload JSON Normalizado ----------------")
                    click.echo(json.dumps(inv, ensure_ascii=False, indent=2))
                    click.echo("========================================================\n")

                if send:
                    logger.info("Opción --send activada: enviando factura(s) a la API...")
                    response = self.api_client.send_batch(
                        self.customer_rnc,
                        invoices_to_send,
                        compress=True,
                        compression_method=self.compression_method,
                    )
                    logger.info(f"Respuesta de la API: {response}")
                    for inv_id in invoice_ids:
                        self.db_connector.mark_as_processed(inv_id)
                    logger.info("Factura(s) marcada(s) como procesada(s) en BD.")
                else:
                    logger.info("[DRY-RUN] Modo simulación: no se modificó la base de datos ni se envió a la API.")

                return invoices_to_send

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
@click.option("--debug", is_flag=True, help="Habilitar modo debug con logs detallados")
@click.option("--dry-run", is_flag=True, help="Simula la extracción sin enviar ni marcar como procesado en BD")
def once(config: Optional[str], debug: bool, dry_run: bool):
    """Ejecuta un solo ciclo de polling (para testing)."""
    try:
        cfg = load_config(config)
        cfg.validate()
        
        agent = ECFAgent(cfg)
        agent.run_once(debug=debug, dry_run=dry_run)
        
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
@click.option(
    "-l", "--limit",
    type=int,
    default=1,
    show_default=True,
    help="Número de facturas a extraer para inspección",
)
@click.option(
    "--send",
    is_flag=True,
    help="Enviar factura(s) a la API tras inspección (por defecto es solo simulación / dry-run)",
)
def debug(config: Optional[str], limit: int, send: bool):
    """Inspecciona y valida la extracción y mapeo de facturas sin alterar la BD."""
    try:
        cfg = load_config(config)
        cfg.validate()
        
        agent = ECFAgent(cfg)
        agent.debug_invoices(limit=limit, send=send)
        
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


@cli.command("check-config", hidden=True)
@click.option(
    "-c", "--config",
    type=click.Path(exists=True),
    help="Ruta al archivo de configuración YAML",
)
@click.pass_context
def check_config(ctx, config: Optional[str]):
    ctx.invoke(validate, config=config)


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
