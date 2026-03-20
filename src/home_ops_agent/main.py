"""FastAPI application — entry point for home-ops-agent."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from home_ops_agent.api.chat import router as chat_router, set_mcp_tools
from home_ops_agent.api.settings import router as settings_router
from home_ops_agent.api.status import router as status_router
from home_ops_agent.database import init_db
from home_ops_agent.mcp.bridge import mcp_tools_to_agent_tools
from home_ops_agent.mcp.client import MCPClient
from home_ops_agent.workers.alert_subscriber import run_alert_subscriber
from home_ops_agent.workers.pr_monitor import run_pr_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

mcp_client = MCPClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: initialize DB, connect MCP servers, start background workers."""
    # Initialize database tables
    await init_db()
    logger.info("Database initialized")

    # Connect to MCP sidecar servers (if available)
    mcp_tools = []
    try:
        await mcp_client.connect(
            name="grafana",
            command="mcp-grafana",
            env={
                "GRAFANA_URL": "http://grafana.monitoring.svc.cluster.local",
            },
        )
        logger.info("Connected to Grafana MCP server")
    except Exception:
        logger.warning("Grafana MCP server not available (will run without Grafana tools)")

    try:
        await mcp_client.connect(
            name="flux",
            command="flux-operator-mcp",
        )
        logger.info("Connected to Flux Operator MCP server")
    except Exception:
        logger.warning("Flux MCP server not available (will run without Flux tools)")

    mcp_tools = mcp_tools_to_agent_tools(mcp_client)
    set_mcp_tools(mcp_tools)
    logger.info("Registered %d MCP tools", len(mcp_tools))

    # Start background workers
    pr_task = asyncio.create_task(run_pr_monitor())
    alert_task = asyncio.create_task(run_alert_subscriber(mcp_tools))
    logger.info("Background workers started")

    yield

    # Shutdown
    pr_task.cancel()
    alert_task.cancel()
    await mcp_client.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Home-Ops Agent",
    description="Agentic home lab operator",
    version="0.1.0",
    lifespan=lifespan,
)

# API routes
app.include_router(status_router)
app.include_router(chat_router)
app.include_router(settings_router)

# Static files (web UI)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
