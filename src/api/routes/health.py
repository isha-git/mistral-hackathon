from fastapi import APIRouter, HTTPException, status

from src.api.config.redis import get_redis_client
from src.api.config.celery import celery_app
from src.api.config.settings import get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "",
    summary="Health check endpoint",
)
async def health_check():
    """
    Check the health of all system components.

    Returns 200 if all components are healthy, 503 if any component is degraded.
    """
    settings = get_settings()
    components = {
        "api": {"status": "healthy"},
        "redis": {"status": "unknown"},
        "celery": {"status": "unknown"},
    }

    # Check Redis
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        components["redis"]["status"] = "healthy"
    except Exception as e:
        components["redis"]["status"] = "unhealthy"
        components["redis"]["error"] = str(e)

    # Check Celery
    try:
        # Try to inspect workers
        inspector = celery_app.control.inspect()
        workers = inspector.ping()
        if workers:
            components["celery"]["status"] = "healthy"
            components["celery"]["workers"] = list(workers.keys())
        else:
            components["celery"]["status"] = "unhealthy"
            components["celery"]["error"] = "No workers responding"
    except Exception as e:
        components["celery"]["status"] = "unhealthy"
        components["celery"]["error"] = str(e)

    # Determine overall status
    all_healthy = all(c["status"] == "healthy" for c in components.values())

    if all_healthy:
        return {
            "status": "healthy",
            "components": components,
            "version": settings.app_version,
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "degraded",
                "components": components,
                "version": settings.app_version,
            },
        )


@router.get(
    "/ready",
    summary="Readiness check",
)
async def readiness_check():
    """
    Simple readiness check for Kubernetes/load balancer health checks.

    Returns 200 if the API is ready to accept requests.
    """
    return {"status": "ready"}


@router.get(
    "/live",
    summary="Liveness check",
)
async def liveness_check():
    """
    Simple liveness check for Kubernetes.

    Returns 200 if the API is alive (doesn't check dependencies).
    """
    return {"status": "alive"}
