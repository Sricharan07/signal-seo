"""Bounded SDK dev-server startup retries, separate from every action deadline."""

import asyncio

from temporalio.testing import WorkflowEnvironment


async def start_temporal(**options):
    for attempt in range(3):
        try:
            return await WorkflowEnvironment.start_local(**options)
        except RuntimeError as error:
            message = str(error).lower()
            if attempt == 2 or not any(
                text in message
                for text in (
                    "failed to start",
                    "5 seconds",
                    "never became ready",
                    "server startup",
                )
            ):
                raise
            await asyncio.sleep(2)
