import os
import platform
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from loguru import logger
from .command_executor import CommandExecutor


class HeartbeatManager:
    def __init__(self, agent: Any):
        self.agent = agent
        self.command_executor = CommandExecutor(agent)
        self.start_time = time.time()
        self.agent_id = self._get_or_create_agent_id()
        self.current_interval = 30

    def _get_or_create_agent_id(self) -> str:
        data_dir = Path("./data")
        data_dir.mkdir(parents=True, exist_ok=True)
        id_file = data_dir / "agent_id"

        if id_file.exists():
            try:
                content = id_file.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception:
                pass

        new_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        try:
            id_file.write_text(new_id, encoding="utf-8")
        except Exception:
            pass

        return new_id

    def pulse(self):
        try:
            queue_size = 0
            if hasattr(self.agent, "retry_queue") and self.agent.retry_queue:
                try:
                    if hasattr(self.agent.retry_queue, "count"):
                        queue_size = self.agent.retry_queue.count()
                    elif hasattr(self.agent.retry_queue, "get_stats"):
                        queue_size = self.agent.retry_queue.get_stats().get("total_pending", 0)
                except Exception:
                    queue_size = 0

            status = "PAUSED" if getattr(self.agent, "paused", False) else "ONLINE"
            config_version = getattr(self.agent, "config_version", None)
            if not isinstance(config_version, str):
                cfg_obj = getattr(self.agent, "config", None)
                cfg_ver = getattr(cfg_obj, "config_version", None)
                config_version = cfg_ver if isinstance(cfg_ver, str) else None

            payload = {
                "agent_id": self.agent_id,
                "hostname": socket.gethostname(),
                "os": f"{platform.system()} {platform.release()}",
                "version": getattr(self.agent, "version", "1.1.8"),
                "status": status,
                "config_version": config_version,
                "queue_size": queue_size,
                "metrics": {
                    "uptime_seconds": int(time.time() - self.start_time),
                    "memory_mb": 0,
                    "last_poll_count": getattr(self.agent, "last_poll_count", 0),
                },
            }

            res = self.agent.api_client.send_heartbeat(payload)

            if res.get("config_outdated"):
                logger.info("Configuración remota desactualizada. Ejecutando recarga...")
                self.agent.reload_config()

            pending_commands = res.get("pending_commands", [])
            for cmd_info in pending_commands:
                cmd_id = cmd_info.get("id")
                cmd_name = cmd_info.get("command")
                cmd_params = cmd_info.get("params", {})
                result = self.command_executor.execute(cmd_name, cmd_params)

                if cmd_id:
                    self.agent.api_client.send_command_result({
                        "command_id": cmd_id,
                        "status": "COMPLETED" if result.get("success") else "FAILED",
                        "result": result,
                    })

            suggested_interval = res.get("suggested_interval")
            if suggested_interval and suggested_interval != self.current_interval:
                self._adjust_interval(suggested_interval)

        except Exception as e:
            logger.warning(f"Error durante pulso de heartbeat: {e}")

    def _adjust_interval(self, new_interval: int):
        self.current_interval = new_interval
        if hasattr(self.agent, "job_manager") and self.agent.job_manager:
            try:
                self.agent.job_manager.scheduler.reschedule_job(
                    "heartbeat",
                    trigger="interval",
                    seconds=new_interval,
                )
                logger.debug(f"Intervalo de heartbeat ajustado a {new_interval}s")
            except Exception as e:
                logger.debug(f"No se pudo reprogramar heartbeat: {e}")
