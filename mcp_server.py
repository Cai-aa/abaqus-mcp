#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abaqus MCP Server v4.0 - bridges MCP protocol to file-based IPC with Abaqus.

Provides tools for script execution, model/job/ODB queries, and viewport capture.
Also exposes the Abaqus connection status as an MCP resource.
"""

import json
import os
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

MCP_HOME = Path(os.environ.get('ABAQUS_MCP_HOME', Path.home() / '.abaqus-mcp'))
COMMANDS_DIR = MCP_HOME / 'commands'
RESULTS_DIR = MCP_HOME / 'results'
STATUS_FILE = MCP_HOME / 'status.json'
TIMEOUT = 30.0

mcp = FastMCP("abaqus-mcp-server")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_command(cmd_type: str, timeout: float = TIMEOUT, **kwargs) -> dict:
    """Write a command file and wait for the result file."""
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cmd_id = uuid.uuid4().hex[:8]
    command = {'id': cmd_id, 'type': cmd_type, 'timestamp': time.time(), **kwargs}

    cmd_path = COMMANDS_DIR / f'cmd_{cmd_id}.json'
    result_path = RESULTS_DIR / f'{cmd_id}.json'

    with open(cmd_path, 'w', encoding='utf-8') as f:
        json.dump(command, f)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if result_path.exists():
            try:
                with open(result_path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
                result_path.unlink(missing_ok=True)
                return result
            except Exception:
                pass
        time.sleep(0.05)

    try:
        cmd_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {'success': False, 'error': f'Timeout: no response from Abaqus in {timeout}s'}


def _read_status() -> dict:
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MCP Resource
# ---------------------------------------------------------------------------

@mcp.resource("abaqus://status")
def abaqus_status() -> str:
    """Current Abaqus MCP plugin status (running / stopped / ready)."""
    status = _read_status()
    if not status:
        return json.dumps({"connected": False, "detail": "status.json not found"})
    return json.dumps(status, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_abaqus_connection() -> str:
    """Check if Abaqus is running and the MCP plugin is loaded and responding."""
    status = _read_status()
    if not status:
        return 'Abaqus MCP plugin not found. Is Abaqus running with the plugin loaded?'

    s = status.get('status', 'unknown')
    msg = status.get('message', '')
    dt = status.get('datetime', '')
    ver = status.get('version', '?')

    if s != 'running':
        return (f'Abaqus plugin loaded but not running (status={s}). '
                f'Run mcp_start() in Abaqus console.')

    result = _send_command('ping', timeout=10.0)
    if result.get('success'):
        ping_data = result.get('data', {})
        pong_ver = ping_data.get('version', ver) if isinstance(ping_data, dict) else ver
        return (f'Connected to Abaqus MCP v{pong_ver}.\n'
                f'Status: {s} — {msg}\nLast update: {dt}')
    else:
        return (f'Abaqus plugin loaded but not responding to commands.\n'
                f'Status: {s} — {msg}\nPing result: {result}\n'
                f'Try running mcp_start() again in Abaqus.')


@mcp.tool()
def execute_script(script: str) -> str:
    """Execute a Python script inside Abaqus/CAE.

    The script runs in the Abaqus kernel environment with access to mdb and session.
    Use print() to return output.
    """
    result = _send_command('execute_script', script=script)
    if result.get('success'):
        output = result.get('output', '')
        return output if output else '(Script executed successfully, no output)'
    else:
        error = result.get('error', 'Unknown error')
        tb = result.get('traceback', '')
        return f'Error: {error}\n{tb}'.strip()


@mcp.tool()
def get_model_info() -> str:
    """Get detailed information about all models in the current Abaqus session.

    Returns parts, materials, steps, loads, BCs, interactions, assembly instances,
    and viewport info.
    """
    result = _send_command('get_model_info')
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        return f'Error: {result.get("error", "Unknown error")}'


@mcp.tool()
def list_jobs() -> str:
    """List all jobs defined in the current Abaqus session with their status."""
    result = _send_command('list_jobs')
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        return f'Error: {result.get("error", "Unknown error")}'


@mcp.tool()
def submit_job(job_name: str) -> str:
    """Submit an Abaqus analysis job by name and wait for completion.

    The job must already be defined in the current Abaqus session (mdb.jobs).
    Returns the final job status.
    """
    result = _send_command('submit_job', timeout=600.0, job_name=job_name)
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        error = result.get('error', 'Unknown error')
        data = result.get('data', {})
        detail = data.get('error', '') if isinstance(data, dict) else ''
        return f'Error: {error}\n{detail}'.strip()


@mcp.tool()
def get_odb_info(odb_path: str) -> str:
    """Open an ODB file (read-only) and return its metadata.

    Returns steps (with frame count and total time), parts, instances, etc.
    Provide the full path to the .odb file.
    """
    result = _send_command('get_odb_info', timeout=60.0, odb_path=odb_path)
    if result.get('success'):
        data = result.get('data', {})
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        error = result.get('error', 'Unknown error')
        data = result.get('data', {})
        detail = data.get('error', '') if isinstance(data, dict) else ''
        return f'Error: {error}\n{detail}'.strip()


@mcp.tool()
def get_viewport_image(viewport_name: str = "", image_format: str = "PNG") -> str:
    """Capture a screenshot of an Abaqus viewport.

    Returns the image as a base64-encoded string.
    Leave viewport_name empty to use the current viewport.
    Supported formats: PNG, SVG, TIFF.
    """
    kwargs = {'format': image_format.upper()}
    if viewport_name:
        kwargs['viewport_name'] = viewport_name
    result = _send_command('get_viewport_image', timeout=30.0, **kwargs)
    if result.get('success'):
        data = result.get('data', {})
        if isinstance(data, dict) and data.get('success'):
            b64 = data.get('image_base64', '')
            fmt = data.get('format', 'png').lower()
            mime_type = {'png': 'image/png', 'svg': 'image/svg+xml',
                         'tif': 'image/tiff', 'tiff': 'image/tiff'}.get(fmt, 'image/' + fmt)
            return f'data:{mime_type};base64,{b64}'
        if not isinstance(data, dict) or not data.get('error'):
            return json.dumps(data, indent=2)
    data = result.get('data', {})
    detail = data.get('error', '') if isinstance(data, dict) else ''
    error = result.get('error') or detail or 'Unknown error'
    return f'Error: {error}'


@mcp.tool()
def ping() -> str:
    """Send a ping to the Abaqus MCP plugin and return pong if alive."""
    result = _send_command('ping', timeout=10.0)
    if result.get('success'):
        data = result.get('data', {})
        if isinstance(data, dict):
            return f'pong (v{data.get("version", "?")})'
        return 'pong'
    return f'No response: {result.get("error", "unknown error")}'


if __name__ == '__main__':
    mcp.run()
