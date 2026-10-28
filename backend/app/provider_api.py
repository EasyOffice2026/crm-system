import hashlib
import hmac
import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .control import Workspace, control_sessions
from .provisioning import ProvisionError, ProvisionRequest, public_state, record_request
from .tenancy import host_name, hosted, provider_host


def provider_key(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    if not hosted() or not provider_host() or host_name(request.headers.get("host", "")) != provider_host():
        raise HTTPException(404, "Not found")
    expected = os.environ.get("CRM_PROVIDER_API_KEY_SHA256", "").lower()
    scheme, _, token = (authorization or "").partition(" ")
    actual = hashlib.sha256(token.encode()).hexdigest()
    if (
        len(expected) != 64
        or scheme.lower() != "bearer"
        or not token
        or not hmac.compare_digest(actual, expected)
    ):
        raise HTTPException(401, "Unauthorized")
    return f"provider-key:{expected[:8]}"


Provider = Annotated[str, Depends(provider_key)]
router = APIRouter(prefix="/provider/v1")


@router.post("/workspaces", status_code=202)
def request_workspace(body: ProvisionRequest, actor: Provider) -> object:
    try:
        workspace = record_request(body, actor)
    except ProvisionError as exc:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)
    state = public_state(workspace)
    return {key: state[key] for key in ("workspaceId", "slug", "status", "step")}


@router.get("/workspaces/{workspace_id}")
def workspace_status(workspace_id: uuid.UUID, actor: Provider) -> dict[str, object]:
    with control_sessions()() as control:
        workspace = control.get(Workspace, str(workspace_id))
        if workspace is None:
            raise HTTPException(404, "Workspace not found")
        return public_state(workspace)
