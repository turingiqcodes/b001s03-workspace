"""Exception hierarchy for GitHub API failures.

Every message is written for the end user (usually an AI agent) and never
includes credentials.
"""

from __future__ import annotations


class GitHubError(Exception):
    """Base class for all errors raised by the GitHub client."""


class AuthenticationError(GitHubError):
    """Credentials are missing, invalid, or could not be exchanged for a token."""


class GitHubAPIError(GitHubError):
    def __init__(self, status_code: int, message: str, method: str, path: str):
        self.status_code = status_code
        self.method = method
        self.path = path
        super().__init__(f"GitHub API error {status_code} for {method} {path}: {message}")


class NotFoundError(GitHubAPIError):
    def __init__(self, method: str, path: str, detail: str = "Not Found"):
        GitHubError.__init__(
            self,
            f"Not found: {method} {path} returned 404 ({detail}). Check the owner, repository, "
            "number/ID and ref, and that the token has access to this resource.",
        )
        self.status_code = 404
        self.method = method
        self.path = path


class RateLimitError(GitHubAPIError):
    def __init__(self, method: str, path: str, message: str, reset_at: str | None = None):
        self.reset_at = reset_at
        super().__init__(429, message, method, path)


class NetworkError(GitHubError):
    """The request could not be completed due to a transport-level failure."""
