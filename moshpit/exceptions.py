class MoshpitException(Exception):
    """Base exception for all Moshpit Mauler operations."""

    pass


class PlatformNotSupportedError(MoshpitException):
    """Raised when the host platform is not compatible with requirements (e.g., non-macOS)."""

    pass


class JXAError(MoshpitException):
    """Raised when execution of the osascript JXA subprocess fails."""

    pass


class MusicAppException(MoshpitException):
    """Raised when a Music.app operation or database query fails."""

    pass
