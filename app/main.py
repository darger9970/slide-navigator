from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Annotated, AsyncGenerator, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, load_settings
from app.database import connect_database, initialize_database
from app.library_management import change_assignment, create_tag, deletion
from app import enrichment
from app import thumbnails
from app import taxonomy
from app import search, work_jobs, assembly
import threading
from app.ingestion import (
    backfill_slide_titles,
    get_deck,
    get_import_job,
    get_slide,
    get_thumbnail_path,
    list_decks,
    list_filter_options,
    list_import_jobs,
    list_slides,
    process_import_job,
    recover_imports,
    stage_browser_uploads,
    update_deck_metadata,
    update_slide_metadata,
)

STATIC_DIR = Path(__file__).parent / "static"
TagId = Annotated[str, Field(min_length=1, max_length=100)]


class LibrarySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[TagId] = Field(min_length=1, max_length=1000)


class LibraryDeletion(LibrarySelection):
    expected_slide_count: int = Field(ge=0)


class NewTag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    tag_type: str = Field(min_length=1, max_length=60)


class DeckMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=250)
    description: str | None = Field(default=None, max_length=20000)
    organisation: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1900, le=2100)
    author: str | None = Field(default=None, max_length=250)
    subject: str | None = Field(default=None, max_length=1000)
    subtitle: str | None = Field(default=None, max_length=2000)
    review_status: Literal["Not Reviewed", "Reviewed"] | None = None
    purpose: str | None = Field(default=None, max_length=2000)
    industry: str | None = Field(default=None, max_length=200)
    keywords: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=100)
    tag_ids: list[TagId] = Field(default_factory=list, max_length=200)


class SlideMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=250)
    description: str | None = Field(default=None, max_length=20000)
    industry: str | None = Field(default=None, max_length=200)
    keywords: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=100)
    tag_ids: list[TagId] = Field(default_factory=list, max_length=200)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    for path in (
        settings.data_dir,
        settings.imports_dir,
        settings.thumbnails_dir,
        settings.indexes_dir,
        settings.staging_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    initialize_database(settings.database_path)
    backfill_slide_titles(settings.database_path)
    for import_id in recover_imports(settings):
        threading.Thread(target=enrichment.import_with_optional_ai,args=(settings,import_id),daemon=True).start()
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE thumbnail_jobs SET status = 'failed', stage = 'failed', error = 'Application restarted during rendering. Retry thumbnails.', completed_at = datetime('now') WHERE status IN ('queued', 'processing')")
    for job_id in enrichment.recover_jobs(settings):
        threading.Thread(target=enrichment.run_job, args=(settings, job_id), daemon=True).start()
    with connect_database(settings.database_path) as db:
        pending_ai = [r[0] for r in db.execute("SELECT id FROM import_jobs WHERE enrichment_options IS NOT NULL AND enrichment_job_id IS NULL AND enrichment_error IS NULL AND status IN ('completed', 'completed_with_errors')")]
    for job_id in pending_ai:
        threading.Thread(target=enrichment.finish_post_import, args=(settings, job_id), daemon=True).start()
    for job in work_jobs.recover(settings):
        if job['kind'] == 'index':
            threading.Thread(target=search.run_index, args=(settings,job['id']),daemon=True).start()
        elif job['kind'] == 'export':
            threading.Thread(target=assembly.run_job,args=(settings,job['id']),daemon=True).start()
    search_stop = threading.Event()
    threading.Thread(target=search.maintain,args=(settings,search_stop),daemon=True).start()
    yield
    search_stop.set()


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Slide Navigator", lifespan=lifespan)
    application.state.settings = settings or load_settings()
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.post('/api/exports/preflight')
    def export_preflight(request: assembly.Selection):
        return assembly.preflight(application.state.settings,request.ids)

    @application.post('/api/exports',status_code=202)
    def export_slides(request: assembly.ExportRequest,background_tasks: BackgroundTasks):
        job=assembly.create_job(application.state.settings,request)
        if not job['reused']:background_tasks.add_task(assembly.run_job,application.state.settings,job['job_id'])
        return job

    @application.get('/api/exports/{job_id}/download')
    def download_export(job_id: str):
        job=work_jobs.get(application.state.settings,job_id)
        if job['kind']!='export' or job['status']!='completed' or not job['output_path']:
            raise HTTPException(409,'Export is not ready to save.')
        expected=(application.state.settings.indexes_dir/'exports'/job_id).resolve()
        output=expected/json.loads(job['options'])['filename']
        if output.resolve().parent!=expected or not output.is_file():raise HTTPException(404,'Export file is unavailable.')
        return FileResponse(output,filename=output.name,media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation')

    @application.get('/api/search')
    def search_library(q: str = Query(default='',max_length=2000), mode: Literal['keyword','semantic','hybrid']='hybrid',
                       kind: Literal['deck','slide'] | None = None, tag_id: list[TagId] = Query(default=[],max_length=100),
                       organisation_id: str | None = None, year: int | None = Query(default=None,ge=1900,le=2100),
                       deck_id: str | None = None, offset: int = Query(default=0,ge=0),limit: int = Query(default=50,ge=1,le=100)):
        return search.retrieve(application.state.settings,q,mode,kind,tag_id,organisation_id,year,deck_id,offset,limit)

    @application.post('/api/search/index',status_code=202)
    def index_library(background_tasks: BackgroundTasks):
        with connect_database(application.state.settings.database_path) as db:
            db.execute("INSERT OR REPLACE INTO app_metadata VALUES ('search_enabled','1')")
        job=work_jobs.create(application.state.settings,'index')
        background_tasks.add_task(search.run_index,application.state.settings,job['job_id'])
        return job

    @application.get('/api/work-jobs')
    def work_history(kind: Literal['index','export'] | None = None):
        with connect_database(application.state.settings.database_path) as db:
            return {'items':[dict(r) for r in db.execute('SELECT * FROM work_jobs WHERE (? IS NULL OR kind=?) ORDER BY created_at DESC LIMIT 100',(kind,kind))]}

    @application.get('/api/work-jobs/{job_id}')
    def work_progress(job_id: str):
        return work_jobs.get(application.state.settings,job_id)

    @application.post('/api/work-jobs/{job_id}/cancel')
    def cancel_work(job_id: str):
        with connect_database(application.state.settings.database_path) as db:
            db.execute("UPDATE work_jobs SET status='cancel_requested' WHERE id=? AND status IN ('queued','processing')",(job_id,))
        return work_jobs.get(application.state.settings,job_id)

    @application.post("/api/decks/{deck_id}/thumbnails", status_code=202)
    def regenerate_thumbnails(deck_id: str, background_tasks: BackgroundTasks):
        job = thumbnails.create_job(application.state.settings, deck_id)
        background_tasks.add_task(thumbnails.run_job, application.state.settings, job["job_id"])
        return job

    @application.get("/api/thumbnail-jobs/{job_id}")
    def thumbnail_progress(job_id: str):
        return thumbnails.get_job(application.state.settings, job_id)

    @application.get("/api/enrichment/models")
    def enrichment_models():
        return enrichment.models(application.state.settings)

    @application.post("/api/enrichment/jobs", status_code=202)
    def enrich(request: enrichment.EnrichmentRequest, background_tasks: BackgroundTasks):
        job = enrichment.create_job(application.state.settings, request)
        background_tasks.add_task(enrichment.run_job, application.state.settings, job["job_id"])
        return job

    @application.get("/api/enrichment/jobs")
    def enrichment_history():
        with connect_database(application.state.settings.database_path) as db:
            return {"items": [dict(r) for r in db.execute("SELECT * FROM enrichment_jobs ORDER BY created_at DESC LIMIT 100")]}

    @application.get("/api/enrichment/jobs/{job_id}")
    def enrichment_progress(job_id: str):
        return enrichment.get_job(application.state.settings, job_id)

    @application.get("/api/enrichment/{kind}/{entity_id}/suggestions")
    def entity_suggestions(kind: Literal["deck", "slide"], entity_id: str):
        return {"items": enrichment.suggestions(application.state.settings, kind, entity_id)}

    @application.post("/api/enrichment/review")
    def review_suggestions(request: enrichment.ReviewRequest):
        return enrichment.review(application.state.settings, request)

    @application.post("/api/library/{kind}/deletion-preview")
    async def preview_deletion(kind: Literal["decks", "slides"], selection: LibrarySelection):
        if len(selection.ids) != len(set(selection.ids)):
            raise HTTPException(422, "Choose each item only once.")
        return deletion(application.state.settings, kind, selection.ids)

    @application.post("/api/library/{kind}/delete")
    async def delete_library_items(kind: Literal["decks", "slides"], selection: LibraryDeletion):
        if len(selection.ids) != len(set(selection.ids)):
            raise HTTPException(422, "Choose each item only once.")
        return deletion(application.state.settings, kind, selection.ids, execute=True,
                        expected_slide_count=selection.expected_slide_count)

    @application.get("/api/taxonomy")
    def taxonomy_list(q: str = "", category: str = "", unused: bool = False):
        return taxonomy.listing(application.state.settings, q, category, unused)

    @application.post("/api/taxonomy/categories")
    def taxonomy_category(value: taxonomy.Category):
        return taxonomy.add_category(application.state.settings, value)

    @application.patch("/api/tags/{tag_id}")
    def taxonomy_edit(tag_id: str, value: taxonomy.TagPatch):
        return taxonomy.rename(application.state.settings, tag_id, value)

    @application.post("/api/tags/{tag_id}/aliases")
    def taxonomy_alias(tag_id: str, value: NewTag):
        return taxonomy.add_alias(application.state.settings, tag_id, value.name)

    @application.post("/api/tags/{tag_id}/impact")
    def taxonomy_impact(tag_id: str, value: taxonomy.Destructive):
        return taxonomy.change(application.state.settings, tag_id, value)

    @application.post("/api/tags/{tag_id}/apply")
    def taxonomy_apply(tag_id: str, value: taxonomy.Destructive):
        return taxonomy.change(application.state.settings, tag_id, value, execute=True)

    @application.post("/api/taxonomy/bulk")
    def taxonomy_bulk(value: taxonomy.Bulk):
        return taxonomy.bulk(application.state.settings, value)

    @application.get("/api/taxonomy/history")
    def taxonomy_history():
        with connect_database(application.state.settings.database_path) as db:
            return {"items": [dict(row) for row in db.execute("SELECT * FROM taxonomy_history ORDER BY created_at DESC LIMIT 200")]}

    @application.post("/api/tags")
    async def add_tag(tag: NewTag):
        return create_tag(application.state.settings, tag.name, tag.tag_type)

    @application.put("/api/{kind}/{entity_id}/tags/{tag_id}")
    async def assign_tag(kind: Literal["decks", "slides"], entity_id: str, tag_id: TagId):
        return change_assignment(application.state.settings, kind, entity_id, tag_id)

    @application.delete("/api/{kind}/{entity_id}/tags/{tag_id}")
    async def unassign_tag(kind: Literal["decks", "slides"], entity_id: str, tag_id: TagId):
        return change_assignment(application.state.settings, kind, entity_id, tag_id, remove=True)

    @application.get("/", include_in_schema=False)
    async def homepage() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/import", status_code=202)
    async def import_presentations(
        background_tasks: BackgroundTasks,
        files: list[UploadFile] = File(...),
        file_metadata: str = Form("[]"),
        storage_mode: str | None = Form(None),
        enrichment_options: str | None = Form(None),
    ) -> dict[str, str]:
        current_settings: Settings = application.state.settings
        ai_options = None
        if enrichment_options is not None:
            try:
                ai_options = enrichment.PostImportOptions.model_validate_json(enrichment_options)
            except ValueError as error:
                raise HTTPException(422, "Invalid optional AI enrichment settings.") from error
        if not files:
            raise HTTPException(status_code=422, detail="Choose at least one file.")
        if len(files) > current_settings.max_archive_files:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Choose no more than {current_settings.max_archive_files} "
                    "files in one import."
                ),
            )
        try:
            metadata = json.loads(file_metadata)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=422, detail="File metadata must be valid JSON."
            ) from error
        if (
            not isinstance(metadata, list)
            or len(metadata) > len(files)
            or any(not isinstance(item, dict) for item in metadata)
        ):
            raise HTTPException(
                status_code=422, detail="File metadata does not match the uploads."
            )
        mode = (storage_mode or current_settings.storage_mode).strip().lower()
        if mode not in {"managed_copy", "reference"}:
            raise HTTPException(status_code=422, detail="Unknown storage mode.")
        if mode == "reference":
            raise HTTPException(
                status_code=422,
                detail=(
                    "Reference mode is deferred for browser uploads because the "
                    "browser does not provide a trustworthy absolute file path. "
                    "Choose Managed copy."
                ),
            )
        job_id = await stage_browser_uploads(
            current_settings, files, metadata, mode
        )
        if ai_options:
            with connect_database(current_settings.database_path) as db:
                db.execute("UPDATE import_jobs SET enrichment_options = ? WHERE id = ?", (ai_options.model_dump_json(), job_id))
            background_tasks.add_task(enrichment.import_with_optional_ai, current_settings, job_id)
        else:
            background_tasks.add_task(process_import_job, current_settings, job_id)
        return {"job_id": job_id, "status": "queued"}

    @application.get("/api/import/jobs")
    async def import_history(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        active_only: bool = False,
    ) -> dict[str, object]:
        return list_import_jobs(
            application.state.settings, offset=offset, limit=limit, active_only=active_only
        )

    @application.get("/api/import/jobs/{job_id}")
    async def import_job_status(job_id: str, item_offset: int = Query(default=0, ge=0), item_limit: int = Query(default=100, ge=1, le=500)) -> dict[str, object]:
        job = get_import_job(application.state.settings, job_id, item_offset=item_offset, item_limit=item_limit)
        if job is None:
            raise HTTPException(status_code=404, detail="Import job not found.")
        return job

    @application.get("/api/decks")
    async def decks(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        q: str = Query(default="", max_length=200),
        organisation_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        sort_by: Literal[
            "name", "original_filename", "organisation", "year",
            "slide_count", "imported_at", "description"
        ] = "imported_at",
        sort_order: Literal["asc", "desc"] = "desc",
        deck_type: list[TagId] = Query(default=[], max_length=100),
        slide_type: list[TagId] = Query(default=[], max_length=100),
        tag_id: list[TagId] = Query(default=[], max_length=100),
    ) -> dict[str, object]:
        return list_decks(
            application.state.settings,
            offset=offset,
            limit=limit,
            query=q,
            organisation_id=organisation_id,
            year=year,
            sort_by=sort_by,
            sort_order=sort_order,
            deck_types=deck_type,
            slide_types=slide_type,
            tag_ids=tag_id,
        )

    @application.get("/api/filters")
    async def library_filter_options() -> dict[str, object]:
        return list_filter_options(application.state.settings)

    @application.get("/api/decks/{deck_id}")
    async def deck_detail(deck_id: str) -> dict[str, object]:
        deck = get_deck(application.state.settings, deck_id)
        if deck is None:
            raise HTTPException(status_code=404, detail="Presentation not found.")
        return deck

    @application.patch("/api/decks/{deck_id}")
    async def edit_deck(deck_id: str, updates: DeckMetadataPatch) -> dict[str, object]:
        try:
            deck = update_deck_metadata(
                application.state.settings,
                deck_id,
                updates.model_dump(exclude_unset=True),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if deck is None:
            raise HTTPException(status_code=404, detail="Presentation not found.")
        return deck

    @application.get("/api/slides")
    async def slides(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        deck_id: str | None = None,
        q: str = Query(default="", max_length=200),
        organisation_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        sort_by: Literal["slide_number", "title", "deck_name", "imported_at"] = "slide_number",
        sort_order: Literal["asc", "desc"] = "asc",
        slide_type: list[TagId] = Query(default=[], max_length=100),
        topic: list[TagId] = Query(default=[], max_length=100),
        tag_id: list[TagId] = Query(default=[], max_length=100),
    ) -> dict[str, object]:
        if deck_id is not None:
            with connect_database(application.state.settings.database_path) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM decks WHERE id = ?", (deck_id,)
                ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Presentation not found.")
        return list_slides(
            application.state.settings,
            offset=offset,
            limit=limit,
            deck_id=deck_id,
            query=q,
            organisation_id=organisation_id,
            year=year,
            sort_by=sort_by,
            sort_order=sort_order,
            slide_types=slide_type,
            topics=topic,
            tag_ids=tag_id,
        )

    @application.get("/api/slides/{slide_id}")
    async def slide_detail(slide_id: str) -> dict[str, object]:
        slide = get_slide(application.state.settings, slide_id)
        if slide is None:
            raise HTTPException(status_code=404, detail="Slide not found.")
        return slide

    @application.patch("/api/slides/{slide_id}")
    async def edit_slide(slide_id: str, updates: SlideMetadataPatch) -> dict[str, object]:
        try:
            slide = update_slide_metadata(
                application.state.settings,
                slide_id,
                updates.model_dump(exclude_unset=True),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if slide is None:
            raise HTTPException(status_code=404, detail="Slide not found.")
        return slide

    @application.get("/api/slides/{slide_id}/thumbnail")
    async def slide_thumbnail(slide_id: str) -> Response:
        slide = get_slide(application.state.settings, slide_id)
        if slide is None:
            raise HTTPException(status_code=404, detail="Slide not found.")
        thumbnail_path = get_thumbnail_path(application.state.settings, slide_id)
        if thumbnail_path is not None:
            return FileResponse(thumbnail_path, media_type="image/png", headers={"Cache-Control": "no-cache"})
        placeholder = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" '
            'viewBox="0 0 640 360"><rect width="640" height="360" fill="#f3f3f3"/>'
            '<rect x="2" y="2" width="636" height="356" rx="4" fill="none" '
            'stroke="#e5e5e5" stroke-width="2"/><path d="M288 145h64v42h-64z'
            'm32 42v17m-18 0h36" fill="none" stroke="#777" stroke-width="4" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
        return Response(content=placeholder, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @application.get("/api/ollama/status")
    async def ollama_status() -> dict[str, object]:
        current_settings: Settings = application.state.settings
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{current_settings.ollama_base_url}/api/tags"
                )
                response.raise_for_status()
        except httpx.RequestError as error:
            return {
                "connected": False,
                "models": [],
                "required_models": list(current_settings.ollama_required_models),
                "error": str(error),
            }
        except httpx.HTTPStatusError as error:
            return {
                "connected": False,
                "models": [],
                "required_models": list(current_settings.ollama_required_models),
                "error": f"Ollama returned HTTP {error.response.status_code}",
            }

        payload = response.json()
        installed_names = {
            model.get("name")
            for model in payload.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
        models = [
            {
                "name": name,
                "available": (
                    name if ":" in name else f"{name}:latest"
                )
                in installed_names,
            }
            for name in current_settings.ollama_required_models
        ]
        return {
            "connected": True,
            "models": models,
            "required_models": list(current_settings.ollama_required_models),
            "error": None,
        }

    return application


app = create_app()
