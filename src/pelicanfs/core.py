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

import fsspec
import fsspec.registry
from fsspec.asyn import AsyncFileSystem
from .dir_header_parser import parse_metalink, get_dirlist_loc
import fsspec.implementations.http as fshttp
import aiohttp
import urllib.parse
import asyncio

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
            directorUrl,
            asynchronous = False,
            loop = None
    ):
        
        # The internal filesystem
        self.httpFileSystem = fshttp.HTTPFileSystem(asynchronous=asynchronous, loop=loop)

        # Ensure the director url ends with a "/"
        if directorUrl[-1] != "/":
            directorUrl = directorUrl + "/"
        self.directorUrl = directorUrl


        super().__init__(self, asynchronous=asynchronous, loop=loop)

        # These are all not implemented in the http fsspec and as such are not implemented in the pelican fsspec
        # They will raise NotImplementedErrors when called
        self._rm_file = self.httpFileSystem._rm_file
        self._cp_file = self.httpFileSystem._cp_file
        self._pipe_file = self.httpFileSystem._pipe_file
        self._mkdir = self.httpFileSystem._mkdir
        self._makedirs = self.httpFileSystem._makedirs
        

        # TODO: These functions are to be implemented. Currently A top level call to glob/du/info will result
        # in a failure
        self._glob = self.httpFileSystem._glob
        self._du = self.httpFileSystem._du
        self._info = self.httpFileSystem._info


    async def get_director_headers(self, fileloc):
        """
        Returns the header response from a GET call to the director
        """
        if fileloc[0] == "/":
            fileloc = fileloc[1:]
        url = self.directorUrl + fileloc
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=False) as resp:
                return resp.headers

    async def get_working_cache(self, fileloc):
        """
        Returns the highest priority cache for the namespace that appears to be owrking
        """
        headers = await self.get_director_headers(fileloc)
        metalist = parse_metalink(headers)[1:]
        while len(metalist) > 0:
            updatedUrl = metalist[0][0]
            metalist = metalist[1:]
            # Timeout response in seconds - the default response is 5 minutes
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession() as session:
                async with session.get(updatedUrl, timeout=timeout) as resp:
                    try:
                        resp.status
                    except (aiohttp.client_exceptions.ClientConnectorError, FileNotFoundError, asyncio.TimeoutError):
                        continue
                    break
        if len(metalist) == 0:
            # No working cache was found
            raise RuntimeError
        
        return updatedUrl

    
    def _dirlist_dec(func):
        """
        Decorator function which, when given a namespace location, get the url for the dirlist location from the headers
        and uses that url for the given function.

        This is for functions which need to list information in the origin directories such as "find", "isdir", "ls"
        """
        async def wrapper(self, *args, **kwargs):
            path = args[0]
            parsedUrl = urllib.parse.urlparse(path)
            headers = await self.get_director_headers(parsedUrl.path)
            dirlistloc = get_dirlist_loc(headers)
            if dirlistloc == None:
                raise RuntimeError
            listUrl = dirlistloc + "/" + parsedUrl.path
            result = await func(self, listUrl, *args[1:], **kwargs)
            return result
        return wrapper

    @_dirlist_dec
    async def _ls(self, path, detail=True, **kwargs):
        return await self.httpFileSystem._ls(path, detail, **kwargs)

    @_dirlist_dec
    async def _isdir(self, path):
        return await self.httpFileSystem._isdir(path)
    
    @_dirlist_dec
    async def _find(self, path, maxdepth=None, withdirs=False, **kwargs):
        return await self.httpFileSystem._find(path, maxdepth, withdirs, **kwargs)
    
    # Not using a decorator because it requires a yield
    async def _walk(self, path, maxdepth=None, on_error="omit", **kwargs):
        parsedUrl = urllib.parse.urlparse(path)
        headers = await self.get_director_headers(parsedUrl.path)
        dirlistloc = get_dirlist_loc(headers)
        if dirlistloc == "":
            raise RuntimeError
        listUrl = dirlistloc + "/" + path
        async for _ in self.httpFileSystem._walk(listUrl, maxdepth, on_error, **kwargs):
                yield _


    def _open(
        self,
        path,
        mode="rb",
        block_size=None,
        autocommit=None,  # XXX: This differs from the base class.
        cache_type=None,
        cache_options=None,
        size=None,
        **kwargs,
    ):    
        loop = asyncio.get_event_loop()
        cache_url = loop.run_until_complete(self.get_working_cache(path))

        return self.httpFileSystem._open(cache_url, mode, block_size, autocommit, cache_type, cache_options, size, **kwargs)
    


    def _cache_dec(func):
        """
        Decorator function which, when given a namespace location, finds the best working cache that serves the namespace,
        then calls the sub function with that namespace


        Note: This will find the nearest cache even if provided with a valid url. The reason being that if that url was found
        via an "ls" call, then that url points to an origin, not the cache. So it cannot be assumed that a valid url points to
        a cache
        """
        async def wrapper(self, *args, **kwargs):
            path = args[0]
            parsedUrl = urllib.parse.urlparse(path)
            if parsedUrl.scheme == "http":
                cacheUrl = path
            else:
                cacheUrl = await self.get_working_cache(parsedUrl.path)
            result = await func(self, cacheUrl, *args[1:], **kwargs)
            return result
        return wrapper
    
    def _cache_multi_dec(func):
        """
        Decorator function which, when given a list of namespace location, finds the best working cache that serves the namespace,
        then calls the sub function with that namespace


        Note: If a valid url is provided, it will not call the director to get a cache. This does mean that if a url was created/retrieved via
        ls and then used for another function, the url will be an origin url and not a cache url. This should be fixed in the future.
        """
        async def wrapper(self, *args, **kwargs):
            path = args[0]
            if isinstance(path, str):
                parsedUrl = urllib.parse.urlparse(path)
                if parsedUrl.scheme == "http":
                    cacheUrl = path
                else:
                    cacheUrl = await self.get_working_cache(parsedUrl.path)
            else:
                cacheUrl = []
                for p in path:
                    parsedUrl = urllib.parse.urlparse(p)
                    if parsedUrl.scheme == "http":
                        cUrl = p
                    else:
                        cUrl = cacheUrl = await self.get_working_cache(parsedUrl.path)
                    cacheUrl.append(cUrl)
            result = await func(self, cacheUrl, *args[1:], **kwargs)
            return result
        return wrapper

    @_cache_dec
    async def _cat_file(self, path, start=None, end=None, **kwargs):
        return await self.httpFileSystem._cat_file(path, start, end, **kwargs)

    @_cache_dec
    async def _exists(self, path, **kwargs):
        return await self.httpFileSystem._exists(path, **kwargs)
    
    @_cache_dec
    async def _isfile(self, path, **kwargs):
        return await self.httpFileSystem._isfile(path, **kwargs)
    
    @_cache_dec
    async def _get_file(self, rpath, lpath, **kwargs):
        return await self.httpFileSystem._get_file(rpath, lpath, **kwargs)
    

    @_cache_multi_dec
    async def _cat(self, path, recursive=False, on_error="raise", batch_size=None, **kwargs):
        return await self.httpFileSystem._cat(path, recursive, on_error, batch_size, **kwargs)

    @_cache_multi_dec
    async def _expand_path(self, path, recursive=False, maxdepth=None):
        return await self.httpFileSystem._expand_path(path, recursive, maxdepth)
    

fsspec.register_implementation(PelicanFileSystem.protocol, PelicanFileSystem)

def PelicanMap(root, pelfs, check=False, create=False):
    loop = asyncio.get_event_loop()
    cache_url = loop.run_until_complete(pelfs.get_working_cache(root))

    return pelfs.get_mapper(cache_url, check=check, create=create)