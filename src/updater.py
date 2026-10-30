"""
Módulo de auto-actualización para el Agente ECF.
Descarga y reemplaza el ejecutable si encuentra una nueva versión.
"""

import os
import sys
import shutil
import tarfile
import tempfile
import zipfile
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from packaging import version
from loguru import logger

from . import __version__

class AutoUpdater:
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get("auto_update", False)
        self.update_url = config.get("update_url")
        self.current_version = __version__
        self.is_frozen = getattr(sys, "frozen", False)
        
        if self.is_frozen:
            self.app_path = Path(sys.executable)
        else:
            self.app_path = Path(sys.argv[0])

    def check_and_update(
        self,
        force: bool = False,
        restart_immediately: bool = True,
        target_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        if target_version:
            clean_tag = target_version.lstrip("v")
            api_url = f"https://api.github.com/repos/10GO-dev/ecf-agent/releases/tags/v{clean_tag}"
        else:
            api_url = self.update_url or "https://api.github.com/repos/10GO-dev/ecf-agent/releases/latest"

        try:
            logger.info(f"Buscando actualizaciones en {api_url}...")
            
            response = requests.get(api_url, timeout=15)
            response.raise_for_status()
            
            release_data = response.json()
            remote_tag = release_data.get("tag_name", "").lstrip("v")
            
            if not remote_tag:
                logger.warning("No se pudo obtener la versión de la release.")
                return {"success": False, "error": "No se pudo obtener la versión de la release"}

            remote_version = version.parse(remote_tag)
            local_version = version.parse(self.current_version)

            logger.info(f"Versión local: {local_version}, Remota: {remote_version}")

            if remote_version <= local_version and not target_version:
                logger.debug("El agente está actualizado.")
                return {
                    "success": True,
                    "updated": False,
                    "old_version": self.current_version,
                    "new_version": self.current_version,
                    "message": f"El agente ya se encuentra en la versión más reciente ({self.current_version}).",
                }

            logger.warning(
                f"🚨 ¡NUEVA ACTUALIZACIÓN DISPONIBLE! "
                f"Versión instalada: {local_version} -> Nueva versión: {remote_version}"
            )

            if not force and not self.enabled:
                logger.info("Auto-actualización deshabilitada. Por favor, actualice manualmente o vía comando remoto.")
                return {
                    "success": True,
                    "updated": False,
                    "available_version": remote_tag,
                    "old_version": self.current_version,
                    "message": f"Nueva versión {remote_tag} disponible, pero auto-actualización está deshabilitada.",
                }

            logger.info(f"Iniciando actualización a {remote_version} (frozen={self.is_frozen})...")

            if self.is_frozen:
                result = self._apply_binary_update(release_data, remote_tag)
            else:
                result = self._apply_source_update(release_data, remote_tag)

            if result.get("success") and restart_immediately:
                self.restart()

            return result

        except Exception as e:
            logger.error(f"Error durante actualización: {e}")
            return {"success": False, "error": str(e)}

    def _extract_archive(self, archive_path: Path, extract_dir: Path):
        name = archive_path.name.lower()
        if name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive_path, "r:*") as tar:
                if hasattr(tarfile, 'data_filter'):
                    tar.extractall(extract_dir, filter='data')
                else:
                    tar.extractall(extract_dir)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_dir)

    def _apply_source_update(self, release_data: Dict[str, Any], remote_tag: str) -> Dict[str, Any]:
        temp_dir = Path(tempfile.mkdtemp(prefix="ecf_agent_src_update_"))
        try:
            download_url = None
            archive_ext = ".tar.gz"

            for asset in release_data.get("assets", []):
                name = asset.get("name", "").lower()
                if name.endswith(".tar.gz") or name.endswith(".tgz"):
                    download_url = asset["browser_download_url"]
                    archive_ext = ".tar.gz"
                    break
                elif name.endswith(".zip"):
                    download_url = asset["browser_download_url"]
                    archive_ext = ".zip"
                    break

            if not download_url:
                download_url = release_data.get("tarball_url") or release_data.get("zipball_url")
                if download_url and "zipball" in download_url:
                    archive_ext = ".zip"

            if not download_url:
                return {
                    "success": False,
                    "error": f"No se encontró código fuente en la release {remote_tag}",
                }

            archive_path = temp_dir / f"release{archive_ext}"
            self._download_file(download_url, archive_path)

            extract_dir = temp_dir / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            self._extract_archive(archive_path, extract_dir)

            candidate_src = None
            for p in extract_dir.rglob("src"):
                if p.is_dir() and (p / "__init__.py").exists() and (p / "main.py").exists():
                    candidate_src = p
                    break

            if not candidate_src:
                return {
                    "success": False,
                    "error": "No se encontró el directorio src válido dentro del archivo descargado",
                }

            root_dir = Path(__file__).resolve().parent.parent
            target_src = root_dir / "src"
            backup_src = root_dir / "src_old"

            if backup_src.exists():
                shutil.rmtree(backup_src, ignore_errors=True)

            target_src.rename(backup_src)
            try:
                shutil.copytree(candidate_src, target_src)
            except Exception as copy_err:
                if backup_src.exists() and not target_src.exists():
                    backup_src.rename(target_src)
                raise copy_err

            candidate_req = None
            for req in extract_dir.rglob("requirements.txt"):
                if req.is_file():
                    candidate_req = req
                    break

            if candidate_req and candidate_req.exists():
                target_req = root_dir / "requirements.txt"
                shutil.copy2(candidate_req, target_req)

                pip_path = None
                if (root_dir / "venv" / "bin" / "pip").exists():
                    pip_path = str(root_dir / "venv" / "bin" / "pip")
                elif (root_dir / "venv" / "Scripts" / "pip.exe").exists():
                    pip_path = str(root_dir / "venv" / "Scripts" / "pip.exe")

                if pip_path:
                    try:
                        logger.info("Actualizando dependencias con pip...")
                        subprocess.run(
                            [pip_path, "install", "--no-cache-dir", "-r", str(target_req)],
                            check=True,
                            timeout=180,
                            capture_output=True,
                        )
                    except Exception as pip_err:
                        logger.warning(f"Error actualizando paquetes pip: {pip_err}")

            shutil.rmtree(backup_src, ignore_errors=True)

            old_version = self.current_version
            self.current_version = remote_tag
            return {
                "success": True,
                "updated": True,
                "old_version": old_version,
                "new_version": remote_tag,
                "message": f"Actualización de código fuente a v{remote_tag} completada exitosamente.",
                "needs_restart": True,
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _apply_binary_update(self, release_data: Dict[str, Any], remote_tag: str) -> Dict[str, Any]:
        temp_dir = Path(tempfile.mkdtemp(prefix="ecf_agent_bin_update_"))
        try:
            asset_url = None
            is_win = sys.platform == "win32"
            target_asset_name = "ecf-agent-windows.zip" if is_win else "ecf-agent-linux.tar.gz"

            for asset in release_data.get("assets", []):
                name = asset.get("name", "")
                if name == target_asset_name:
                    asset_url = asset["browser_download_url"]
                    break

            if not asset_url:
                for asset in release_data.get("assets", []):
                    name = asset.get("name", "").lower()
                    if is_win and (name.endswith(".zip") or name.endswith(".exe")):
                        asset_url = asset["browser_download_url"]
                        break
                    elif not is_win and (name.endswith(".tar.gz") or name == "ecf-agent"):
                        asset_url = asset["browser_download_url"]
                        break

            if not asset_url:
                return {
                    "success": False,
                    "error": f"No se encontró binario compatible en release {remote_tag}",
                }

            download_path = temp_dir / ("update.zip" if ".zip" in asset_url else "update.tar.gz" if ".tar.gz" in asset_url else "update.bin")
            self._download_file(asset_url, download_path)

            extracted_exe = None
            if download_path.name.endswith((".zip", ".tar.gz", ".tgz")):
                extract_dir = temp_dir / "extracted"
                extract_dir.mkdir(parents=True, exist_ok=True)
                self._extract_archive(download_path, extract_dir)

                target_filename = "ecf-agent.exe" if is_win else "ecf-agent"
                for p in extract_dir.rglob(target_filename):
                    if p.is_file():
                        extracted_exe = p
                        break
            else:
                extracted_exe = download_path

            if not extracted_exe or not extracted_exe.exists():
                return {
                    "success": False,
                    "error": "No se encontró el ejecutable dentro del archivo descargado",
                }

            backup_path = self.app_path.with_suffix(".old.exe") if is_win else self.app_path.with_suffix(".old")
            if backup_path.exists():
                backup_path.unlink()

            self.app_path.rename(backup_path)
            shutil.copy2(extracted_exe, self.app_path)

            if not is_win:
                self.app_path.chmod(0o755)

            old_version = self.current_version
            self.current_version = remote_tag
            return {
                "success": True,
                "updated": True,
                "old_version": old_version,
                "new_version": remote_tag,
                "message": f"Actualización binaria a v{remote_tag} completada exitosamente.",
                "needs_restart": True,
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _download_file(self, url: str, target: Path):
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(target, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    def restart(self):
        logger.info("Reiniciando proceso del agente...")
        sys.stdout.flush()
        sys.stderr.flush()
        if sys.platform == 'win32':
            if self.is_frozen:
                subprocess.Popen([str(self.app_path)] + sys.argv[1:])
            else:
                args = [sys.executable, sys.argv[0]] + sys.argv[1:]
                subprocess.Popen(args)
            sys.exit(0)
        else:
            try:
                if self.is_frozen:
                    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
                else:
                    args = [sys.executable, sys.argv[0]] + sys.argv[1:]
                    os.execv(sys.executable, args)
            except Exception as e:
                logger.warning(f"Error reiniciando con execv ({e}), saliendo para reinicio por systemd...")
                sys.exit(0)
