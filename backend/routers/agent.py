"""Local Agent distribution and information endpoints."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/info")
async def agent_info() -> dict:
    """Connection info for the CodingPass Local Agent."""
    return {
        "host": "127.0.0.1",
        "port": 8765,
        "websocket_url": "ws://127.0.0.1:8765",
        "download_url": "/api/agent/download",
        "version": "1.0.0",
    }


@router.get("/download")
async def download_agent_package():
    """Download a standalone zip containing the Local Agent runner."""
    agent_dir = Path(__file__).resolve().parent.parent.parent / "local_agent"
    members = [
        (agent_dir / name, name)
        for name in ("agent.py", "run_agent.sh", "run_agent.bat", "README.md")
        if (agent_dir / name).is_file()
    ]
    if not any(name == "agent.py" for _, name in members):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The Local Agent package is not installed on this server.",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath, name in members:
            zf.write(fpath, arcname=f"codingpass-agent/{name}")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="codingpass-agent.zip"',
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
