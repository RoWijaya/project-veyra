import signal
from contextlib import contextmanager
from threading import Event, current_thread, main_thread

from django.conf import settings
from redis import Redis, RedisError

from .observability import logger

HEARTBEAT_TTL = 180
WORKERS = ("outbox", "billing", "refunds", "account_mail")


def heartbeat_key(name):
    return f"veyra:worker:{name}:progress"


def redis_client():
    return Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)


@contextmanager
def worker_shutdown():
    stopping = Event()

    def stop(signum, frame):
        stopping.set()

    registered = {}
    if current_thread() is main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            registered[signum] = signal.signal(signum, stop)
    try:
        yield stopping
    finally:
        for signum, handler in registered.items():
            signal.signal(signum, handler)


def run_worker(name, process_batch, *, once, interval):
    if once:
        process_batch(lambda: False)
        return
    with worker_shutdown() as stopping, redis_client() as redis:
        while not stopping.is_set():
            def checkpoint():
                if stopping.is_set():
                    return True
                try:
                    redis.set(heartbeat_key(name), b"ok", ex=HEARTBEAT_TTL)
                except RedisError:
                    logger.warning("Worker progress could not be recorded", extra={"event": f"worker.{name}.heartbeat_failed"})
                return False

            process_batch(checkpoint)
            checkpoint()
            stopping.wait(interval)
