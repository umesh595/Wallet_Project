from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import router
from app.database import init_db, shutdown_db
from app.logging_config import logger
from app.config import settings
app = FastAPI(title=settings.APP_NAME, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Wallet Service...")
    await init_db()
    logger.info("Database initialized with User, Wallet, and Transaction tables")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Wallet Service...")
    await shutdown_db()
    logger.info("Database connections closed")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": settings.APP_NAME}

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)