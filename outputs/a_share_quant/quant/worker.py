from __future__ import annotations

import os

from .backtest.queueing import BACKTEST_QUEUE_NAME, get_redis_backtest_queue


def main() -> None:
    if os.getenv("A_PI_BACKTEST_QUEUE", "local").strip().lower() not in {"redis", "rq", "distributed"}:
        raise SystemExit("Set A_PI_BACKTEST_QUEUE=redis before starting a distributed worker")
    queue, connection = get_redis_backtest_queue()
    try:
        from rq import Worker
        from rq.serializers import JSONSerializer
    except ImportError as error:
        raise SystemExit("Install the optional `.[redis-queue]` dependencies first") from error
    worker = Worker([queue], connection=connection, serializer=JSONSerializer)
    print(f"A-pi backtest worker listening on queue {BACKTEST_QUEUE_NAME!r}")
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
