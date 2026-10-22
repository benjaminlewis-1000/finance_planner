from fastapi import APIRouter, HTTPException

from app.system.owlplanner_upgrade import check_for_update, test_candidate_version

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/owlplanner/status")
def owlplanner_status():
    try:
        return check_for_update()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach PyPI: {e}")


@router.post("/owlplanner/test-upgrade")
def owlplanner_test_upgrade():
    """Test-drives the latest owlplanner release in a throwaway virtualenv by
    running the real backend test suite against it. Never touches the
    running environment or any file -- applying a verified-safe upgrade
    (bumping requirements.txt, rebuilding, redeploying) is a separate manual
    step."""
    try:
        status = check_for_update()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach PyPI: {e}")

    if status["up_to_date"]:
        return {"ran": False, "reason": "Already on the latest version.", **status}

    result = test_candidate_version(status["latest_version"])
    return {
        "ran": True,
        **status,
        "test_passed": result.passed,
        "test_returncode": result.returncode,
        "test_output": result.output,
        "test_duration_seconds": result.duration_seconds,
    }
