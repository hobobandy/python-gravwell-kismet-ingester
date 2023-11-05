import asyncio
import contextlib
import functools


def suppress_asyncio_cancelled_error(func):
    """Decorator to reduce code repetition. Suppresses task cancellation exception."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Suppressing exception, no concerns since in control of the asyncio loop
        with contextlib.suppress(asyncio.CancelledError):
            await func(*args, **kwargs)

    return wrapper
