import argparse
import logging
import os
import signal
import threading
from datetime import UTC, datetime

import httpx
from sqlalchemy import or_, select

from .control import Workspace, control_sessions
from .provisioning import ErpPlatform, ProvisionError, run_steps

log = logging.getLogger(__name__)


def run_once(erp: ErpPlatform, stopping: threading.Event | None = None) -> dict[str, str]:
    with control_sessions()() as control:
        identifiers = list(
            control.scalars(
                select(Workspace.id)
                .where(
                    Workspace.status.in_(("provisioning", "failed")),
                    or_(
                        Workspace.next_provision_attempt_at <= datetime.now(UTC),
                        (Workspace.status == "provisioning") & Workspace.next_provision_attempt_at.is_(None),
                    ),
                )
                .order_by(Workspace.updated_at, Workspace.id)
                .limit(20)
            )
        )
    results = {}
    for identifier in identifiers:
        if stopping is not None and stopping.is_set():
            break
        try:
            result = run_steps(identifier, "provider-worker", erp, scheduled=True)
            results[identifier] = str(result["status"])
            log.info("workspace=%s status=%s", identifier, result["status"])
        except Exception as exc:
            results[identifier] = "failed"
            code = exc.code if isinstance(exc, ProvisionError) else type(exc).__name__
            log.warning("workspace=%s provisioning failed code=%s", identifier, code)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.provider_worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if os.environ.get("CRM_TENANCY") != "hosted":
        print("The provider worker only runs with CRM_TENANCY=hosted")
        return 2
    if os.environ.get("CRM_PROVIDER_WORKER_ENABLED") != "true":
        print("CRM_PROVIDER_WORKER_ENABLED is not true; provisioning stays disabled")
        return 0
    if os.environ.get("CRM_ENV") == "production":
        print("Hosted provisioning is not approved for production in this release")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            while not stopping.is_set():
                try:
                    results = run_once(ErpPlatform(client), stopping)
                    if args.once:
                        return 1 if "failed" in results.values() else 0
                except Exception as exc:
                    log.error("Provider cycle failed: %s", type(exc).__name__)
                    if args.once:
                        return 1
                stopping.wait(5)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
