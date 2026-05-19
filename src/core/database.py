import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from pathlib import Path

from .config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    _instance: Optional['Database'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        db_path = config.get('database.path')
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._initialized = True
        self.init_schema()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        with self.get_connection() as conn:
            conn = conn.cursor()

            conn.execute("""
                CREATE TABLE IF NOT EXISTS bounties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT,
                    price REAL,
                    currency TEXT DEFAULT 'USD',
                    difficulty TEXT,
                    repository_url TEXT,
                    repository_name TEXT,
                    issue_url TEXT,
                    tags TEXT,
                    is_bounty INTEGER DEFAULT 0,
                    created_at TIMESTAMP,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'new',
                    classification TEXT,
                    confidence REAL,
                    assigned_agent TEXT,
                    processing_status TEXT DEFAULT 'new'
                )
            """)

            try:
                conn.execute("""
                    ALTER TABLE bounties ADD COLUMN is_bounty INTEGER DEFAULT 0
                """)
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bounty_id INTEGER NOT NULL,
                    branch_name TEXT,
                    commit_sha TEXT,
                    diff_content TEXT,
                    agent_type TEXT,
                    confidence_score REAL,
                    review_notes TEXT,
                    validation_passed INTEGER DEFAULT 0,
                    test_output TEXT,
                    workspace_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    review_action TEXT,
                    reviewer_comments TEXT,
                    pr_url TEXT,
                    FOREIGN KEY (bounty_id) REFERENCES bounties(id)
                )
            """)

            try:
                conn.execute("""ALTER TABLE review_queue ADD COLUMN validation_passed INTEGER DEFAULT 0""")
            except Exception:
                pass
            try:
                conn.execute("""ALTER TABLE review_queue ADD COLUMN test_output TEXT""")
            except Exception:
                pass
            try:
                conn.execute("""ALTER TABLE review_queue ADD COLUMN workspace_path TEXT""")
            except Exception:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS processing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bounty_id INTEGER,
                    agent_type TEXT,
                    action TEXT,
                    status TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bounties_status
                ON bounties(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bounties_external_id
                ON bounties(external_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_review_queue_status
                ON review_queue(status)
            """)

    def cleanup_stale_tasks(self, days: int = 30) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM bounties
                WHERE processing_status IN ('new', 'pending')
                AND fetched_at < datetime('now', ?)
            """, (f'-{days} days',))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} stale tasks older than {days} days")
            return deleted

    def cleanup_old_logs(self, days: int = 30) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM processing_logs
                WHERE created_at < datetime('now', ?)
            """, (f'-{days} days',))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def add_bounty(self, bounty_data: Dict[str, Any]) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO bounties (
                    external_id, title, description, price, currency,
                    difficulty, repository_url, repository_name, issue_url,
                    tags, is_bounty, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bounty_data.get('id'),
                bounty_data.get('title'),
                bounty_data.get('description'),
                bounty_data.get('price'),
                bounty_data.get('currency', 'USD'),
                bounty_data.get('difficulty'),
                bounty_data.get('repository_url'),
                bounty_data.get('repository_name'),
                bounty_data.get('issue_url'),
                bounty_data.get('tags'),
                bounty_data.get('is_bounty', 0),
                bounty_data.get('created_at')
            ))
            return cursor.lastrowid

    def get_pending_bounties(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM bounties
                WHERE status = 'new'
                ORDER BY price DESC
                LIMIT 20
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_bounty_classification(self, bounty_id: int, classification: str, confidence: float):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                UPDATE bounties
                SET classification = ?, confidence = ?, processing_status = 'classified'
                WHERE id = ?
            """, (classification, confidence, bounty_id))

    def update_bounty_status(self, bounty_id: int, status: str):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                UPDATE bounties SET processing_status = ? WHERE id = ?
            """, (status, bounty_id))

    def add_to_review_queue(self, review_data: Dict[str, Any]) -> int:
        import json
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO review_queue (
                    bounty_id, branch_name, commit_sha, diff_content,
                    agent_type, confidence_score, review_notes,
                    validation_passed, test_output, workspace_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review_data.get('bounty_id'),
                review_data.get('branch_name'),
                review_data.get('commit_sha'),
                review_data.get('diff_content'),
                review_data.get('agent_type'),
                review_data.get('confidence_score'),
                review_data.get('suggested_comment', ''),
                1 if review_data.get('validation_passed') else 0,
                json.dumps(review_data.get('test_output', [])),
                review_data.get('workspace_path', '')
            ))
            return cursor.lastrowid

    def get_pending_reviews(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, b.title, b.description, b.repository_url, b.price, b.repository_name, b.issue_url
                FROM review_queue r
                JOIN bounties b ON r.bounty_id = b.id
                WHERE r.status = 'pending'
                ORDER BY r.confidence_score DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def has_pending_review_for(self, bounty_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM review_queue
                WHERE bounty_id = ? AND status = 'pending'
            """, (bounty_id,))
            return cursor.fetchone() is not None

    def update_review(self, review_id: int, action: str, comments: str = None):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                UPDATE review_queue
                SET status = 'reviewed',
                    review_action = ?,
                    reviewer_comments = ?,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (action, comments, review_id))

    def update_review_pr(self, review_id: int, pr_url: str):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                UPDATE review_queue SET pr_url = ? WHERE id = ?
            """, (pr_url, review_id))

    def log_processing(self, bounty_id: int, agent_type: str, action: str, status: str, details: str = None):
        with self.get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO processing_logs (bounty_id, agent_type, action, status, details)
                VALUES (?, ?, ?, ?, ?)
            """, (bounty_id, agent_type, action, status, details))

    def get_processing_logs(self, bounty_id: int = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if bounty_id:
                cursor.execute("""
                    SELECT * FROM processing_logs
                    WHERE bounty_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (bounty_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM processing_logs
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_running_tasks_count(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM bounties
                WHERE processing_status = 'processing'
            """)
            return cursor.fetchone()[0]

    def reset_processing_bounties(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bounties
                SET processing_status = 'new'
                WHERE processing_status IN ('processing', 'classified')
            """)
            reset = cursor.rowcount
            conn.commit()
            if reset > 0:
                logger.info(f"Reset {reset} interrupted bounties back to 'new'")
            return reset

    def get_bounty_by_id(self, bounty_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bounties WHERE id = ?", (bounty_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_bounties(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bounties ORDER BY fetched_at DESC")
            return [dict(row) for row in cursor.fetchall()]


db = Database()