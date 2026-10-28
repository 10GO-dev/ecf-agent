import os
import time
from typing import Any, Dict
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
            else:
                return {"success": False, "error": f"Comando no reconocido: {cmd}"}
        except Exception as e:
            logger.error(f"Error ejecutando comando {cmd}: {e}")
            return {"success": False, "error": str(e)}

    def _test_db(self, params: Dict[str, Any]) -> Dict[str, Any]:
        start = time.perf_counter()
        with self.agent.db_lock:
            with self.agent.db_connector:
                count = self.agent.db_connector.execute_scalar("SELECT 1")
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                db_cfg = getattr(self.agent.db_connector, "config", {}) or {}
                driver = db_cfg.get("driver") or type(self.agent.db_connector).__name__
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

    def _fetch_logs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        max_lines = params.get("lines", 100)
        log_file = self.agent.config.get("logging.file", "./logs/ecf-agent.log")

        if not os.path.exists(log_file):
            return {"success": True, "lines": ["No log file found at " + log_file]}

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                selected = all_lines[-max_lines:]
                return {"success": True, "lines": [line.rstrip() for line in selected]}
        except Exception as e:
            return {"success": False, "error": f"Error leyendo logs: {e}"}
