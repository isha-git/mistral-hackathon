from fastapi import FastAPI

from api.routes.health import router as health_router
from api.routes.webhook import router as webhook_router

app = FastAPI(title="WhatsApp Bridge API")

app.include_router(health_router)
app.include_router(webhook_router)
