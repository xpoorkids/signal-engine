from fastapi import FastAPI
from app.routes import health, scan, score, packet, watch

app = FastAPI(title="signal-engine")

app.include_router(health.router)
app.include_router(scan.router)
app.include_router(score.router)
app.include_router(packet.router)
app.include_router(watch.router)
