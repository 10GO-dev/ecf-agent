import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from loguru import logger


class CommandExecutor:
    def __init__(self, agent: Any):
        self.agent = agent

    def execute(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        cmd = (command or "").upper().strip()
        logger.info(f"Ejecutando comando remoto: {cmd} con params={params}")

        try:
            if cmd == "TEST_DB":
                return self._test_db(params)
            elif cmd == "FORCE_POLL":
                return self._force_poll()
            elif cmd == "FORCE_SYNC_STATUS":
                return self._force_sync_status()
            elif cmd == "RELOAD_CONFIG":
                return self._reload_config()
            elif cmd == "PAUSE":
                self.agent.paused = True
                return {"success": True, "status": "PAUSED"}
            elif cmd == "RESUME":
                self.agent.paused = False
                return {"success": True, "status": "ACTIVE"}
            elif cmd == "FETCH_LOGS":
                return self._fetch_logs(params)
            elif cmd in ("TRIGGER_UPDATE", "UPDATE_VERSION"):
                return self._trigger_update(params)
            elif cmd in ("PREVIEW_EXTRACTION", "DRY_RUN_DEBUG"):
                return self._preview_extraction(params)
            else:
                return {"success": False, "error": f"Comando no reconocido: {cmd}"}
        except Exception as e:
            logger.error(f"Error ejecutando comando {cmd}: {e}")
            return {"success": False, "error": str(e)}

    def _test_db(self, params: Dict[str, Any]) -> Dict[str, Any]:
        start = time.perf_counter()
        with self.agent.db_lock:
            with self.agent.db_connector:
                db_cfg = getattr(self.agent.db_connector, "config", {}) or {}
                driver = str(db_cfg.get("driver") or type(self.agent.db_connector).__name__).lower()
                query = "SELECT 1 FROM DUAL" if "oracle" in driver else "SELECT 1"
                count = self.agent.db_connector.execute_scalar(query)
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                db_name = db_cfg.get("database", "unknown")
                return {
                    "success": True,
                    "result": count,
                    "latency_ms": elapsed_ms,
                    "driver": driver,
                    "database": db_name,
                }

    def _force_poll(self) -> Dict[str, Any]:
        count = self.agent.poll_invoices()
        return {"success": True, "processed_count": count or 0}

    def _force_sync_status(self) -> Dict[str, Any]:
        count = self.agent.sync_statuses()
        return {"success": True, "synced_count": count or 0}

    def _reload_config(self) -> Dict[str, Any]:
        reloaded = self.agent.reload_config()
        return {"success": reloaded}

    def _find_log_files(self) -> List[Path]:
        candidates = []

        cfg_file = self.agent.config.get("logging.file")
        if cfg_file:
            candidates.append(Path(cfg_file))

        base_dirs = []
        try:
            base_dirs.append(Path.cwd())
        except Exception:
            pass

        try:
            base_dirs.append(Path(sys.executable).parent)
        except Exception:
            pass

        if hasattr(self.agent, "updater") and hasattr(self.agent.updater, "app_path"):
            try:
                base_dirs.append(self.agent.updater.app_path.parent)
            except Exception:
                pass

        log_names = [
            "logs/service.log",
            "logs/service-err.log",
            "logs/service-error.log",
            "logs/service-out.log",
            "logs/ecf-agent.log",
            "service.log",
            "service-err.log",
            "service-error.log",
            "service-out.log",
            "ecf-agent.log",
        ]

        for b in base_dirs:
            for n in log_names:
                candidates.append(b / n)
            logs_dir = b / "logs"
            if logs_dir.is_dir():
                try:
                    for p in logs_dir.glob("*.log"):
                        candidates.append(p)
                except Exception:
                    pass

        existing = []
        seen = set()
        for p in candidates:
            try:
                resolved = p.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if resolved.is_file():
                    existing.append(resolved)
            except Exception:
                continue

        existing.sort(key=lambda f: (f.stat().st_size > 0, f.stat().st_mtime), reverse=True)
        return existing

    def _fetch_logs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            max_lines = max(1, min(int(params.get("lines", 100)), 5000))
        except (ValueError, TypeError):
            max_lines = 100

        level_filter = str(params.get("level") or "").strip().upper()
        search_filter = str(params.get("search") or "").strip().lower()

        found_files = self._find_log_files()

        if not found_files:
            return {
                "success": True,
                "lines": ["No se encontró ningún archivo de registros (.log) en ./logs ni en el directorio del agente."],
                "available_files": [],
            }

        target_file = found_files[0]
        try:
            st = target_file.stat()
            file_size_bytes = st.st_size
            if file_size_bytes < 1024:
                size_formatted = f"{file_size_bytes} B"
            elif file_size_bytes < 1024 * 1024:
                size_formatted = f"{file_size_bytes / 1024:.1f} KB"
            else:
                size_formatted = f"{file_size_bytes / (1024 * 1024):.2f} MB"

            from datetime import datetime
            modified_at = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                all_raw_lines = f.readlines()

            total_lines = len(all_raw_lines)
            filtered = []
            for raw_line in all_raw_lines:
                line = raw_line.rstrip()
                if not line:
                    continue
                if level_filter and level_filter not in line.upper():
                    continue
                if search_filter and search_filter not in line.lower():
                    continue
                filtered.append(line)

            selected = filtered[-max_lines:]
            msg = None
            if not selected:
                msg = f"El archivo {target_file.name} no contiene registros con los filtros seleccionados." if (level_filter or search_filter) else f"El archivo de logs ({target_file.name}) está vacío."
                selected = [msg]

            return {
                "success": True,
                "lines": selected,
                "file": target_file.name,
                "file_path": str(target_file),
                "file_size": size_formatted,
                "file_size_bytes": file_size_bytes,
                "total_lines": total_lines,
                "returned_lines": len(selected) if msg is None else 0,
                "modified_at": modified_at,
                "available_files": [f.name for f in found_files[:5]],
            }
        except Exception as e:
            return {"success": False, "error": f"Error leyendo logs de {target_file}: {e}"}

    def _trigger_update(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target_version = params.get("target_version") or params.get("version")
        with self.agent.db_lock:
            result = self.agent.updater.check_and_update(
                force=True,
                restart_immediately=False,
                target_version=target_version,
            )
            if result.get("needs_restart"):
                import threading
                threading.Timer(2.0, self.agent.updater.restart).start()
            return result

    def _preview_extraction(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(params.get("limit", 1)), 5))
        except Exception:
            limit = 1

        try:
            self.agent.reload_config()
        except Exception:
            pass

        with self.agent.db_lock:
            with self.agent.db_connector:
                invoices_to_send, invoice_ids, _ = self.agent._extract_and_prepare_batch(
                    limit, filter_retry=False
                )

                preview_invoices = []
                for idx, inv in enumerate(invoices_to_send[:limit]):
                    inv_id = str(invoice_ids[idx]) if idx < len(invoice_ids) else ""
                    inv_data = inv.get("invoice_data", {}) if isinstance(inv, dict) else {}
                    preview_invoices.append({
                        "id": inv_id,
                        "ecf": inv.get("ecf", ""),
                        "ecf_type": inv.get("ecf_type", ""),
                        "rnc_buyer": inv.get("rnc_buyer", ""),
                        "total_amount": inv.get("total_amount", ""),
                        "subquery_counts": {
                            "details": len(inv_data.get("Detalles") or []),
                            "taxes": len(inv_data.get("ImpuestosAdicionales") or []),
                            "payments": len(inv_data.get("FormasPago") or []),
                            "reference_info": 1 if inv_data.get("InformacionReferencia") else 0,
                        },
                        "invoice_data": inv_data,
                    })

                return {
                    "success": True,
                    "invoices_count": len(preview_invoices),
                    "preview_invoices": preview_invoices,
                    "message": (
                        f"Simulación completada: {len(preview_invoices)} comprobante(s) extraído(s) en modo solo-lectura."
                        if preview_invoices
                        else "La consulta principal no devolvió comprobantes pendientes en este momento."
                    ),
                }
