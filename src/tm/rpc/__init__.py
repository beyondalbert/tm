"""Embedding interface: ``tm serve --stdio``.

The protocol is JSON-RPC 2.0 over newline-delimited JSON. See
``docs/design/embedding.md``.
"""

from __future__ import annotations

from tm.rpc.protocol import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION"]
