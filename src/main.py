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

import click
from loguru import logger

from . import __version__
from .config import Config, ConfigError, load_config
from .database import create_connector
from .queue import RetryQueue
from .scheduler import JobManager
from .sender import ECFApiClient
from .updater import AutoUpdater


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
        
        # Campos de la query
        self.json_field = config.get("database.json_field", "invoice_json")
        self.id_field = config.get("database.id_field", "id")
        self.ecf_field = config.get("database.ecf_field", "ecf_number")
        self.type_field = config.get("database.type_field", "ecf_type")
        self.rnc_buyer_field = config.get("database.rnc_buyer_field", "rnc_buyer")
        self.total_field = config.get("database.total_field", "total_amount")

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
                
                logger.info(f"Obtenidas {len(invoices)} facturas pendientes")
                
                # Preparar datos para envío
                invoices_to_send = []
                invoice_ids = []
                
                for row in invoices:
                    try:
                        # Obtener el JSON de la factura
                        invoice_json = row.get(self.json_field)
                        
                        # Si es string, parsearlo
                        if isinstance(invoice_json, str):
                            invoice_data = json.loads(invoice_json)
                        else:
                            invoice_data = invoice_json
                        
                        if not invoice_data:
                            logger.warning(f"Factura sin datos JSON: {row.get(self.id_field)}")
                            continue
                        
                        # Preparar estructura para envío
                        invoice_payload = {
                            "rnc_buyer": str(row.get(self.rnc_buyer_field, "")),
                            "ecf": str(row.get(self.ecf_field, "")),
                            "ecf_type": str(row.get(self.type_field, "")),
                            "total_amount": str(row.get(self.total_field, "0.00")),
                            "invoice_data": invoice_data,  # Se comprimirá en el sender
                        }
                        
                        # logger.debug(f"Factura procesada: {json.dumps(invoice_data, ensure_ascii=False, indent=2)}")
                        
                        invoices_to_send.append(invoice_payload)
                        invoice_ids.append(row.get(self.id_field))
                        
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
                    
                except Exception as e:
                    logger.error(f"Error enviando batch: {e}")
                    
                    # Agregar a cola de reintentos
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
        
        for item in pending:
            try:
                self.api_client.send_single(
                    item["customer_rnc"],
                    item["payload"],
                    compress=True,
                    compression_method=self.compression_method,
                )
                
                # Éxito: eliminar de cola
                self.retry_queue.remove(item["invoice_id"])
                logger.info(f"Factura {item['invoice_id']} reenviada exitosamente")
                
            except Exception as e:
                # Actualizar contador de intentos
                self.retry_queue.update_attempt(item["invoice_id"], str(e))
                logger.warning(
                    f"Reintento fallido para {item['invoice_id']} "
                    f"(intento {item['attempts'] + 1}): {e}"
                )

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
        
        self.job_manager.add_polling_job(self.poll_invoices, polling_interval)
        self.job_manager.add_retry_job(self.retry_invoices, retry_interval)
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
