"""
Cola de reintentos usando SQLite.
Almacena facturas que no se pudieron enviar para reintentar más tarde.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class RetryQueue:
    """
    Cola persistente para reintentos de facturas fallidas.
    
    Usa SQLite para almacenar las facturas que no se pudieron enviar,
    permitiendo reintentar el envío más tarde.
    """

    def __init__(self, db_path: str = "./data/queue.db"):
        """
        Inicializa la cola de reintentos.
        
        Args:
            db_path: Ruta al archivo SQLite
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"Cola de reintentos inicializada: {self.db_path}")

    def _init_db(self):
        """Crea las tablas si no existen."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id TEXT UNIQUE NOT NULL,
                    customer_rnc TEXT NOT NULL,
                    ecf_type TEXT,
                    ecf_number TEXT,
                    payload TEXT NOT NULL,
                    error_message TEXT,
                    attempts INTEGER DEFAULT 0,
                    last_attempt TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_attempts 
                ON pending_invoices(attempts, last_attempt)
            """)
            
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtiene una conexión a la base de datos."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def add(
        self,
        invoice_id: str,
        customer_rnc: str,
        payload: Dict[str, Any],
        ecf_type: Optional[str] = None,
        ecf_number: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Agrega una factura a la cola de reintentos.
        
        Args:
            invoice_id: ID único de la factura
            customer_rnc: RNC del cliente
            payload: Datos de la factura (ya transformados)
            ecf_type: Tipo de comprobante
            ecf_number: Número e-NCF
            error_message: Mensaje de error del intento fallido
        
        Returns:
            True si se agregó correctamente
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pending_invoices 
                    (invoice_id, customer_rnc, ecf_type, ecf_number, payload, error_message, attempts, last_attempt)
                    VALUES (?, ?, ?, ?, ?, ?, 
                        COALESCE((SELECT attempts FROM pending_invoices WHERE invoice_id = ?), 0) + 1,
                        CURRENT_TIMESTAMP)
                    """,
                    (
                        invoice_id,
                        customer_rnc,
                        ecf_type,
                        ecf_number,
                        json.dumps(payload, ensure_ascii=False),
                        error_message,
                        invoice_id,
                    ),
                )
                conn.commit()
                logger.debug(f"Factura {invoice_id} agregada a cola de reintentos")
                return True
        except Exception as e:
            logger.error(f"Error agregando factura a cola: {e}")
            return False

    def get_pending(self, max_retries: int = 5, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Obtiene facturas pendientes de reintento.
        
        Args:
            max_retries: Número máximo de intentos permitidos
            limit: Máximo de facturas a obtener
        
        Returns:
            Lista de facturas pendientes
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, invoice_id, customer_rnc, ecf_type, ecf_number, 
                       payload, error_message, attempts, last_attempt, created_at
                FROM pending_invoices
                WHERE attempts < ?
                ORDER BY last_attempt ASC, created_at ASC
                LIMIT ?
                """,
                (max_retries, limit),
            )
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "invoice_id": row["invoice_id"],
                    "customer_rnc": row["customer_rnc"],
                    "ecf_type": row["ecf_type"],
                    "ecf_number": row["ecf_number"],
                    "payload": json.loads(row["payload"]),
                    "error_message": row["error_message"],
                    "attempts": row["attempts"],
                    "last_attempt": row["last_attempt"],
                    "created_at": row["created_at"],
                })
            
            logger.debug(f"Obtenidas {len(results)} facturas pendientes de reintento")
            return results

    def remove(self, invoice_id: str) -> bool:
        """
        Elimina una factura de la cola (enviada exitosamente).
        
        Args:
            invoice_id: ID de la factura
        
        Returns:
            True si se eliminó correctamente
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM pending_invoices WHERE invoice_id = ?",
                    (invoice_id,),
                )
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.debug(f"Factura {invoice_id} eliminada de cola de reintentos")
                    return True
                return False
        except Exception as e:
            logger.error(f"Error eliminando factura de cola: {e}")
            return False

    def update_attempt(self, invoice_id: str, error_message: Optional[str] = None):
        """
        Actualiza el contador de intentos de una factura.
        
        Args:
            invoice_id: ID de la factura
            error_message: Mensaje de error del último intento
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE pending_invoices 
                    SET attempts = attempts + 1, 
                        last_attempt = CURRENT_TIMESTAMP,
                        error_message = ?
                    WHERE invoice_id = ?
                    """,
                    (error_message, invoice_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error actualizando intento: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas de la cola.
        
        Returns:
            Diccionario con estadísticas
        """
        with self._get_connection() as conn:
            # Total pendientes
            total = conn.execute(
                "SELECT COUNT(*) as count FROM pending_invoices"
            ).fetchone()["count"]
            
            # Por número de intentos
            by_attempts = {}
            for row in conn.execute(
                "SELECT attempts, COUNT(*) as count FROM pending_invoices GROUP BY attempts"
            ).fetchall():
                by_attempts[row["attempts"]] = row["count"]
            
            # Más antigua
            oldest = conn.execute(
                "SELECT MIN(created_at) as oldest FROM pending_invoices"
            ).fetchone()["oldest"]
            
            return {
                "total_pending": total,
                "by_attempts": by_attempts,
                "oldest_pending": oldest,
            }

    def cleanup_old(self, max_age_days: int = 7) -> int:
        """
        Limpia facturas muy antiguas de la cola.
        
        Args:
            max_age_days: Máximo de días para mantener en cola
        
        Returns:
            Número de facturas eliminadas
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM pending_invoices 
                WHERE created_at < datetime('now', '-' || ? || ' days')
                """,
                (max_age_days,),
            )
            conn.commit()
            deleted = cursor.rowcount
            
            if deleted > 0:
                logger.info(f"Limpiadas {deleted} facturas antiguas de la cola")
            
            return deleted
