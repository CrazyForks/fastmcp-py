"""Registry for FastMCP-internal client extensions (SEP-2133).

Core ships the client wiring for opt-in extensions but no extension of its own.
A companion package (``fastmcp-tasks``) provides an extension the ``Client``
should register *automatically* — so an ordinary ``Client(url)`` transparently
drives a server's background tasks without the caller passing anything. The
package cannot reach into core's ``Client`` constructor, so core exposes this
hook instead: the package registers a factory on import, and ``Client`` folds
the factory's extension in alongside the user's own.

This mirrors the server-side ``set_background_context_factory`` hook: core
declares the extension point, the tasks package fills it. With no package
imported, the registry is empty and ``Client`` behaves exactly as before.

A factory receives the client's elicitation callback (so a task resolver can
answer in-task input prompts) and returns a ``ClientExtension`` to register, or
``None`` to contribute nothing for this client.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.client.extension import ClientExtension
    from mcp.client.session import ElicitationFnT

#: A factory that builds a FastMCP-internal client extension for one ``Client``,
#: given that client's elicitation callback (``None`` when the client has no
#: elicitation handler).
InternalClientExtensionFactory = Callable[
    ["ElicitationFnT | None"], "ClientExtension | None"
]

_internal_client_extension_factories: list[InternalClientExtensionFactory] = []


def register_internal_client_extension_factory(
    factory: InternalClientExtensionFactory,
) -> None:
    """Register a factory whose extension every ``Client`` folds in automatically.

    Idempotent: registering the same factory object twice is a no-op, so a
    package importing more than once does not double-register.
    """
    if factory not in _internal_client_extension_factories:
        _internal_client_extension_factories.append(factory)


def build_internal_client_extensions(
    elicitation_callback: ElicitationFnT | None,
) -> list[ClientExtension]:
    """Build the internal extensions to fold into a ``Client`` under construction.

    Each registered factory is invoked with the client's elicitation callback;
    factories that return ``None`` contribute nothing. Empty when no package has
    registered a factory (plain core).
    """
    extensions: list[ClientExtension] = []
    for factory in _internal_client_extension_factories:
        extension = factory(elicitation_callback)
        if extension is not None:
            extensions.append(extension)
    return extensions
