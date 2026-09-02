from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import ExternalIntegrationError
from app.schemas.external_actions import (
    CreateFollowUpTaskPayload,
    CreateOrUpdateCRMLeadPayload,
    MarkLeadStatusPayload,
)


class CRMActionResult(BaseModel):
    external_reference: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class CRMProvider(Protocol):
    def create_or_update_lead(
        self, payload: CreateOrUpdateCRMLeadPayload
    ) -> CRMActionResult: ...

    def create_task(self, payload: CreateFollowUpTaskPayload) -> CRMActionResult: ...

    def update_lead_status(
        self, payload: MarkLeadStatusPayload
    ) -> CRMActionResult: ...


class HubSpotCRMProvider:
    """Fixed-host HubSpot adapter; never accepts a caller-provided URL."""

    _BASE_URL = "https://api.hubapi.com"

    def __init__(
        self,
        *,
        access_token: str,
        timeout_seconds: float = 10.0,
        client: object | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("HubSpot access token is required")
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {access_token}"}
        )

    def create_or_update_lead(
        self, payload: CreateOrUpdateCRMLeadPayload
    ) -> CRMActionResult:
        data = {
            "inputs": [
                {
                    "id": str(payload.email),
                    "idProperty": "email",
                    "properties": {
                        "email": str(payload.email),
                        "gtm_agentos_lead_id": str(payload.lead_id),
                        "firstname": payload.name,
                        "company": payload.company,
                        "jobtitle": payload.job_title or "",
                    },
                }
            ]
        }
        response = self._post(
            f"{self._BASE_URL}/crm/objects/2026-03/contacts/batch/upsert",
            data,
        )
        return CRMActionResult(external_reference=self._reference(response, "contact"))

    def create_task(self, payload: CreateFollowUpTaskPayload) -> CRMActionResult:
        response = self._post(
            f"{self._BASE_URL}/crm/v3/objects/tasks",
            {
                "properties": {
                    "hs_task_subject": payload.title,
                    "hs_task_body": payload.description,
                    "hs_task_status": "NOT_STARTED",
                }
            },
        )
        return CRMActionResult(external_reference=self._reference(response, "task"))

    def update_lead_status(
        self, payload: MarkLeadStatusPayload
    ) -> CRMActionResult:
        search_response = self._post(
            f"{self._BASE_URL}/crm/v3/objects/contacts/search",
            {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "gtm_agentos_lead_id",
                                "operator": "EQ",
                                "value": str(payload.lead_id),
                            }
                        ]
                    }
                ],
                "properties": ["hs_lead_status"],
                "limit": 1,
            },
        )
        contact_id = self._reference(search_response, "contact")
        response = self._patch(
            f"{self._BASE_URL}/crm/v3/objects/contacts/{contact_id}",
            {"properties": {"hs_lead_status": payload.status}},
        )
        return CRMActionResult(external_reference=self._reference(response, "contact"))

    def _post(self, url: str, data: dict[str, object]) -> object:
        return self._request("post", url, data)

    def _patch(self, url: str, data: dict[str, object]) -> object:
        return self._request("patch", url, data)

    def _request(
        self, method: str, url: str, data: dict[str, object]
    ) -> object:
        try:
            request_method = getattr(self._client, method)
            response = request_method(
                url,
                json=data,
                timeout=self._timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise ExternalIntegrationError("HubSpot request failed") from exc
        if not 200 <= response.status_code < 300:
            raise ExternalIntegrationError("HubSpot rejected the CRM action")
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise ExternalIntegrationError("HubSpot response was invalid") from exc

    @staticmethod
    def _reference(data: object, fallback_prefix: str) -> str:
        if isinstance(data, dict):
            candidate = data.get("id")
            if isinstance(candidate, str) and candidate:
                return candidate[:500]
            results = data.get("results")
            if isinstance(results, list) and results and isinstance(results[0], dict):
                candidate = results[0].get("id")
                if isinstance(candidate, str) and candidate:
                    return candidate[:500]
        raise ExternalIntegrationError(f"HubSpot returned no {fallback_prefix} reference")
