from fastapi import APIRouter

from app.api.v1.endpoints import admin, assessment, assignment_views, auth, upload

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(assignment_views.router)
api_router.include_router(upload.router)
api_router.include_router(assessment.router)
