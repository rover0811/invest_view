from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter()


@router.get("/health")
async def health(request: Request):
    consumer = getattr(request.app.state, "alert_consumer", None)
    if consumer is not None and not consumer.is_alive():
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
