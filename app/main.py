from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from storage.db import init_db
from storage.apps_repo import AppRepo
from storage.jobs_repo import JobRepo

from .jobs import bulk_jobs, job_runs, jobs
from .outreach_worker import OutreachWorker
from .routes import applications, cold, dashboard, finder, outreach, placeholders, review, settings
from .routes import jobs as jobs_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    jobs.recover()
    bulk_jobs.recover()
    job_runs.recover()
    JobRepo().seed_watchlist_once()
    AppRepo().seed_answers()
    worker = OutreachWorker.start_if_enabled()
    yield
    if worker:
        worker.stop()


app = FastAPI(title="Job Hunt", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(review.router)
app.include_router(dashboard.router)
app.include_router(finder.router)
app.include_router(jobs_routes.router)
app.include_router(applications.router)
app.include_router(cold.router)  # before outreach: /outreach/{contact_id} would catch it
app.include_router(outreach.router)
app.include_router(settings.router)
app.include_router(placeholders.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/review")
