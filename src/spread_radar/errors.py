class UpstreamApiError(RuntimeError):
    """A data provider could not satisfy a read-only request."""


class NotificationError(RuntimeError):
    """A notification provider could not deliver a message safely."""
