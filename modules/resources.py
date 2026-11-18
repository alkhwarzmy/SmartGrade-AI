"""Bound CPU-heavy operations per service; overload remains explicit and retryable."""

from functools import wraps
from threading import BoundedSemaphore
from modules.auth import CapacityError

_HEAVY = BoundedSemaphore(2)


def bounded_processing(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _HEAVY.acquire(timeout=5):
            raise CapacityError("الخدمة تعالج طلبات أخرى؛ أعد المحاولة")
        try:
            return function(*args, **kwargs)
        finally:
            _HEAVY.release()

    return wrapped
