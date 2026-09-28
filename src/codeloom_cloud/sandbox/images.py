from __future__ import annotations

RUNTIMES = ("python", "node", "golang")

# GitHub's primary language, lowercased. Anything else still boots the
# harness, on the python image, and the engine reports that LSP is unavailable.
_FROM_LANGUAGE = {
    "python": "python",
    "go": "golang",
    "golang": "golang",
    "javascript": "node",
    "typescript": "node",
    "tsx": "node",
    "jsx": "node",
    "node": "node",
    "nodejs": "node",
}


def runtime_for_language(language: str | None) -> str:
    if not language:
        return "python"
    return _FROM_LANGUAGE.get(language.strip().lower(), "python")


def image_for(sandbox_image: str, runtime: str) -> str:
    """Swap the image tag for the sandbox runtime.

    ``codeloom-sandbox:main`` and ``ghcr.io/org/codeloom-sandbox:python`` both
    keep their repository and take ``python``, ``node``, or ``golang`` as the
    tag. A registry port (``localhost:5000/codeloom-sandbox``) is not a tag.
    """
    if runtime not in RUNTIMES:
        runtime = "python"
    _repo, separator, tag = sandbox_image.rpartition(":")
    if not separator or "/" in tag:
        return f"{sandbox_image}:{runtime}"
    return f"{_repo}:{runtime}"
