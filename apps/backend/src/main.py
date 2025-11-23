
"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import the database manager we just created
from src.core.database import connect_db, disconnect_db, db

# 1. Define the Lifespan (Startup & Shutdown Logic)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print(" Connecting to Database...")
    try:
        await connect_db()
    except Exception as e:
        print(f" Database Connection Failed: {e}")
    
    yield  # The application runs here
    
    # --- SHUTDOWN ---
    print(" Disconnecting from Database...")
    await disconnect_db()

# 2. Initialize App with Lifespan
app = FastAPI(
    title="Maigie API",
    description="AI-powered student companion API",
    version="0.1.0",
    lifespan=lifespan, # <--- This wires up the DB connection
)

# 3. CORS middleware (Kept your existing settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://localhost:5173"],  # Web and mobile dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Maigie API", "version": "0.1.0"}

# 4. Updated Health Check to include Database Status
@app.get("/health")
async def health():
    """Health check endpoint."""
    db_status = "connected" if db.is_connected() else "disconnected"
    return {
        "status": "healthy",
        "database": db_status
    }