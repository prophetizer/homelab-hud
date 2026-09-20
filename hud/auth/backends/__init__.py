# SPDX-License-Identifier: Apache-2.0
"""Identity backends. Each turns an inbound request (or a login flow) into an
:class:`ExternalIdentity`; the service turns that into a :class:`~hud.auth.Principal`."""

from hud.auth.backends.base import ExternalIdentity
from hud.auth.backends.forward import ForwardBackend

__all__ = ["ExternalIdentity", "ForwardBackend"]
