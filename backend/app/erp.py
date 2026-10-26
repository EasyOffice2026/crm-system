import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


def connection() -> tuple[str, str]:
    base = os.environ.get("CRM_ERP_URL", "").rstrip("/")
    token = os.environ.get("CRM_ERP_TOKEN", "")
    if not base or not token:
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
    return base, token


def erp_get(path: str, params: dict[str, str] | None = None) -> object:
    return erp_request("GET", path, params=params)


def erp_post(path: str, payload: dict[str, object]) -> object:
    return erp_request("POST", path, payload=payload)


def erp_request(
    method: str, path: str, params: dict[str, str] | None = None, payload: dict[str, object] | None = None
) -> object:
    base, token = connection()
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.request(
                method,
                f"{base}/api/v1/integrations/crm/{path}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
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
