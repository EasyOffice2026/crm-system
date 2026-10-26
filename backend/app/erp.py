import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from .tenancy import current_workspace, hosted


def erp_base() -> str:
    base = os.environ.get("CRM_ERP_URL", "").rstrip("/")
    if not base:
        raise HTTPException(503, "ERP accounting is not connected")
    try:
        parsed = urlsplit(base)
    except ValueError as exc:
        raise HTTPException(503, "ERP URL must be a trusted HTTPS origin") from exc
    local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        (parsed.scheme != "https" and not (local and os.environ.get("CRM_ENV") != "production"))
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(503, "ERP URL must be a trusted HTTPS origin")
    return base


def workspace_token(workspace_id: str) -> str | None:
    from .control import WorkspaceCredential, control_sessions
    from .secretbox import decrypt

    with control_sessions()() as control:
        credential = control.scalar(
            select(WorkspaceCredential).where(
                WorkspaceCredential.workspace_id == workspace_id, WorkspaceCredential.status == "active"
            )
        )
        return decrypt(credential.token_enc) if credential else None


def connection() -> tuple[str, dict[str, str]]:
    if hosted():
        workspace = current_workspace.get()
        token = workspace_token(workspace.id) if workspace else None
        if not workspace or not token:
            raise HTTPException(503, "ERP accounting is not connected")
        return erp_base(), {"Authorization": f"Bearer {token}", "X-Crm-Workspace-Id": workspace.id}
    token = os.environ.get("CRM_ERP_TOKEN", "")
    if not os.environ.get("CRM_ERP_URL") or not token:
        raise HTTPException(503, "ERP accounting is not connected")
    return erp_base(), {"Authorization": f"Bearer {token}"}


def erp_get(path: str, params: dict[str, str] | None = None) -> object:
    return erp_request("GET", path, params=params)


def erp_post(path: str, payload: dict[str, object]) -> object:
    return erp_request("POST", path, payload=payload)


def erp_request(
    method: str, path: str, params: dict[str, str] | None = None, payload: dict[str, object] | None = None
) -> object:
    base, headers = connection()
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.request(
                method,
                f"{base}/api/v1/integrations/crm/{path}",
                params=params,
                json=payload,
                headers=headers,
            )
        if response.status_code in {401, 403}:
            raise HTTPException(502, "ERP rejected the CRM connection")
        if method == "POST" and response.status_code in {409, 422}:
            raise HTTPException(
                502, "ERP refused the journal; check reconciliation, accounts and open periods"
            )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "ERP accounting is unavailable") from exc
