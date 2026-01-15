"""
Abstracción de conexión a bases de datos.
Proporciona una interfaz unificada para diferentes motores de BD.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from loguru import logger


class DatabaseError(Exception):
    """Error de base de datos."""
    pass


class DatabaseConnector(ABC):
    """Clase base abstracta para conectores de base de datos."""

    def __init__(self, config: Dict[str, Any]):
        """
        Inicializa el conector.
        
        Args:
            config: Configuración de la base de datos
        """
        self.config = config
        self.connection = None
        self._id_field = config.get("id_field", "id")
        self._type_field = config.get("type_field", "tipoecf")
        self._processed_field = config.get("processed_field", "procesada_dgii")

    @abstractmethod
    def connect(self) -> None:
        """Establece la conexión a la base de datos."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Cierra la conexión a la base de datos."""
        pass

    @abstractmethod
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Ejecuta una query SELECT y retorna los resultados.
        
        Args:
            query: Query SQL a ejecutar
            params: Parámetros para la query
        
        Returns:
            Lista de diccionarios con los resultados
        """
        pass

    @abstractmethod
    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """
        Ejecuta una query UPDATE/INSERT/DELETE.
        
        Args:
            query: Query SQL a ejecutar
            params: Parámetros para la query
        
        Returns:
            Número de filas afectadas
        """
        pass

    def get_pending_invoices(self, batch_size: int = 50) -> List[Dict[str, Any]]:
        """
        Obtiene facturas pendientes de procesar.
        
        Args:
            batch_size: Número máximo de facturas a obtener
        
        Returns:
            Lista de facturas pendientes
        """
        query_template = self.config.get("query", "")
        if not query_template:
            raise DatabaseError("No se ha configurado la query de consulta")
        
        # Reemplazar placeholders
        query = query_template.format(batch_size=batch_size)
        
        logger.debug(f"Ejecutando query de facturas pendientes (limit={batch_size})")
        results = self.execute_query(query)
        logger.info(f"Obtenidas {len(results)} facturas pendientes")
        
        return results

    def mark_as_processed(self, invoice_id: Any) -> bool:
        """
        Marca una factura como procesada.
        
        Args:
            invoice_id: ID de la factura
        
        Returns:
            True si se actualizó correctamente
        """
        query_template = self.config.get("update_query", "")
        if not query_template:
            # Query por defecto
            query_template = f"""
                UPDATE facturas 
                SET {self._processed_field} = 1 
                WHERE {self._id_field} = {{id}}
            """
        
        query = query_template.format(id=invoice_id)
        
        try:
            rows = self.execute_update(query)
            if rows > 0:
                logger.debug(f"Factura {invoice_id} marcada como procesada")
                return True
            else:
                logger.warning(f"No se actualizó la factura {invoice_id}")
                return False
        except Exception as e:
            logger.error(f"Error marcando factura {invoice_id} como procesada: {e}")
            return False

    def test_connection(self) -> bool:
        """
        Prueba la conexión a la base de datos.
        
        Returns:
            True si la conexión es exitosa
        """
        try:
            self.connect()
            # Ejecutar query simple de prueba
            self.execute_query("SELECT 1")
            logger.info("Conexión a base de datos exitosa")
            return True
        except Exception as e:
            logger.error(f"Error de conexión a base de datos: {e}")
            return False
        finally:
            self.disconnect()

    def __enter__(self):
        """Context manager: conectar."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: desconectar."""
        self.disconnect()
        return False


