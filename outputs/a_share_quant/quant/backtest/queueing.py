from __future__ import annotations

import os
from typing import Any

BACKTEST_QUEUE_NAME = "a-pi-backtests"


def queue_mode() -> str:
    value = os.getenv("A_PI_BACKTEST_QUEUE", "local").strip().lower()
    if value in {"rq", "distributed"}:
        value = "redis"
    if value not in {"local", "redis"}:
        raise RuntimeError("A_PI_BACKTEST_QUEUE must be 'local' or 'redis'")
    return value


def redis_connection_url() -> str:
    return os.getenv("A_PI_REDIS_URL", os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")).strip()


def get_redis_backtest_queue() -> tuple[Any, Any]:
    """Return (RQ Queue, Redis connection), requiring Redis only in opt-in mode."""
    try:
        from redis import Redis
        from rq import Queue
        from rq.serializers import JSONSerializer
    except ImportError as error:
        raise RuntimeError("分布式回测队列缺少 redis/rq 依赖；安装 `.[redis-queue]` 后重试") from error
    try:
        connection = Redis.from_url(redis_connection_url(), socket_connect_timeout=2, socket_timeout=3)
        connection.ping()
        queue = Queue(BACKTEST_QUEUE_NAME, connection=connection, serializer=JSONSerializer)
        return queue, connection
    except Exception as error:
        raise RuntimeError(f"Redis 回测队列不可用：{str(error)[:240]}") from error


def redis_queue_counts(queue: Any, connection: Any) -> dict[str, int]:
    try:
        from rq.registry import StartedJobRegistry

        started = StartedJobRegistry(queue.name, connection=connection, serializer=queue.serializer)
        return {"queued": int(queue.count), "running": int(started.count)}
    except Exception as error:
        raise RuntimeError(f"读取 Redis 队列状态失败：{str(error)[:200]}") from error
