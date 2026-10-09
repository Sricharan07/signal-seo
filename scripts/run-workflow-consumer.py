#!/usr/bin/env python3
"""Run the durable workflow command consumer under an external supervisor."""

import asyncio
import os
import sys

from signal_core.workflow_consumer_health import (
    WorkflowConsumerHealth,
    WorkflowConsumerHealthServer,
)
from signal_core.workflow_consumer_runtime import (
    JsonLineConsumerObserver,
    WorkflowConsumerConfigurationError,
    WorkflowConsumerRuntimeConfig,
    install_stop_handlers,
    run_workflow_consumer,
)


async def main() -> int:
    config = WorkflowConsumerRuntimeConfig.from_environment(os.environ)
    stop = asyncio.Event()
    observer = JsonLineConsumerObserver(sys.stdout)
    health = WorkflowConsumerHealth(
        release=config.release,
        stale_after_seconds=config.health_stale_seconds,
    )

    def request_drain() -> None:
        if health.mark_draining():
            try:
                observer.lifecycle("consumer_draining")
            except Exception:
                return

    install_stop_handlers(stop, on_stop=request_drain)
    async with WorkflowConsumerHealthServer(
        health,
        host=config.health_host,
        port=config.health_port,
    ):
        await run_workflow_consumer(
            config,
            stop=stop,
            observer=observer,
            health=health,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except WorkflowConsumerConfigurationError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as error:
        print(f"Workflow consumer failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
