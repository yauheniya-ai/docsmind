"""End to end, nothing mocked: ingest with the real CLI, start the real server, read the document
over HTTP, and load the bundled UI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

CLI = "from docsmind.cli.app import main; main()"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def env(postgres_url, tmp_path):
    # tmp_path as cwd: a developer's own .env must not leak into the run.
    return {
        **os.environ,
        "DOCSMIND_DATABASE_URL": postgres_url,
        "DOCSMIND_BLOB_ROOT": str(tmp_path / "blobs"),
    }


def _cli(env, tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-c", CLI, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False
    )


@pytest.fixture
def server(env, tmp_path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", CLI, "serve", "--port", str(port)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.5)
        else:
            pytest.fail("the server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_ingest_with_the_cli_then_read_it_through_the_server(env, tmp_path, make_pdf, server):
    pdf = make_pdf("Access reviews happen every quarter.")

    ingested = _cli(env, tmp_path, "ingest", str(pdf), "--collection", "audit", "--version", "q4")
    assert ingested.returncode == 0, ingested.stderr
    assert "Ingested" in ingested.stdout

    documents = httpx.get(f"{server}/api/documents").json()
    mine = next(d for d in documents if d["collection"] == "audit" and d["label"] == "q4")
    fulltext = httpx.get(f"{server}/api/documents/{mine['id']}/fulltext").json()["full_text"]
    assert "Access reviews" in fulltext

    page = httpx.get(server)
    assert page.status_code == 200 and "<html" in page.text.lower()  # the bundled UI


def test_the_cli_reports_its_version(env, tmp_path):
    result = _cli(env, tmp_path, "version")
    assert result.returncode == 0 and result.stdout.strip()[0].isdigit()
