import time
import random
from typing import Callable, Type, Tuple, Optional
from functools import wraps

import requests

from .logger import get_logger

logger = get_logger(__name__)


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    retryable_statuses: Tuple[int, ...] = (429, 500, 502, 503, 504),
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        requests.ConnectionError,
        requests.Timeout,
    ),
):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    resp = func(*args, **kwargs)
                    if hasattr(resp, 'status_code') and resp.status_code in retryable_statuses:
                        if attempt < max_retries:
                            delay = _compute_delay(attempt, base_delay, max_delay, backoff)
                            logger.warning(
                                "HTTP %d on attempt %d/%d for %s - retrying in %.1fs",
                                resp.status_code, attempt + 1, max_retries + 1,
                                func.__name__, delay,
                            )
                            time.sleep(delay)
                            continue
                    return resp
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = _compute_delay(attempt, base_delay, max_delay, backoff)
                        logger.warning(
                            "%s on attempt %d/%d for %s - retrying in %.1fs: %s",
                            type(e).__name__, attempt + 1, max_retries + 1,
                            func.__name__, delay, e,
                        )
                        time.sleep(delay)
                    else:
                        raise
            if last_exc:
                raise last_exc
            return None
        return wrapper
    return decorator


def _compute_delay(attempt: int, base_delay: float, max_delay: float, backoff: float) -> float:
    delay = base_delay * (backoff ** attempt)
    jitter = random.uniform(0, delay * 0.1)
    return min(delay + jitter, max_delay)
