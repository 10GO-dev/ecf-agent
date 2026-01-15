"""
Módulo de conexión a bases de datos.
Soporta MySQL, PostgreSQL, SQL Server y Oracle.
"""

from .connector import DatabaseConnector, create_connector

__all__ = ["DatabaseConnector", "create_connector"]
