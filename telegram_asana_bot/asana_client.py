from __future__ import annotations

import httpx


class AsanaAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AsanaClient:
    BASE = "https://app.asana.com/api/1.0"

    def __init__(self, pat: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        url = f"{self.BASE}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.request(
                method,
                url,
                headers=self._headers,
                params=params,
                json=json_body,
            )
        try:
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            raise AsanaAPIError(f"Asana вернула не JSON: {r.text[:200]}", r.status_code) from exc

        if r.status_code >= 400:
            errs = payload.get("errors") if isinstance(payload, dict) else None
            msg = errs[0].get("message") if errs else str(payload)
            raise AsanaAPIError(f"Asana ошибка ({r.status_code}): {msg}", r.status_code)

        if not isinstance(payload, dict) or "data" not in payload:
            raise AsanaAPIError(f"Неожиданный ответ Asana: {str(payload)[:300]}", r.status_code)

        return payload["data"]

    async def list_workspaces_for_me(self) -> list[dict]:
        data = await self._request(
            "GET",
            "/users/me",
            params={"opt_fields": "workspaces.gid,workspaces.name"},
        )
        workspaces = data.get("workspaces") or []
        return [w for w in workspaces if isinstance(w, dict)]

    async def list_projects(self, workspace_gid: str) -> list[dict]:
        data = await self._request(
            "GET",
            "/projects",
            params={
                "workspace": workspace_gid,
                "archived": "false",
                "opt_fields": "name,gid",
                "limit": "100",
            },
        )
        return [p for p in data if isinstance(p, dict)]

    async def list_sections(self, project_gid: str) -> list[dict]:
        data = await self._request(
            "GET",
            f"/projects/{project_gid}/sections",
            params={"opt_fields": "name,gid", "limit": "100"},
        )
        return [s for s in data if isinstance(s, dict)]

    async def list_users_in_workspace(self, workspace_gid: str) -> list[dict]:
        data = await self._request(
            "GET",
            f"/workspaces/{workspace_gid}/users",
            params={"opt_fields": "name,gid", "limit": "100"},
        )
        return [u for u in data if isinstance(u, dict)]

    async def create_task(
        self,
        *,
        name: str,
        project_gid: str,
        assignee_gid: str | None,
    ) -> dict:
        body: dict = {
            "data": {
                "name": name,
                "projects": [project_gid],
            }
        }
        if assignee_gid:
            body["data"]["assignee"] = assignee_gid

        return await self._request(
            "POST",
            "/tasks",
            params={"opt_fields": "gid,name,permalink_url"},
            json_body=body,
        )

    async def add_task_to_section(self, section_gid: str, task_gid: str) -> dict:
        return await self._request(
            "POST",
            f"/sections/{section_gid}/addTask",
            json_body={"data": {"task": task_gid}},
        )
