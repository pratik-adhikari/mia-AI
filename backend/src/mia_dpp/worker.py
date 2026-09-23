"""Persistent local worker for resumable crawl/research jobs."""

from __future__ import annotations

import asyncio
import logging
import os

from mia_dpp.mia import Mia

LOGGER = logging.getLogger("mia_dpp.worker")


async def run_worker() -> None:
    """Continuously advance queued research jobs without any serverless execution limit."""

    logging.basicConfig(
        level=os.getenv("MIA_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    poll_seconds = max(float(os.getenv("MIA_WORKER_POLL_SECONDS", "1")), 0.1)
    application = Mia()
    LOGGER.info("local research worker started; polling every %.1fs", poll_seconds)
    try:
        while True:
            job = application.context.catalogue.next_queued_background_job()
            if job is None:
                await asyncio.sleep(poll_seconds)
                continue
            LOGGER.info(
                "advancing job %s iteration=%s processed=%s/%s",
                job.id,
                job.metadata.get("iteration", 0),
                job.metadata.get("processedSources", 0),
                job.metadata.get("totalSources", 0),
            )
            try:
                updated = await application.run_deep_research(job.id, user_id=job.user_id)
            except Exception:
                LOGGER.exception("research job %s failed", job.id)
                await asyncio.sleep(poll_seconds)
                continue
            LOGGER.info(
                "job %s status=%s iteration=%s processed=%s/%s",
                updated.id,
                updated.status.value,
                updated.metadata.get("iteration", 0),
                updated.metadata.get("processedSources", 0),
                updated.metadata.get("totalSources", 0),
            )
    finally:
        await application.close()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        LOGGER.info("local research worker stopped")


if __name__ == "__main__":
    main()
