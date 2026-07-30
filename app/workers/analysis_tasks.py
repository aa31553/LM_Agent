import logging
import signal
import time

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.analysis_job_service import (
    AnalysisJobService,
    cleanup_expired_analysis_files,
    run_analysis_job,
)
from app.services.spreadsheet_profile_job_service import (
    SpreadsheetProfileJobService,
    run_spreadsheet_profile,
)

logger = logging.getLogger(__name__)
_stop_requested = False


def _request_stop(_signum, _frame) -> None:
    global _stop_requested
    _stop_requested = True


def run_worker() -> None:
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    logger.info("analysis_worker_started")
    next_cleanup_at = 0.0
    while not _stop_requested:
        now = time.monotonic()
        if now >= next_cleanup_at:
            deleted = cleanup_expired_analysis_files()
            if deleted:
                logger.info(
                    "expired_analysis_files_deleted",
                    extra={"file_count": deleted},
                )
            next_cleanup_at = now + 3600
        with SessionLocal() as db:
            source = SpreadsheetProfileJobService(db).claim_next()
            source_id = source.id if source is not None else None
        if source_id is not None:
            logger.info(
                "spreadsheet_profile_claimed",
                extra={"analysis_file_id": str(source_id)},
            )
            run_spreadsheet_profile(source_id)
            continue
        with SessionLocal() as db:
            job = AnalysisJobService(db).claim_next()
            job_id = job.id if job is not None else None
        if job_id is None:
            time.sleep(settings.worker_poll_interval_seconds)
            continue
        logger.info(
            "analysis_job_claimed",
            extra={"analysis_job_id": str(job_id)},
        )
        run_analysis_job(job_id)
    logger.info("analysis_worker_stopped")


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
