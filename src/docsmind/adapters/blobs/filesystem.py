"""Local-disk `BlobStore`. This is the default (`DOCSMIND_BLOB_BACKEND=filesystem`)
and what the Docker-compose quickstart uses. `adapters/blobs/s3.py` and
`supabase.py` implement the same `BlobStore` protocol for the Neon/Supabase
deployment paths.

File I/O here is synchronous under the hood (plain `open()`), which is fine
for local disk — it's fast enough that adding `aiofiles` as a dependency
just to avoid a few milliseconds of blocking isn't worth it at this stage.
"""

from __future__ import annotations

from pathlib import Path


class FilesystemBlobStore:
    """Satisfies the `BlobStore` protocol: `put(key, data, content_type) -> uri`,
    `get(uri) -> bytes`. URIs are `file://<absolute path>`.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        # content_type isn't needed for a filesystem store (no HTTP headers to
        # set), but every BlobStore implementation accepts it so callers don't
        # need to know which backend they're talking to.
        del content_type
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return f"file://{dest.resolve()}"

    async def get(self, uri: str) -> bytes:
        if not uri.startswith("file://"):
            raise ValueError(f"FilesystemBlobStore can't resolve non-file:// uri: {uri}")
        path = Path(uri[len("file://") :])
        return path.read_bytes()
