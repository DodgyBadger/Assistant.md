"""Observe real asynchronous import outcomes through the application's public API."""

import asyncio


async def wait_import_status(application, status):
    async with asyncio.timeout(30):
        while True:
            response = await application.app.request_async(
                "/api/import/jobs", params={"vault": application.vault.name}
            )
            assert response.status_code == 200, response.text
            jobs = response.json()["jobs"]
            if jobs and all(job["status"] == status for job in jobs):
                return jobs
            await asyncio.sleep(0.1)
