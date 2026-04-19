"""CronScheduler: asyncio-based cron trigger for the WorkflowEngine.

Uses croniter to compute next trigger times and sleeps until then.
Each cron entry defines a workflow name and an input factory callable.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from importlib import import_module

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field

from .engine import WorkflowEngine

log = logging.getLogger(__name__)


class CronEntry(BaseModel):
    """A single cron-scheduled workflow trigger."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cron_expr: str = Field(description="Standard cron expression (5 fields)")
    workflow_name: str = Field(description="Name of registered workflow to trigger")
    input_factory: Callable[[], BaseModel] = Field(
        description="Called each time the cron fires to produce a fresh input"
    )


class CronScheduler:
    """Schedules workflow runs on cron expressions.

    Usage::

        scheduler = CronScheduler(engine)
        scheduler.add("*/5 * * * *", "ContentPipeline", lambda: MyInput(...))
        await scheduler.start()
        # ... on shutdown:
        await scheduler.stop()
    """

    def __init__(self, engine: WorkflowEngine) -> None:
        self._engine = engine
        self._entries: list[CronEntry] = []
        self._task: asyncio.Task[None] | None = None

    def add(
        self,
        cron_expr: str,
        workflow_name: str,
        input_factory: Callable[[], BaseModel],
    ) -> None:
        """Register a cron-scheduled workflow trigger."""
        self._entries.append(
            CronEntry(
                cron_expr=cron_expr,
                workflow_name=workflow_name,
                input_factory=input_factory,
            )
        )
        log.debug("scheduler: added cron '%s' for workflow '%s'", cron_expr, workflow_name)

    async def start(self) -> None:
        """Start the scheduler loop as a background asyncio task."""
        if not self._entries:
            log.debug("scheduler: no entries, not starting")
            return
        if self._task is not None and not self._task.done():
            log.warning("scheduler: already running")
            return
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")
        log.info("scheduler: started with %d entry/entries", len(self._entries))

    async def stop(self) -> None:
        """Cancel the scheduler loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("scheduler: stopped")

    async def _loop(self) -> None:
        """Main scheduling loop: sleep until the next trigger, then fire."""
        while True:
            now = datetime.now(timezone.utc)

            # Compute next trigger time for each entry.
            next_fire_times: list[tuple[datetime, CronEntry]] = []
            for entry in self._entries:
                cron = croniter(entry.cron_expr, now)
                next_dt: datetime = cron.get_next(datetime)
                next_fire_times.append((next_dt, entry))

            if not next_fire_times:
                await asyncio.sleep(60)
                continue

            # Sleep until the nearest trigger time.
            nearest_dt = min(dt for dt, _ in next_fire_times)
            sleep_sec = max(0.0, (nearest_dt - now).total_seconds())
            log.debug("scheduler: sleeping %.1fs until next trigger", sleep_sec)

            try:
                await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                raise

            # Re-read clock after sleep and fire entries that are now due (±1s).
            now_after = datetime.now(timezone.utc)
            for next_dt, entry in next_fire_times:
                remaining = (next_dt - now_after).total_seconds()
                if remaining <= 1.0:
                    await self._fire(entry)

    async def _fire(self, entry: CronEntry) -> None:
        """Trigger a workflow run for a cron entry."""
        workflow_id = uuid.uuid4()
        workflow_input = entry.input_factory()
        try:
            # Create the Workflow DB row first; abort if it fails.
            await self._create_workflow_row(entry, workflow_id, workflow_input)
        except Exception as exc:
            log.error(
                "scheduler: failed to create DB row for '%s', skipping trigger: %s",
                entry.workflow_name, exc,
            )
            return

        try:
            await self._engine.trigger(entry.workflow_name, workflow_input, workflow_id)
            log.info(
                "scheduler: triggered '%s' workflow_id=%s",
                entry.workflow_name, workflow_id,
            )
        except Exception as exc:
            log.error(
                "scheduler: failed to trigger '%s': %s", entry.workflow_name, exc
            )

    async def _create_workflow_row(
        self, entry: CronEntry, workflow_id: uuid.UUID, input_obj: BaseModel
    ) -> None:
        """Create the Workflow DB row before triggering the engine.

        Raises on failure so _fire can abort the trigger.
        """
        import app.db as _db

        session_maker = _db.async_session_maker
        if session_maker is None:
            raise RuntimeError("DB not initialized; cannot create Workflow row")

        enums_module = import_module("app.models.enums")
        WorkflowStatus = getattr(enums_module, "WorkflowStatus")
        WorkflowType = getattr(enums_module, "WorkflowType")
        WorkflowInputSchema = getattr(
            import_module("app.models.json_schemas"),
            "WorkflowInputSchema",
        )
        # Only create a typed Workflow row for known input schemas.
        if not isinstance(input_obj, WorkflowInputSchema):
            log.debug(
                "scheduler: input is not WorkflowInputSchema (%s), skipping DB row",
                type(input_obj).__name__,
            )
            return

        Workflow = getattr(import_module("app.models.workflow"), "Workflow")
        async with session_maker() as session:
            wf = Workflow(
                id=workflow_id,
                workflow_type=WorkflowType.CONTENT_PIPELINE,
                input_json=input_obj,
                status=WorkflowStatus.PENDING,
            )
            session.add(wf)
            await session.commit()
