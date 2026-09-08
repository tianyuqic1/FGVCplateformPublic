from __future__ import annotations

__all__ = ["app", "create_app"]


def create_app(*args, **kwargs):
    """Load the legacy FastAPI application only when it is explicitly requested."""
    from .app import create_app as factory

    return factory(*args, **kwargs)


def __getattr__(name: str):
    if name == "app":
        from .app import app

        return app
    raise AttributeError(name)
