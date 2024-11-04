"""
Copyright (C) 2024, Pelican Project, Morgridge Institute for Research
 
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

import asyncio
import logging
import threading
import urllib.parse
import re


import cachetools
import aiohttp
from fsspec.utils import glob_translate
from fsspec.asyn import AsyncFileSystem, sync
import fsspec.implementations.http as fshttp

from .dir_header_parser import parse_metalink

logger = logging.getLogger("fsspec.pelican")

class PelicanException(RuntimeError):
    """
    Base class for all Pelican-related failures
    """

class BadDirectorResponse(PelicanException):
    """
    The director response did not include Link Headers
    """

class NoAvailableSource(PelicanException):
    """
    No source endpoint is currently available for the requested object
    """

class InvalidMetadata(PelicanException):
    """
    No Pelican metadata was found for the federation
    """

class _CacheManager(object):
    """
    Manage a list of caches.

    Each entry in the namespace has an associated list of caches that are willing
    to provide services to the client.  As the caches are used, if they timeout
    or otherwise cause errors, they should be skipped for future operations.
    """

    def __init__(self, cache_list):
        """
        Construct a new cache manager from an ordered list of cache URL strings.
        The cache URL is assumed to have the form of:
            scheme://hostname[:port]
        e.g., https://cache.example.com:8443 or http://cache2.example.com

        The list ordering is assumed to be the order of preference; the first cache
        in the list will be used until it's explicitly noted as bad.
        """
        self._lock = threading.Lock()
        self._cache_list = []
        # Work around any bugs where the director may return the same cache twice
        cache_set = set()
        for cache in cache_list:
            parsed_url = urllib.parse.urlparse(cache)
            parsed_url = parsed_url._replace(path="", query="", fragment="")
            cache_str = parsed_url.geturl()
            if cache_str in cache_set:
                continue
            cache_set.add(cache_str)
            self._cache_list.append(parsed_url.geturl())

    def get_url(self, obj_name):
        """
        Given an object name, return the currently-preferred cache
        """
        with self._lock:
            if not self._cache_list:
                raise NoAvailableSource()

            return urllib.parse.urljoin(self._cache_list[0], obj_name)

    def bad_cache(self, cache_url: str):
        """
        If a bad cache is found, remove it from the list of possible caches to use
        """
        cache_url_parsed = urllib.parse.urlparse(cache_url)
        cache_url_parsed = cache_url_parsed._replace(path="", query="", fragment="")
        with self._lock:
            self._cache_list.remove(cache_url_parsed.geturl())


class PelicanFileSystem(AsyncFileSystem):
    """
    Access a pelican namespace as if it were a file system.

    This exposes a filesystem-like API (ls, cp, open, etc.) on top of pelican

    It works by composing with an http fsspec. Whenever a function call
    is made to the PelicanFileSystem, it will call out to the director to get
    an appropriate url for the given call. This url is then passed on to the 
    http fsspec which will handle the actual logic of the function.

    NOTE: Once a url is passed onto the http fsspec, that url will be the one
    used for all sub calls within the http fsspec.
    """

    protocol = "pelican"


    def __init__ (
            self,
            federationDiscoveryUrl="",
            direct_reads = False,
            preferred_caches = None,
            asynchronous = False,
            loop = None,
            **kwargs
    ):
        super().__init__(self, asynchronous=asynchronous, loop=loop, **kwargs)

        self._namespace_cache = cachetools.TTLCache(maxsize=50, ttl=15*60)
        self._namespace_lock = threading.Lock()

        self.token = kwargs.get('headers', {}).get('Authorization')

        # The internal filesystem
        self.http_file_system = fshttp.HTTPFileSystem(asynchronous=asynchronous,
                                                      loop=loop,
                                                      **kwargs)

        self.discovery_url = federationDiscoveryUrl
        self.director_url = ""

        self.direct_reads = direct_reads
        self.preferred_caches = preferred_caches

        # These are all not implemented in the http fsspec and as such are
        # not implemented in the pelican fsspec
        # They will raise NotImplementedErrors when called
        self._rm_file = self.http_file_system._rm_file
        self._cp_file = self.http_file_system._cp_file
        self._pipe_file = self.http_file_system._pipe_file
        self._mkdir = self.http_file_system._mkdir
        self._makedirs = self.http_file_system._makedirs

    # Note this is a class method because it's overwriting a class method for the AbstractFileSystem
    @classmethod
    def _strip_protocol(cls, path):
        """For HTTP, we always want to keep the full URL"""
        if path.startswith("osdf://"):
            path = path[7:]
        elif path.startswith("pelican://"):
            path = path[10:]
        return path

    @staticmethod
    def _remove_host_from_path(path):
        parsed_url = urllib.parse.urlparse(path)
        updated_url = parsed_url._replace(netloc="", scheme="")
        return urllib.parse.urlunparse(updated_url)

    @staticmethod
    def _remove_host_from_paths(paths):
        if isinstance(paths, list):
            return [PelicanFileSystem._remove_host_from_paths(path) for path in paths]


        if isinstance(paths, dict):
            if 'name' in paths:
                path = paths['name']
                paths['name'] = PelicanFileSystem._remove_host_from_path(path)
                if 'url' in paths:
                    url = paths['url']
                    paths['url'] = PelicanFileSystem._remove_host_from_path(url)
                    return paths
            else:
                new_dict = {}
                for key, item in paths.items():
                    new_key = PelicanFileSystem._remove_host_from_path(key)
                    new_item = PelicanFileSystem._remove_host_from_paths(item)
                    new_dict[new_key] = new_item
                return new_dict

        if isinstance(paths, str):
            return PelicanFileSystem._remove_host_from_path(paths)

        return paths

    async def _discover_federation_metadata(self, disc_url):
        """
        Returns the json response from a GET call to the metadata discovery url of the federation
        """
        # Parse the url for federation discovery
        discovery_url = urllib.parse.urlparse(disc_url)
        discovery_url = discovery_url._replace(scheme="https",
                                               path="/.well-known/pelican-configuration")
        session = await self.http_file_system.set_session()
        async with session.get(discovery_url.geturl()) as resp:
            if resp.status != 200:
                raise InvalidMetadata()
            return await resp.json(content_type="")

    async def get_director_headers(self, fileloc, origin=False) -> dict[str, str]:
        """
        Returns the header response from a GET call to the director
        """
        if fileloc[0] == "/":
            fileloc = fileloc[1:]

        if not self.director_url:
            metadata_json = await self._discover_federation_metadata(self.discovery_url)
            # Ensure the director url has a '/' at the end
            director_url = metadata_json.get('director_endpoint')
            if not director_url:
                raise InvalidMetadata()

            if not director_url.endswith("/"):
                director_url = director_url + "/"
            self.director_url = director_url

        if origin:
            url = urllib.parse.urljoin(self.director_url, "/api/v1.0/director/origin/") + fileloc
        else:
            url = urllib.parse.urljoin(self.director_url, fileloc)
        session = await self.http_file_system.set_session()
        async with session.get(url, allow_redirects=False) as resp:
            return resp.headers

    async def get_working_cache(self, fileloc: str) -> str:
        """
        Returns the highest priority cache for the namespace that appears to be working
        """
        namespace = None
        fparsed = urllib.parse.urlparse(fileloc)
        # Removing the query if need be
        cache_url = self._match_namespace(fparsed.path)
        if cache_url:
            return cache_url

        # Calculate the list of applicable caches; this takes into account the
        # preferredCaches for the filesystem.  If '+' is a preferred cache, we
        # add all the director-provided caches to the list (doing a round of de-dup)
        cache_list = []
        if self.preferred_caches:
            cache_list = [urllib.parse.urlparse(urllib.parse.urljoin(cache, fileloc))\
                          ._replace(query=fparsed.query).geturl()
                          if cache != "+" else "+" for cache in self.preferred_caches]
            namespace = "/"
        if not self.preferred_caches or ("+" in self.preferred_caches):
            headers = await self.get_director_headers(fileloc)
            metalist, namespace = parse_metalink(headers)
            old_cache_list = cache_list
            cache_list = []
            cache_set = set()
            new_caches = [urllib.parse.urlparse(entry[0])._replace(query=fparsed.query).geturl() for
                          entry in metalist]
            for cache in old_cache_list:
                if cache == "+":
                    for cache2 in new_caches:
                        if cache2 not in cache_set:
                            cache_set.add(cache2)
                            cache_list.append(cache2)
                else:
                    cache_list.append(cache)
            if not cache_list:
                cache_list = new_caches

        while cache_list:
            updated_url = cache_list[0]
            # Timeout response in seconds - the default response is 5 minutes
            timeout = aiohttp.ClientTimeout(total=5)
            session = await self.http_file_system.set_session()
            if self.token:
                session.headers["Authorization"] = self.token
            try:
                async with session.head(updated_url, timeout=timeout) as resp:
                    if resp.status >= 200 and resp.status < 400:
                        break
            except (aiohttp.client_exceptions.ClientConnectorError,
                    FileNotFoundError,
                    asyncio.TimeoutError,
                    asyncio.exceptions.TimeoutError):
                pass
            cache_list = cache_list[1:]

        if not cache_list:
            # No working cache was found
            raise NoAvailableSource()

        with self._namespace_lock:
            self._namespace_cache[namespace] = _CacheManager(cache_list)

        return updated_url

    async def get_origin_url(self, fileloc: str) -> str:
        """
        Returns an origin url for the given namespace location
        """
        headers = await self.get_director_headers(fileloc, origin=True)
        origin = headers.get("Location")
        if not origin:
            raise NoAvailableSource()
        return origin

    async def get_dirlist_url(self, fileloc: str) -> str:
        """
        Returns a dirlist host url for the given namespace locations
        """

        if not self.director_url:
            metadata_json = await self._discover_federation_metadata(self.discovery_url)
            # Ensure the director url has a '/' at the end
            director_url = metadata_json.get('director_endpoint')
            if not director_url:
                raise InvalidMetadata()

            if not director_url.endswith("/"):
                director_url = director_url + "/"
            self.director_url = director_url

        url = urllib.parse.urljoin(self.director_url, fileloc)

        # Timeout response in seconds - the default response is 5 minutes
        timeout = aiohttp.ClientTimeout(total=5)
        session = await self.http_file_system.set_session()
        async with session.request('PROPFIND',
                                   url,
                                   timeout=timeout,
                                   allow_redirects = False) as resp:
            if 'Link' not in resp.headers:
                raise BadDirectorResponse()
            dirlist_url = parse_metalink(resp.headers)[0][0][0]
        if not dirlist_url:
            raise NoAvailableSource()
        return dirlist_url

    def _get_prefix_info(self, path: str) -> _CacheManager:
        """
        Given a path into the filesystem, return the information inthe
        namespace cache (if any)
        """
        namespace_info = None
        with self._namespace_lock:
            prefixes = list(self._namespace_cache.keys())
            prefixes.sort(reverse=True)
            for prefix in prefixes:
                if path.startswith(prefix):
                    namespace_info = self._namespace_cache.get(prefix)
                    break
        return namespace_info

    def _match_namespace(self, fileloc: str):
        namespace_info = self._get_prefix_info(fileloc)
        if not namespace_info:
            return

        return namespace_info.get_url(fileloc)

    def _bad_cache(self, url: str):
        """
        Given a URL of a cache transfer that failed, record
        the corresponding cache as a "bad cache" in the namespace
        cache.
        """
        cache_url = urllib.parse.urlparse(url)
        path = cache_url.path
        cache_url = cache_url._replace(query="", path="", fragment="")
        bad_cache = cache_url.geturl()

        namespace_info = self._get_prefix_info(path)
        if not namespace_info:
            return
        namespace_info.bad_cache(bad_cache)

    @staticmethod
    def _dirlist_dec(func):
        """
        Decorator function which, when given a namespace location, gets the url for the dirlist
        location from the headers and uses that url for the given function. It then normalizes
        the paths or list of paths returned by the function

        This is for functions which need to retrieve information from origin directories
        such as "find", "ls", "info", etc.
        """
        async def wrapper(self, *args, **kwargs):
            path = self._check_fspath(args[0]) # pylint: disable=protected-access
            data_url = await self.get_dirlist_url(path)
            return await func(self, data_url, *args[1:], **kwargs)
        return wrapper

    @_dirlist_dec
    async def _ls(self, path, detail=True, **kwargs):
        results = await self.http_file_system._ls(path, detail, **kwargs) # pylint: disable=protected-access
        return self._remove_host_from_paths(results)

    @_dirlist_dec
    async def _isdir(self, path):
        return await self.http_file_system._isdir(path) # pylint: disable=protected-access

    @_dirlist_dec
    async def _find(self, path, maxdepth=None, withdirs=False, **kwargs):
        results = await self.http_file_system._find(path, maxdepth, withdirs, **kwargs) # pylint: disable=protected-access
        return self._remove_host_from_paths(results)

    async def _isfile(self, path):
        return not await self._isdir(path)

    async def _glob(self, path, maxdepth=None, **kwargs):
        """
        Find files by glob-matching.

        This implementation is based of the one in HTTPSFileSystem,
        except it cleans the path url of double '//' and checks for
        the dirlisthost ahead of time
        """
        if maxdepth is not None and maxdepth < 1:
            raise ValueError("maxdepth must be at least 1")

        ends_with_slash = path.endswith("/")  # _strip_protocol strips trailing slash
        path = self._strip_protocol(path)
        append_slash_to_dirname = ends_with_slash or path.endswith(("/**", "/*"))
        idx_star = path.find("*") if path.find("*") >= 0 else len(path)
        idx_brace = path.find("[") if path.find("[") >= 0 else len(path)

        min_idx = min(idx_star, idx_brace)

        detail = kwargs.pop("detail", False)

        if not fshttp.has_magic(path):
            if await self._exists(path, **kwargs):
                if not detail:
                    return [path]
                else:
                    return {path: await self._info(path, **kwargs)}
            else:
                if not detail:
                    return []  # glob of non-existent returns empty
                else:
                    return {}
        elif "/" in path[:min_idx]:
            min_idx = path[:min_idx].rindex("/")
            root = path[: min_idx + 1]
            depth = path[min_idx + 1 :].count("/") + 1
        else:
            root = ""
            depth = path[min_idx + 1 :].count("/") + 1

        if "**" in path:
            if maxdepth is not None:
                idx_double_stars = path.find("**")
                depth_double_stars = path[idx_double_stars:].count("/") + 1
                depth = depth - depth_double_stars + maxdepth
            else:
                depth = None

        allpaths = await self._find(
            root, maxdepth=depth, withdirs=True, detail=True, **kwargs
        )

        pattern = glob_translate(path + ("/" if ends_with_slash else ""))
        pattern = re.compile(pattern)

        out = {
            (
                p.rstrip("/")
                if not append_slash_to_dirname
                and info["type"] == "directory"
                and p.endswith("/")
                else p
            ): info
            for p, info in sorted(allpaths.items())
            if pattern.match(p.rstrip("/"))
        }

        if detail:
            return out
        else:
            return list(out)

    @_dirlist_dec
    async def _du(self, path, total=True, maxdepth=None, **kwargs):
        return await self.http_file_system._du(path, total, maxdepth, **kwargs) # pylint: disable=protected-access

    # Not using a decorator because it requires a yield
    async def _walk(self, path, maxdepth=None, on_error="omit", **kwargs):
        path = self._check_fspath(path)
        list_url = await self.get_dirlist_url(path)
        async for _ in self.http_file_system._walk(list_url, maxdepth, on_error, **kwargs): # pylint: disable=protected-access
            yield self._remove_host_from_path(_)

    def _io_wrapper(self, func):
        """
        A wrapper around calls to the file which intercepts
        failures and marks the corresponding cache as bad
        """
        def io_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except:
                self._bad_cache(args[0])
                raise
        return io_wrapper

    def _async_io_wrapper(self, func):
        """
        An async wrapper around calls to the file which intercepts
        failures and marks the corresponding cache as bad
        """
        async def io_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except:
                self._bad_cache(args[0])
                raise

        return io_wrapper

    def _check_fspath(self, path: str) -> str:
        """
        Given a path (either absolute or a pelican://-style URL),
        check that the pelican://-style URL is compatible with the current
        filesystem object and return the path.
        """
        if not path.startswith("/"):
            pelican_url = urllib.parse.urlparse("pelican://" + path)
            discovery_url = pelican_url._replace(path="/", fragment="", query="", params="")
            discovery_str = discovery_url.geturl()
            if not self.discovery_url:
                self.discovery_url = discovery_str
            elif self.discovery_url != discovery_str:
                raise InvalidMetadata()
            path = pelican_url.path
        return path

    def open(self, path, mode, **kwargs):
        path = self._check_fspath(path)
        data_url = sync(self.loop, self.get_origin_url if
                        self.direct_reads else
                        self.get_working_cache, path)
        fp = self.http_file_system.open(data_url, mode, **kwargs)
        fp.read = self._io_wrapper(fp.read)
        return fp

    async def open_async(self, path, **kwargs):
        path = self._check_fspath(path)
        if self.direct_reads:
            data_url = await self.get_origin_url(path)
        else:
            data_url = self.get_working_cache(path)
        fp = await self.http_file_system.open_async(data_url, **kwargs)
        fp.read = self._async_io_wrapper(fp.read)
        return fp

    @staticmethod
    def _cache_dec(func):
        """
        Decorator function which, when given a namespace location, finds the best working cache
        that serves the namespace, then calls the sub function with that namespace


        Note: This will find the nearest cache even if provided with a valid url. The reason being 
        that if that url was found via an "ls" call, then that url points to an origin, not the
        cache. So it cannot be assumed that a valid url points to a cache
        """
        async def wrapper(self, *args, **kwargs):
            path = self._check_fspath(args[0]) # pylint: disable=protected-access
            if self.directReads:
                data_url = await self.get_origin_url(path)
            else:
                data_url = await self.get_working_cache(path)
            try:
                result = await func(self, data_url, *args[1:], **kwargs)
            except:
                self._bad_cache(data_url) # pylint: disable=protected-access
                raise
            return result
        return wrapper

    @staticmethod
    def _cache_multi_dec(func):
        """
        Decorator function which, when given a list of namespace location, finds the best
        working cache that serves the namespace, then calls the sub function with that namespace


        Note: If a valid url is provided, it will not call the director to get a cache. 
        This does mean that if a url was created/retrieved via ls and then used for another 
        function, the url will be an origin url and not a cache url.
        
        TODO: Fix the above
        """
        async def wrapper(self, *args, **kwargs):
            path = args[0]
            if isinstance(path, str):
                path = self._check_fspath(args[0]) # pylint: disable=protected-access
                if self.directReads:
                    data_url = await self.get_origin_url(path)
                else:
                    data_url = await self.get_working_cache(path)
            else:
                data_url = []
                for p in path:
                    p = self._check_fspath(p) # pylint: disable=protected-access
                    if self.directReads:
                        d_url = await self.get_origin_url(p)
                    else:
                        d_url =  await self.get_working_cache(p)
                    data_url.append(d_url)
            try:
                result = await func(self, data_url, *args[1:], **kwargs)
            except:
                if isinstance(data_url, list):
                    for d_url in data_url:
                        self._bad_cache(d_url) # pylint: disable=protected-access
                else:
                    self._bad_cache(data_url) # pylint: disable=protected-access
                raise
            return result
        return wrapper


    @_cache_dec
    async def _cat_file(self, path, start=None, end=None, **kwargs):
        return await self.http_file_system._cat_file(path, start, end, **kwargs) # pylint: disable=protected-access

    @_cache_dec
    async def _exists(self, path, **kwargs):
        return await self.http_file_system._exists(path, **kwargs) # pylint: disable=protected-access

    @_cache_dec
    async def _get_file(self, rpath, lpath, **kwargs):
        return await self.http_file_system._get_file(rpath, lpath, **kwargs) # pylint: disable=protected-access

    @_cache_dec
    async def _info(self, path, **kwargs):
        results =  await self.http_file_system._info(path, **kwargs) # pylint: disable=protected-access
        return self._remove_host_from_paths(results)

    @_cache_dec
    async def _get(self, rpath, lpath, **kwargs):
        results = await self.http_file_system._get(rpath, lpath, **kwargs) # pylint: disable=protected-access
        return self._remove_host_from_paths(results)

    @_cache_multi_dec
    async def _cat(self, path, recursive=False, on_error="raise", batch_size=None, **kwargs):
        results = await self.http_file_system._cat(path, recursive, on_error, batch_size, **kwargs) # pylint: disable=protected-access
        return self._remove_host_from_paths(results)

    @_cache_multi_dec
    async def _expand_path(self, path, recursive=False, maxdepth=None):
        return await self.http_file_system._expand_path(path, recursive, maxdepth) # pylint: disable=protected-access

class OSDFFileSystem(PelicanFileSystem):
    """
    A FSSpec AsyncFileSystem representing the OSDF
    """

    protocol = "osdf"

    def __init__(self, **kwargs):
        super().__init__("pelican://osg-htc.org", **kwargs)

def PelicanMap(root, pelfs: PelicanFileSystem, check=False, create=False): # pylint: disable=invalid-name
    """
    Returns and FSMap object assigning creating a mutable mapper at the root location
    """
    return pelfs.get_mapper(root, check=check, create=create)
