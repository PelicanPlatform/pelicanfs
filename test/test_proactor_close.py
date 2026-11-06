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

from asyncio.proactor_events import _ProactorBasePipeTransport
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_socket_closes_when_shutdown_fails():
    sock = MagicMock()
    sock.shutdown.side_effect = ConnectionResetError(10054, "reset by peer")
    transport = SimpleNamespace(_called_connection_lost=False, _protocol=MagicMock(), _sock=sock, _server=None)

    _ProactorBasePipeTransport._call_connection_lost(transport, None)

    sock.close.assert_called_once_with()
    assert transport._sock is None
    assert transport._called_connection_lost
