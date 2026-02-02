from fastapi import FastAPI

app = FastAPI(title="signal-engine")

@app.get("/health")
def health():
    return {"status": "ok"}
