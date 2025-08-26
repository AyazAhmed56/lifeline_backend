from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services import ai_agent

# Import all routers
from routers import (
    users,
    health_records,
    medicines,
    appointments,
    reports,
    blood_requests,
    emergencies,
)
from services import ai_service

app = FastAPI(
    title="Lifeline Backend",
    version="1.0.0",
    description="Personal health record & emergency system with AI integration"
)

app.include_router(ai_agent.router)

# CORS setup — abhi sab allow hai, prod me origins restrict karna better
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ change to ["http://localhost:3000", "https://yourdomain.com"] in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", tags=["Health"])
def root():
    return {"msg": "Backend running fine 🚀"}

# Register routers
app.include_router(users.router, prefix="/api")
app.include_router(health_records.router, prefix="/api")
app.include_router(medicines.router, prefix="/api")
app.include_router(appointments.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(blood_requests.router, prefix="/api")
app.include_router(emergencies.router, prefix="/api")
app.include_router(ai_service.router, prefix="/api")  # optional; remove if not needed
