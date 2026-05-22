# scheduler.py
# APScheduler background scheduler for daily ranking fetch at 21:00

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import database
import tracker

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _daily_job() -> None:
    """Scheduled job: fetch and save all rankings (iOS + Google Play). Called by APScheduler."""
    logger.info("Scheduled job triggered at %s", datetime.now().isoformat())
    try:
        results = tracker.fetch_and_save_all()
        ios_count = len(results.get("ios", {}))
        google_count = len(results.get("google", {}))
        logger.info("Scheduled fetch completed. iOS: %d games, Google Play: %d games",
                     ios_count, google_count)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Scheduled fetch failed: %s", exc, exc_info=True)


def start_scheduler() -> None:
    """
    Start the background scheduler.

    On startup, check if today's rankings have already been fetched.
    If not, trigger an immediate fetch to backfill today's data.
    Then schedule the daily 21:00 job.
    """
    if _scheduler.running:
        logger.warning("Scheduler is already running; skipping start.")
        return

    # Add daily cron job at configured hour:minute (China Standard Time)
    _scheduler.add_job(
        _daily_job,
        trigger=CronTrigger(
            hour=config.SCHEDULE_HOUR,
            minute=config.SCHEDULE_MINUTE,
            timezone="Asia/Shanghai",
        ),
        id="daily_ranking_fetch",
        name="Daily Game Ranking Fetch",
        replace_existing=True,
        misfire_grace_time=3600,  # allow up to 1 hour late execution
    )

    _scheduler.start()
    logger.info(
        "Scheduler started. Daily fetch scheduled at %02d:%02d CST.",
        config.SCHEDULE_HOUR,
        config.SCHEDULE_MINUTE,
    )

    # Backfill if today not yet checked
    if not database.has_checked_today():
        logger.info("No ranking data for today — triggering immediate fetch.")
        import threading
        thread = threading.Thread(target=_daily_job, daemon=True)
        thread.start()


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
