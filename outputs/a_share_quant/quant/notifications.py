"""Account-scoped durable SMTP outbox; delivery is explicit and off by default."""

from __future__ import annotations

import hashlib
import logging
import os
import smtplib
import sqlite3
import ssl
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class NotificationOutbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=30)) as db, db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS notification_outbox(event_id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT "pending", attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT "", created REAL NOT NULL)'
            )

    def enqueue(self, event_id: str, title: str, body: str) -> bool:
        if (
            not event_id
            or len(event_id) > 200
            or not title
            or len(title) > 200
            or len(body) > 2000
        ):
            raise ValueError("invalid notification content")
        # Notifications carry only deliberately chosen generic text; never raw
        # exception strings, passwords, tokens, balances or recovery codes.
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            return (
                db.execute(
                    "INSERT OR IGNORE INTO notification_outbox(event_id,title,body,created) VALUES(?,?,?,?)",
                    (event_id, title, body, time.time()),
                ).rowcount
                == 1
            )

    def dispatch_one(
        self, sender: Callable[[str, str, str], None], *, now: float | None = None
    ) -> str:
        timestamp = time.time() if now is None else now
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                'UPDATE notification_outbox SET status="failed",lease=0,error="retry_exhausted" WHERE status="sending" AND lease<=? AND attempts>=5',
                (timestamp,),
            )
            row = db.execute(
                'SELECT event_id,title,body,attempts FROM notification_outbox WHERE status IN ("pending","sending") AND due<=? AND lease<=? AND attempts<5 ORDER BY created,event_id LIMIT 1',
                (timestamp, timestamp),
            ).fetchone()
            if row is None:
                return "empty"
            event_id, title, body, attempts = row
            db.execute(
                'UPDATE notification_outbox SET status="sending",lease=?,attempts=attempts+1 WHERE event_id=?',
                (timestamp + 60, event_id),
            )
        try:
            sender(event_id, title, body)
        except Exception as error:
            # SMTP exception messages/chains can contain credentials or recipients.
            LOGGER.exception(
                "Notification delivery failed: %s", type(error).__name__, exc_info=None
            )
            with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                db.execute(
                    "UPDATE notification_outbox SET status=?,due=?,lease=0,error=? WHERE event_id=?",
                    (
                        "failed" if attempts + 1 >= 5 else "pending",
                        timestamp + min(3600, 30 * 2**attempts),
                        type(error).__name__,
                        event_id,
                    ),
                )
            return "retry" if attempts + 1 < 5 else "failed"
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute(
                'UPDATE notification_outbox SET status="sent",lease=0,error="" WHERE event_id=?',
                (event_id,),
            )
        return "sent"

    def status(self) -> dict[str, object]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            rows = db.execute(
                "SELECT event_id,title,status,attempts,due,error FROM notification_outbox ORDER BY created DESC LIMIT 100"
            ).fetchall()
        return {
            "delivery_enabled": os.getenv("A_PI_NOTIFY_ENABLED") == "1",
            "events": [
                dict(
                    zip(
                        ("event_id", "title", "status", "attempts", "due", "error"), row
                    )
                )
                for row in rows
            ],
            "semantics": "at-least-once after lease recovery; SMTP delivery cannot guarantee exactly once",
        }


def smtp_sender() -> Callable[[str, str, str], None]:
    if os.getenv("A_PI_NOTIFY_ENABLED") != "1":
        raise ValueError("external notifications are disabled")
    required = ["HOST", "FROM", "TO"]
    values = {key: os.getenv("A_PI_NOTIFY_SMTP_" + key, "").strip() for key in required}
    if any(not value for value in values.values()):
        raise ValueError(
            "SMTP host/from/to are required; do not put credentials in chat"
        )
    for key in ["FROM", "TO"]:
        address = values[key]
        if (
            "\r" in address
            or "\n" in address
            or parseaddr(address)[1] != address
            or "@" not in address
        ):
            raise ValueError("invalid SMTP address")
    port = int(os.getenv("A_PI_NOTIFY_SMTP_PORT", "587"))
    if not 1 <= port <= 65535:
        raise ValueError("invalid SMTP port")

    def send(event_id: str, title: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = (
            values["FROM"],
            values["TO"],
            title,
        )
        message["Message-ID"] = (
            "<" + hashlib.sha256(event_id.encode()).hexdigest() + "@a-pi.local>"
        )
        message.set_content(body)
        transport: smtplib.SMTP = (
            smtplib.SMTP_SSL(
                values["HOST"], port, timeout=10, context=ssl.create_default_context()
            )
            if port == 465
            else smtplib.SMTP(values["HOST"], port, timeout=10)
        )
        with transport as client:
            if port != 465:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            username = os.getenv("A_PI_NOTIFY_SMTP_USERNAME", "")
            if username:
                client.login(username, os.getenv("A_PI_NOTIFY_SMTP_PASSWORD", ""))
            client.send_message(message)

    return send


def queue_decay_alerts(
    ledger_path: Path, run_id: str, result: Mapping[str, object]
) -> str:
    """Persist generic account-local warnings, without sending or exposing factors."""
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return "no_alerts"
    events = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        evaluation = candidate.get("evaluation")
        horizons = (
            evaluation.get("horizons") if isinstance(evaluation, Mapping) else None
        )
        if not isinstance(horizons, Mapping):
            continue
        for horizon, report in horizons.items():
            decay = report.get("decay_monitor") if isinstance(report, Mapping) else None
            if isinstance(decay, Mapping) and decay.get("status") == "alert":
                identity = hashlib.sha256(
                    f"{run_id}:{candidate.get('factor_key')}:{horizon}".encode()
                ).hexdigest()
                events.append("factor-decay:" + identity)
    if not events:
        return "no_alerts"
    try:
        box = NotificationOutbox(ledger_path.with_name("notifications.sqlite3"))
        for identity in events:
            box.enqueue(
                identity,
                "A-pi 因子衰减待复核",
                "已保存研究样本出现持续 IC 衰减诊断，请到因子评估报告查看截止日期与覆盖；这不是实时信号，不自动停用策略。",
            )
        return "queued"
    except (OSError, sqlite3.Error):
        return "queue_unavailable"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Explicit delivery of an account-scoped notification outbox"
    )
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument(
        "--send",
        action="store_true",
        help="authorize sending one queued event through configured SMTP",
    )
    args = parser.parse_args()
    outbox = NotificationOutbox(args.outbox)
    print(outbox.dispatch_one(smtp_sender()) if args.send else outbox.status())
