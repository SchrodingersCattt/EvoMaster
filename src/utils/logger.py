import json
import logging
import logging.handlers
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any


class LogContext:
    """当前 run 的日志上下文，由 Worker 在 run 开始/结束时 bind/clear，便于用 session_id/task_id 检索一次 run 的全部日志。"""

    _session_id: ContextVar[str | None] = ContextVar(
        'log_context_session_id', default=None
    )
    _task_id: ContextVar[str | None] = ContextVar('log_context_task_id', default=None)

    @classmethod
    def bind(cls, session_id: str | None, task_id: str | None) -> None:
        """设置当前上下文的 session_id/task_id，此后同上下文内的日志会带上该标识。"""
        cls._session_id.set(session_id or '-')
        cls._task_id.set(task_id or '-')

    @classmethod
    def clear(cls) -> None:
        """清除当前上下文的 session_id/task_id。"""
        cls._session_id.set(None)
        cls._task_id.set(None)

    @staticmethod
    def session_id_from_path(path: str) -> str | None:
        """从 /api/v1/chat/sessions/<segment>/... 解析 session_id；list、run_status 等无 session 返回 None。"""
        if '/chat/sessions/' not in path:
            return None
        parts = path.rstrip('/').split('/')
        if 'sessions' not in parts:
            return None
        idx = parts.index('sessions')
        if idx + 1 >= len(parts):
            return None
        segment = parts[idx + 1]
        if segment in ('list', 'run_status'):
            return None
        return segment


class LogContextFilter(logging.Filter):
    """将 contextvars 中的 session_id/task_id 注入到每条 LogRecord，便于检索。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = LogContext._session_id.get(None) or '-'
        record.task_id = LogContext._task_id.get(None) or '-'
        return True


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record):
        log_entry = {
            'timestamp': time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(record.created)
            ),
            'level': record.levelname,
            'logger': record.name,
            'module': record.module,
            'filename': record.filename,
            'lineno': record.lineno,
            'function': record.funcName,
            'message': record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)

        # Add extra fields if present
        for key, value in record.__dict__.items():
            if key not in {
                'name',
                'msg',
                'args',
                'levelname',
                'levelno',
                'pathname',
                'filename',
                'module',
                'exc_info',
                'exc_text',
                'stack_info',
                'lineno',
                'funcName',
                'created',
                'msecs',
                'relativeCreated',
                'thread',
                'threadName',
                'processName',
                'process',
                'getMessage',
            }:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False)


class LoggingConfig:
    """Logging configuration settings."""

    # Default log settings
    DEFAULT_LOG_LEVEL = 'INFO'
    DEFAULT_LOG_DIR = '/data/logs'
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB
    DEFAULT_BACKUP_COUNT = 5
    DEFAULT_CONSOLE_OUTPUT = True
    DEFAULT_JSON_FORMAT = False

    @classmethod
    def get_main_app_config(cls) -> dict[str, Any]:
        """Get logging configuration for main application."""
        return {
            'log_level': os.getenv('LOG_LEVEL', cls.DEFAULT_LOG_LEVEL),
            'log_file': os.path.join(
                os.getenv('LOG_DIR', cls.DEFAULT_LOG_DIR), 'matmaster-evo.log'
            ),
            'max_bytes': int(os.getenv('LOG_MAX_BYTES', cls.DEFAULT_MAX_BYTES)),
            'backup_count': int(
                os.getenv('LOG_BACKUP_COUNT', cls.DEFAULT_BACKUP_COUNT)
            ),
            'console_output': os.getenv('LOG_CONSOLE', 'true').lower() == 'true',
            'json_format': os.getenv('LOG_JSON_FORMAT', 'false').lower() == 'true',
        }

    @classmethod
    def get_worker_config(cls) -> dict[str, Any]:
        """Get logging configuration for agent worker."""
        return {
            'log_level': os.getenv('LOG_LEVEL', cls.DEFAULT_LOG_LEVEL),
            'log_file': os.path.join(
                os.getenv('LOG_DIR', cls.DEFAULT_LOG_DIR),
                'matmaster-evo-worker.log',
            ),
            'max_bytes': int(os.getenv('LOG_MAX_BYTES', cls.DEFAULT_MAX_BYTES)),
            'backup_count': int(
                os.getenv('LOG_BACKUP_COUNT', cls.DEFAULT_BACKUP_COUNT)
            ),
            'console_output': os.getenv('LOG_CONSOLE', 'true').lower() == 'true',
            'json_format': os.getenv('LOG_JSON_FORMAT', 'false').lower() == 'true',
        }


def setup_logging(
    log_level: str = None,
    log_file: str = None,
    max_bytes: int = None,
    backup_count: int = None,
    console_output: bool = True,
    json_format: bool = False,
) -> None:
    """Set up logging configuration with rotation support.

    Args:
        log_level: Logging level.
        log_file: Path to log file.
        max_bytes: Maximum bytes per log file before rotation.
        backup_count: Number of backup files to keep.
        console_output: Whether to output to console.
        json_format: Whether to use JSON format for file logging.
    """
    # Use environment variables or defaults
    log_level = log_level or os.getenv('LOG_LEVEL', 'INFO')
    log_file = log_file or os.getenv('LOG_FILE', '/data/logs/bohrium-agents.log')
    max_bytes = max_bytes or int(os.getenv('LOG_MAX_BYTES', '10485760'))  # 10MB
    backup_count = backup_count or int(os.getenv('LOG_BACKUP_COUNT', '5'))
    json_format = json_format or os.getenv('LOG_JSON_FORMAT', 'false').lower() == 'true'

    # Create log directory if it doesn't exist
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Remove any existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 每条日志带上 session_id/task_id，便于按一次 run 检索（由 LogContextFilter 注入）
    _log_context_filter = LogContextFilter()

    # Create formatters
    _text_fmt = (
        '%(asctime)s - %(name)s - %(levelname)s - '
        '[session_id=%(session_id)s task_id=%(task_id)s] - '
        '%(filename)s:%(lineno)d - %(message)s'
    )
    if json_format:
        file_formatter = JsonFormatter()
    else:
        file_formatter = logging.Formatter(_text_fmt)

    console_formatter = logging.Formatter(_text_fmt)

    # Set root logger level
    root_logger.setLevel(getattr(logging, log_level))

    # Add file handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, log_level))
    file_handler.addFilter(_log_context_filter)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Add console handler if requested
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level))
        console_handler.addFilter(_log_context_filter)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # Configure specific loggers
    for logger_name, logger_level in {
        'uvicorn': log_level,
        'uvicorn.error': log_level,
        'uvicorn.access': log_level,
        'fastapi': log_level,
        'bohrium_agents': log_level,
        'bohr': log_level,
    }.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, logger_level))

    # Log initial message
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured - Level: {log_level}, File: {log_file}, Console: {console_output}, JSON: {json_format}"
    )
