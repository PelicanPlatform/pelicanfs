"""
Copyright (C) 2026, Pelican Project, Morgridge Institute for Research

Licensed under the Apache License, Version 2.0 (the "License"); you
may not use this file except in compliance with the License.  You may
obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

# Calls that differ between aiowebdav2 1.0 and 0.6. aiowebdav2 1.0 requires Python 3.13,
# so older Pythons get 0.6. Results use the attribute names of aiowebdav2.ResourceInfo.
# Remove the 0.6 code with those Pythons.

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from aiowebdav2 import Client, RemoteResourceNotFoundError

_LEGACY = hasattr(Client, "list_with_infos")


@dataclass(frozen=True)
class _LegacyResource:
    path: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    content_type: str | None


def _legacy_resource(info: dict[str, str], path: str, is_dir: bool) -> _LegacyResource:
    try:
        modified = parsedate_to_datetime(info["modified"])
    except ValueError:
        modified = None
    size = info["size"]
    return _LegacyResource(
        path=f"{path.rstrip('/')}/" if is_dir else path,
        is_dir=is_dir,
        size=None if is_dir or not size else int(size),
        modified=modified,
        content_type=info["content_type"] or None,
    )


async def list_resources(client: Client, path: str) -> list[Any]:
    """
    The resources in the collection at `path`, without the collection itself.
    """
    if _LEGACY:
        infos = await client.list_with_infos(path)
        return [_legacy_resource(info, info["path"], info["isdir"] == "True") for info in infos]
    return await client.list(path)


async def stat_resource(client: Client, path: str) -> Any:
    """
    The resource at `path`.
    """
    if _LEGACY:
        from aiowebdav2.parser import WebDavXmlUtils  # pyright: ignore[reportMissingImports]  # noqa: PLC0415
        from aiowebdav2.urn import Urn  # pyright: ignore[reportMissingImports]  # noqa: PLC0415

        # Client.info drops resourcetype, so do its PROPFIND here and parse both
        remote_path = Urn(path).path()
        response = await client.execute_request(action="info", path=remote_path)
        resource = WebDavXmlUtils.extract_response_for_path(content=await response.read(), path=remote_path, hostname=client._url)
        is_dir = resource.find(".//{DAV:}resourcetype/{DAV:}collection") is not None
        return _legacy_resource(WebDavXmlUtils.get_info_from_response(resource), path, is_dir)
    return await client.stat(path)


async def resource_exists(client: Client, path: str) -> bool:
    """
    Whether the server has a resource at `path`.

    Client.exists also requires the reply to name `path`, so a reply that names the
    resource another way, or carries no body, would read as missing. The status settles it.
    """
    if _LEGACY:
        return await client.check(path)
    try:
        await client.request("PROPFIND", path, headers={"Depth": "0"})
    except RemoteResourceNotFoundError:
        return False
    return True
