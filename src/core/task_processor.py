import threading
import queue
import time
from typing import Dict, Any, Optional
from datetime import datetime

from ..core.database import db
from ..utils.logger import get_logger

logger = get_logger(__name__)


class TaskProcessor:
    _instance: Optional['TaskProcessor'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._queue = queue.Queue()
        self._status: Dict[str, Dict[str, Any]] = {}
        self._logs: Dict[str, list] = {}
        self._cancelled: set = set()
        self._worker_thread = None
        self._running = False
        self._shutdown_event = threading.Event()
        self._current_task_id: Optional[str] = None
        self._initialized = True

    def start(self):
        if self._running:
            return

        self._running = True
        self._shutdown_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name='task-processor')
        self._worker_thread.start()
        logger.info("Task processor started")

    def stop(self, timeout: int = 60):
        logger.info("Stopping task processor...")
        self._running = False
        self._shutdown_event.set()

        if self._current_task_id:
            logger.info(f"Waiting for current task {self._current_task_id} to finish (timeout: {timeout}s)")

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("Task processor thread did not finish within timeout")
            else:
                logger.info("Task processor thread finished")

        logger.info("Task processor stopped")

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def submit(self, bounty_id: int, process_fn):
        task_id = str(bounty_id)
        self._status[task_id] = {
            'bounty_id': bounty_id,
            'status': 'queued',
            'progress': 0,
            'step': 'Waiting to start...',
            'started_at': None,
            'completed_at': None,
            'error': None,
        }
        self._logs[task_id] = []
        self._queue.put((task_id, bounty_id, process_fn))
        logger.info(f"Task {task_id} submitted to queue")

    def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._status.get(task_id)

    def get_logs(self, task_id: str) -> list:
        return self._logs.get(task_id, [])

    def cancel(self, task_id: str) -> bool:
        """Cancel a task. If currently running, sets a flag to abort."""
        task_id = str(task_id)
        was_active = False
        if task_id in self._status and self._status[task_id].get('status') in ('queued', 'processing'):
            was_active = True
        self._cancelled.add(task_id)
        if task_id in self._status:
            self._status[task_id]['status'] = 'cancelled'
        logger.info(f"Task {task_id} cancelled")
        return was_active

    def _log(self, task_id: str, step: str, detail: str = ''):
        entry = {
            'timestamp': datetime.utcnow().isoformat() + '+00:00',
            'step': step,
            'detail': detail,
        }
        if task_id in self._logs:
            self._logs[task_id].append(entry)
        if task_id in self._status:
            self._status[task_id]['step'] = step

        bounty_id = self._status.get(task_id, {}).get('bounty_id')
        if bounty_id:
            try:
                db.log_processing(bounty_id, step, detail, 'processing')
            except Exception as e:
                logger.error(f"Failed to log to DB: {e}")

    def _update_progress(self, task_id: str, progress: int, step: str):
        if task_id in self._status:
            self._status[task_id]['progress'] = progress
            self._status[task_id]['step'] = step

    def _worker_loop(self):
        while self._running:
            try:
                task_id, bounty_id, process_fn = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if task_id in self._cancelled:
                self._cancelled.discard(task_id)
                logger.info(f"Task {task_id} was cancelled, skipping")
                self._queue.task_done()
                continue

            try:
                self._process_task(task_id, bounty_id, process_fn)
            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}")
                if task_id in self._status:
                    self._status[task_id]['status'] = 'error'
                    self._status[task_id]['error'] = str(e)
                    self._status[task_id]['completed_at'] = datetime.utcnow().isoformat() + '+00:00'

            self._queue.task_done()

    def _process_task(self, task_id: str, bounty_id: int, process_fn):
        if task_id in self._cancelled:
            self._cancelled.discard(task_id)
            logger.info(f"Task {task_id} cancelled before start")
            return

        self._current_task_id = task_id
        self._update_progress(task_id, 5, 'Starting...')
        self._status[task_id]['status'] = 'processing'
        self._status[task_id]['started_at'] = datetime.utcnow().isoformat() + '+00:00'

        self._log(task_id, 'start', f'Processing bounty {bounty_id}')

        db.update_bounty_status(bounty_id, 'processing')

        self._update_progress(task_id, 10, 'Classifying task...')
        self._log(task_id, 'classify', 'Analyzing task complexity and routing to agent')

        self._update_progress(task_id, 20, 'Cloning repository...')
        self._log(task_id, 'clone', 'Downloading repository to temp directory')

        self._update_progress(task_id, 40, 'Generating fix...')
        self._log(task_id, 'generate', 'AI agent is analyzing code and generating fix')

        self._update_progress(task_id, 60, 'Applying changes...')
        self._log(task_id, 'apply', 'Writing changes and creating local commit')

        self._update_progress(task_id, 75, 'Running validation...')
        self._log(task_id, 'validate', 'Running tests and lint checks')

        self._update_progress(task_id, 85, 'Code review...')
        self._log(task_id, 'review', 'Reviewing code quality and best practices')

        self._update_progress(task_id, 95, 'Queueing for review...')
        self._log(task_id, 'queue', 'Adding to review queue for human approval')

        if task_id in self._cancelled:
            self._cancelled.discard(task_id)
            logger.info(f"Task {task_id} cancelled during processing")
            self._status[task_id]['status'] = 'cancelled'
            self._log(task_id, 'cancelled', 'Task was cancelled by user')
            db.update_bounty_status(bounty_id, 'new')
            self._current_task_id = None
            return

        try:
            result = process_fn(bounty_id)
        except KeyboardInterrupt:
            logger.info(f"Task {task_id} interrupted by shutdown signal")
            self._status[task_id]['status'] = 'error'
            self._status[task_id]['error'] = 'Interrupted by shutdown'
            self._log(task_id, 'interrupted', 'Shutdown requested, task interrupted')
            db.update_bounty_status(bounty_id, 'failed')
            result = None

        if result:
            if result.get('model_used'):
                self._log(task_id, 'model', f"Model: {result['model_used']}")
                self._status[task_id]['model_used'] = result['model_used']
            if result.get('token_stats'):
                stats = result['token_stats']
                self._log(task_id, 'tokens', f"Prompt: {stats.get('prompt_tokens', '?')} | Completion: {stats.get('completion_tokens', '?')} | Total: {stats.get('total_tokens', '?')}")
                self._status[task_id]['token_stats'] = stats
            if result.get('duration'):
                self._log(task_id, 'duration', f"Processing time: {result['duration']:.1f}s")
                self._status[task_id]['duration'] = result['duration']

        if result and result.get('success'):
            self._update_progress(task_id, 100, 'Complete - queued for review')
            self._status[task_id]['status'] = 'completed'
            self._log(task_id, 'complete', 'Task processed successfully, awaiting human review')
        else:
            error = result.get('error', 'Unknown error') if result else 'No result'
            self._status[task_id]['status'] = 'error'
            self._status[task_id]['error'] = error
            self._log(task_id, 'error', error)

        self._status[task_id]['completed_at'] = datetime.utcnow().isoformat() + '+00:00'
        self._current_task_id = None

    def get_active_tasks(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in self._status.items() if v.get('status') in ('queued', 'processing')}

    def get_completed_tasks(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in self._status.items() if v.get('status') in ('completed', 'error')}


task_processor = TaskProcessor()
