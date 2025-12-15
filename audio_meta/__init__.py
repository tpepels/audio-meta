"Audio metadata correction daemon package."

from importlib import metadata

__all__ = ["__version__"]


def __getattr__(name: str) -> str:
    if name == "__version__":
        try:
            return metadata.version("audio-meta")
        except metadata.PackageNotFoundError:  # pragma: no cover - during editable dev installs
            return "0.0.0"
    raise AttributeError(name)
