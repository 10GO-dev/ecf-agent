"""
Gestor de trabajos programados.
Utiliza APScheduler para ejecutar tareas periódicas.
"""

from typing import Callable, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger


class JobManager:
    """
    Gestor de trabajos programados para el agente ECF.
    
    Maneja la ejecución periódica de:
    - Polling de facturas nuevas
    - Reintentos de facturas fallidas
    - Limpieza de cola
    """

    def __init__(self):
        """Inicializa el gestor de trabajos."""
        self.scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,  # Combinar ejecuciones perdidas
                "max_instances": 1,  # Solo una instancia por job
            }
        )
        self._jobs: Dict[str, str] = {}  # nombre -> job_id
        logger.info("JobManager inicializado")

    def add_polling_job(
        self,
        func: Callable,
        interval_seconds: int = 30,
        name: str = "poll_invoices",
    ):
        """
        Agrega el trabajo de polling de facturas.
        
        Args:
            func: Función a ejecutar
            interval_seconds: Intervalo entre ejecuciones
            name: Nombre del trabajo
        """
        job = self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=name,
            name=name,
            replace_existing=True,
        )
        self._jobs[name] = job.id
        logger.info(f"Job '{name}' agregado: cada {interval_seconds}s")

    def add_retry_job(
        self,
        func: Callable,
        interval_seconds: int = 300,
        name: str = "retry_invoices",
    ):
        """
        Agrega el trabajo de reintentos.
        
        Args:
            func: Función a ejecutar
            interval_seconds: Intervalo entre ejecuciones
            name: Nombre del trabajo
        """
        job = self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=name,
            name=name,
            replace_existing=True,
        )
        self._jobs[name] = job.id
        logger.info(f"Job '{name}' agregado: cada {interval_seconds}s")

    def add_cleanup_job(
        self,
        func: Callable,
        interval_hours: int = 24,
        name: str = "cleanup_queue",
    ):
        """
        Agrega el trabajo de limpieza.
        
        Args:
            func: Función a ejecutar
            interval_hours: Intervalo en horas
            name: Nombre del trabajo
        """
        job = self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(hours=interval_hours),
            id=name,
            name=name,
            replace_existing=True,
        )
        self._jobs[name] = job.id
        logger.info(f"Job '{name}' agregado: cada {interval_hours}h")

    def start(self):
        """Inicia el scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler iniciado")

    def stop(self, wait: bool = True):
        """
        Detiene el scheduler.
        
        Args:
            wait: Si esperar a que terminen los jobs en ejecución
        """
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("Scheduler detenido")

    def pause_job(self, name: str):
        """Pausa un trabajo específico."""
        if name in self._jobs:
            self.scheduler.pause_job(self._jobs[name])
            logger.info(f"Job '{name}' pausado")

    def resume_job(self, name: str):
        """Reanuda un trabajo pausado."""
        if name in self._jobs:
            self.scheduler.resume_job(self._jobs[name])
            logger.info(f"Job '{name}' reanudado")

    def run_now(self, name: str):
        """Ejecuta un trabajo inmediatamente."""
        if name in self._jobs:
            job = self.scheduler.get_job(self._jobs[name])
            if job:
                job.modify(next_run_time=None)
                logger.info(f"Job '{name}' ejecutado inmediatamente")

    def get_status(self) -> Dict:
        """
        Obtiene el estado de todos los trabajos.
        
        Returns:
            Diccionario con estado de cada job
        """
        status = {
            "running": self.scheduler.running,
            "jobs": {},
        }
        
        for name, job_id in self._jobs.items():
            job = self.scheduler.get_job(job_id)
            if job:
                status["jobs"][name] = {
                    "next_run": str(job.next_run_time) if job.next_run_time else None,
                    "pending": job.pending,
                }
        
        return status

    def __enter__(self):
        """Context manager: iniciar."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: detener."""
        self.stop()
        return False
