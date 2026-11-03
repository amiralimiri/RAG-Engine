from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.middleware import CorrelationLoggingMiddleware
from app.api.routes import router
from app.core.concurrency import shutdown_background_loop
from app.core.config import BASE_DIR, get_settings
from app.core.logger import configure_logging
from app.core.telemetry import flush_telemetry
from app.rag.evaluation import get_evaluator
from app.rag.memory import get_conversation_memory
from app.rag.prompts import get_prompt_store
from app.rag.workflows import get_workflow_runner

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    configure_logging()
    get_prompt_store().seed_prompts()
    get_workflow_runner(memory=get_conversation_memory(), evaluator=get_evaluator())
    yield
    # Shutdown
    flush_telemetry()
    shutdown_background_loop()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)

app.include_router(router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name},
    )
