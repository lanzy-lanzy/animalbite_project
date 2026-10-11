"""Static file storage backends.

Why the dev backend exists
--------------------------
Django's ``runserver`` static handler (``django.contrib.staticfiles.handlers.
StaticFilesHandler``) answers ``/static/...`` requests itself and *never runs the
middleware chain* for them: ``load_middleware()`` is a deliberate no-op and
``get_response()`` just calls ``serve()``. So no ``Cache-Control`` header can be
attached to a stylesheet in development -- the response comes back with only
``Last-Modified``.

With no cache directives at all, browsers apply heuristic freshness off
``Last-Modified`` and keep reusing the copy they already downloaded. That is why
a rebuilt Tailwind bundle only showed up after a hard refresh.

Rather than fight the handler, we bust the cache at the URL level. Every
``{% static %}`` in development gets the file's mtime appended as ``?v=...``, so
a plain reload requests a brand-new URL the moment ``static/css/output.css`` is
rebuilt, and unchanged assets still hit the browser cache.

In production ``CompressedManifestStaticFilesStorage`` content-hashes filenames
instead, which already gives the same guarantee without touching templates.
"""

import os
from functools import lru_cache

from django.contrib.staticfiles.finders import find
from django.contrib.staticfiles.storage import StaticFilesStorage

_ABSOLUTE_PREFIXES = ("http://", "https://", "//", "data:", "blob:")


@lru_cache(maxsize=256)
def _resolve_static_path(name):
    """Look ``name`` up on the static finders.

    The finders return the *dev source* files (``STATICFILES_DIRS`` plus each
    app's ``static/`` directory) rather than ``STATIC_ROOT``, which is what
    ``runserver`` actually serves and what we need an mtime for. Cached because
    the resolved location is stable for the lifetime of the process -- only the
    mtime is re-read on every call.
    """
    try:
        return find(name)
    except Exception:
        # A misconfigured finder must not take the page down.
        return None


class DevCacheBustingStaticFilesStorage(StaticFilesStorage):
    """``StaticFilesStorage`` that versions development URLs by file mtime."""

    def url(self, name):
        url = super().url(name)

        if not name or url.startswith(_ABSOLUTE_PREFIXES):
            return url

        path = _resolve_static_path(name)
        if not path:
            return url

        try:
            version = int(os.path.getmtime(path))
        except OSError:
            return url

        if not version:
            return url

        separator = "&" if "?" in url else "?"
        return f"{url}{separator}v={version}"