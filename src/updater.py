"""
Módulo de auto-actualización para el Agente ECF.
Descarga y reemplaza el ejecutable si encuentra una nueva versión.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from packaging import version
from loguru import logger

from . import __version__

class AutoUpdater:
    """Gestor de actualizaciones automáticas."""

    def __init__(self, config: Dict[str, Any]):
        """
        Inicializa el actualizador.

        Args:
            config: Diccionario de configuración (sección 'agent').
        """
        self.enabled = config.get("auto_update", False)
        self.update_url = config.get("update_url")
        self.current_version = __version__
        self.is_frozen = getattr(sys, "frozen", False)
        
        # Ruta del ejecutable actual
        if self.is_frozen:
            self.app_path = Path(sys.executable)
        else:
            # En desarrollo, no tiene sentido actualizar el .py
            self.app_path = Path(sys.argv[0])

    def check_and_update(self) -> bool:
        """
        Verifica si hay actualizaciones en GitHub Releases y las aplica.
        """
        # Si no se configura URL específica, usar la API pública de GitHub
        # Formato esperado: https://api.github.com/repos/USUARIO/REPO/releases/latest
        api_url = self.update_url or "https://api.github.com/repos/TU_USUARIO/ecf-agent/releases/latest"

        try:
            logger.info(f"Buscando actualizaciones en {api_url}...")
            
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            
            release_data = response.json()
            remote_tag = release_data.get("tag_name", "").lstrip("v") # ej. v1.0.1 -> 1.0.1
            
            if not remote_tag:
                logger.warning("No se pudo obtener la versión de la release.")
                return False

            remote_version = version.parse(remote_tag)
            local_version = version.parse(self.current_version)

            logger.info(f"Versión local: {local_version}, Remota: {remote_version}")

            if remote_version <= local_version:
                logger.debug("El agente está actualizado.")
                return False

            # NOTIFICACIÓN DE ACTUALIZACIÓN
            logger.warning(
                f"🚨 ¡NUEVA ACTUALIZACIÓN DISPONIBLE! "
                f"Versión instalada: {local_version} -> Nueva versión: {remote_version}"
            )

            if not self.is_frozen:
                logger.info("Ejecutando desde código fuente, no se puede auto-actualizar automáticamente. Actualice manualmente.")
                return False

            if not self.enabled:
                logger.info("Auto-actualización deshabilitada. Por favor, actualice manualmente.")
                return False

            logger.info(f"Iniciando actualización automática a {remote_version}...")
            logger.info(f"Buscando asset compatible...")
            
            # Buscar el asset correcto según el OS
            asset_url = None
            target_name = "ecf-agent-windows.exe" if sys.platform == "win32" else "ecf-agent-linux"
            
            for asset in release_data.get("assets", []):
                if asset["name"] == target_name:
                    asset_url = asset["browser_download_url"]
                    break
            
            # Fallback para nombres antiguos o genericos
            if not asset_url and sys.platform == "win32":
                 for asset in release_data.get("assets", []):
                    if asset["name"].endswith(".exe"):
                        asset_url = asset["browser_download_url"]
                        break

            if not asset_url:
                logger.error(f"No se encontró un ejecutable compatible en la release {remote_tag}.")
                return False
            
            logger.info(f"Descargando actualización desde: {asset_url}")

            # Descargar el nuevo ejecutable
            new_exe_path = self.app_path.with_suffix(".new.exe") if sys.platform == "win32" else self.app_path.with_suffix(".new")
            self._download_file(asset_url, new_exe_path)

            if new_exe_path.stat().st_size == 0:
                logger.error("Descarga fallida (archivo vacío).")
                new_exe_path.unlink(missing_ok=True)
                return False

            logger.info("Descarga completada. Aplicando actualización...")

            # Reemplazar ejecutable (Windows: rename trick; Linux: overwrite directly usually works or rename)
            backup_path = self.app_path.with_suffix(".old.exe") if sys.platform == "win32" else self.app_path.with_suffix(".old")
            
            if backup_path.exists():
                backup_path.unlink()

            self.app_path.rename(backup_path)
            new_exe_path.rename(self.app_path)
            
            if sys.platform != "win32":
                self.app_path.chmod(0o755) # Asegurar ejecutable en Linux
            
            logger.warning("Actualización aplicada. Reiniciando servicio...")
            self._restart()
            return True

        except Exception as e:
            logger.error(f"Error durante actualización: {e}")
            return False

    def _download_file(self, url: str, target: Path):
        """Descarga un archivo con stream."""
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(target, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    def _restart(self):
        """Reinicia la aplicación."""
        logger.info("Reiniciando proceso...")
        # Asegurar que logs se escriban
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Ejecutar nuevo proceso
        # subprocess.Popen detaches the new process
        if sys.platform == 'win32':
             subprocess.Popen([str(self.app_path)] + sys.argv[1:])
        else:
             # Linux/Unix exec replaces the process
             os.execv(sys.executable, [sys.executable] + sys.argv[1:])
        
        # Salir del proceso actual
        sys.exit(0)