class MySQLConnector(DatabaseConnector):
    """Conector para MySQL/MariaDB."""

    def connect(self) -> None:
        """Establece conexión a MySQL."""
        try:
            import pymysql
            
            self.connection = pymysql.connect(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 3306),
                user=self.config.get("username"),
                password=self.config.get("password"),
                database=self.config.get("database"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
            logger.debug("Conectado a MySQL")
        except ImportError:
            raise DatabaseError("PyMySQL no está instalado. Ejecute: pip install pymysql")
        except Exception as e:
            raise DatabaseError(f"Error conectando a MySQL: {e}")

    def disconnect(self) -> None:
        """Cierra conexión a MySQL."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Desconectado de MySQL")

    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Ejecuta SELECT en MySQL."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """Ejecuta UPDATE/INSERT/DELETE en MySQL."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            self.connection.commit()
            return cursor.rowcount


class PostgreSQLConnector(DatabaseConnector):
    """Conector para PostgreSQL."""

    def connect(self) -> None:
        """Establece conexión a PostgreSQL."""
        try:
            import psycopg2
            import psycopg2.extras
            
            self.connection = psycopg2.connect(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 5432),
                user=self.config.get("username"),
                password=self.config.get("password"),
                database=self.config.get("database"),
            )
            logger.debug("Conectado a PostgreSQL")
        except ImportError:
            raise DatabaseError("psycopg2 no está instalado. Ejecute: pip install psycopg2-binary")
        except Exception as e:
            raise DatabaseError(f"Error conectando a PostgreSQL: {e}")

    def disconnect(self) -> None:
        """Cierra conexión a PostgreSQL."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Desconectado de PostgreSQL")

    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Ejecuta SELECT en PostgreSQL."""
        import psycopg2.extras
        
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        with self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """Ejecuta UPDATE/INSERT/DELETE en PostgreSQL."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            self.connection.commit()
            return cursor.rowcount


class SQLServerConnector(DatabaseConnector):
    """Conector para SQL Server."""

    def connect(self) -> None:
        """Establece conexión a SQL Server."""
        try:
            import pyodbc
            
            driver = self.config.get("odbc_driver", "ODBC Driver 17 for SQL Server")
            host = self.config.get("host", "localhost")
            port = self.config.get("port", 1433)
            database = self.config.get("database")
            username = self.config.get("username")
            password = self.config.get("password")
            
            connection_string = (
                f"DRIVER={{{driver}}};"
                f"SERVER={host},{port};"
                f"DATABASE={database};"
                f"UID={username};"
                f"PWD={password};"
            )
            
            self.connection = pyodbc.connect(connection_string)
            logger.debug("Conectado a SQL Server")
        except ImportError:
            raise DatabaseError("pyodbc no está instalado. Ejecute: pip install pyodbc")
        except Exception as e:
            raise DatabaseError(f"Error conectando a SQL Server: {e}")

    def disconnect(self) -> None:
        """Cierra conexión a SQL Server."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Desconectado de SQL Server")

    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Ejecuta SELECT en SQL Server."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params or ())
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """Ejecuta UPDATE/INSERT/DELETE en SQL Server."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params or ())
            self.connection.commit()
            return cursor.rowcount
        finally:
            cursor.close()


class OracleConnector(DatabaseConnector):
    """Conector para Oracle Database."""

    def connect(self) -> None:
        """Establece conexión a Oracle."""
        try:
            import cx_Oracle
            
            host = self.config.get("host", "localhost")
            port = self.config.get("port", 1521)
            service = self.config.get("service_name") or self.config.get("database")
            username = self.config.get("username")
            password = self.config.get("password")
            
            dsn = cx_Oracle.makedsn(host, port, service_name=service)
            self.connection = cx_Oracle.connect(username, password, dsn)
            logger.debug("Conectado a Oracle")
        except ImportError:
            raise DatabaseError("cx_Oracle no está instalado. Ejecute: pip install cx_Oracle")
        except Exception as e:
            raise DatabaseError(f"Error conectando a Oracle: {e}")

    def disconnect(self) -> None:
        """Cierra conexión a Oracle."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Desconectado de Oracle")

    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Ejecuta SELECT en Oracle."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params or {})
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """Ejecuta UPDATE/INSERT/DELETE en Oracle."""
        if not self.connection:
            raise DatabaseError("No hay conexión activa")
        
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, params or {})
            self.connection.commit()
            return cursor.rowcount
        finally:
            cursor.close()


# Registro de drivers disponibles
DRIVERS = {
    "mysql": MySQLConnector,
    "mariadb": MySQLConnector,
    "postgres": PostgreSQLConnector,
    "postgresql": PostgreSQLConnector,
    "sqlserver": SQLServerConnector,
    "mssql": SQLServerConnector,
    "oracle": OracleConnector,
}


def create_connector(config: Dict[str, Any]) -> DatabaseConnector:
    """
    Factory para crear el conector apropiado según el driver configurado.
    
    Args:
        config: Configuración de la base de datos
    
    Returns:
        Instancia del conector apropiado
    
    Raises:
        DatabaseError: Si el driver no está soportado
    """
    driver = config.get("driver", "").lower()
    
    if driver not in DRIVERS:
        supported = ", ".join(DRIVERS.keys())
        raise DatabaseError(f"Driver no soportado: {driver}. Soportados: {supported}")
    
    connector_class = DRIVERS[driver]
    logger.info(f"Usando conector: {connector_class.__name__}")
    
    return connector_class(config)
