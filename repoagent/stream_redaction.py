"""Incremental literal-secret filtering with a retained ambiguous suffix."""

from .security import REDACTED_VALUE


class SecretTextStream:
    def __init__(self, secrets):
        self.secrets = tuple(
            sorted({value for value in secrets if value}, key=len, reverse=True)
        )
        self.pending = ""
        self.closed = False

    def feed(self, text):
        if self.closed:
            return ""
        self.pending += str(text)
        return self._drain(complete=False)

    def _drain(self, *, complete):
        output, index = [], 0
        while index < len(self.pending):
            remaining = len(self.pending) - index
            if not complete and any(
                len(secret) > remaining and secret.startswith(self.pending[index:])
                for secret in self.secrets
            ):
                break
            match = next(
                (
                    secret
                    for secret in self.secrets
                    if self.pending.startswith(secret, index)
                ),
                None,
            )
            if match is not None:
                output.append(REDACTED_VALUE)
                index += len(match)
            else:
                output.append(self.pending[index])
                index += 1
        self.pending = self.pending[index:]
        return "".join(output)

    def discard(self):
        self.pending = ""

    def finish(self, *, complete=False):
        """Release unmatched tails only at a confirmed normal end of text."""
        if self.closed:
            return ""
        self.closed = True
        if complete:
            return self._drain(complete=True)
        self.discard()
        return ""
