import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import telemetry
from app.routes.contour import router as contour_router
from app.services.contour_analyzer import warm_up

VERSION = "1.2.0"

# The backend's own log messages (logger names starting "pond"), shown in
# the terminal and, with telemetry on, exported as OpenTelemetry logs.
_pond_log = logging.getLogger("pond")
_pond_log.setLevel(logging.INFO)
if not _pond_log.handlers:
    _console = logging.StreamHandler()
    _console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _pond_log.addHandler(_console)

# Off unless OTEL_EXPORTER_OTLP_ENDPOINT or an OTEL_*_EXPORTER is set; see
# app/telemetry.py. Each uvicorn worker process runs this once.
telemetry.setup("pond-backend", VERSION)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compile the analysis code in the background, so the first real
    # request doesn't wait for it and the server starts answering at once.
    threading.Thread(target=warm_up, daemon=True).start()
    yield


app = FastAPI(
    title="Pond Planning API",
    description="""
    Backend API for automated pond and catchment analysis.

    The backend accepts KML or KMZ contour data, generates a Digital Elevation Model
    (DEM), performs hydrological analysis, identifies potential pond locations,
    and calculates the catchment area and related characteristics for selected
    ponds.
    """,
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(contour_router)

@app.get("/")
def root():
    """
    Return basic information about the Pond Analysis API.

    This endpoint serves as the root entry point of the backend API.
    It can be used to verify that the FastAPI application is running
    and accessible.

    Unlike the analysis endpoints, this endpoint does not perform any
    terrain, DEM, hydrological, or pond-related calculations.

    Returns:
        200:
            A JSON object containing a simple status message indicating
            that the API is operational.

    Example response:

        {
            "message": "Pond Analysis API is running"
        }
    """

    return {
        "message": "Pond Planning API is running"
    }


if telemetry.ENABLED:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # One server span per request, carrying on the trace started by the load
    # balancer (W3C traceparent header). "/" is left out: the load balancer
    # checks it every few seconds, which would bury the real requests.
    # receive/send spans for each chunk of an upload are left out as noise.
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=r"^https?://[^/]+/$",
        exclude_spans=["receive", "send"],
    )

    # Must stay the outermost layer; see FreshTraceContext in app/telemetry.py.
    app = telemetry.FreshTraceContext(app)
