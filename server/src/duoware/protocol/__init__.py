"""Codecs for every wire format in PROTOCOL.md. Code follows the document, never the other way round."""

from duoware import PROTOCOL_VERSION  # noqa: F401  (PROTOCOL.md §0: "v": 1)


class ProtocolError(Exception):
    """A message broke PROTOCOL.md. `code` is the short reason: `bad`, `version`, `too_big` for phone
    messages (§2), `parse`, `range`, `long`, `unknown` for car command lines (§5.4)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
