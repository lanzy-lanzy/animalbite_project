"""Middleware that stops the development server from serving stale HTML.

Note what this can and cannot reach: ``runserver`` answers ``/static/...``
requests through ``StaticFilesHandler``, which short-circuits before the
middleware chain runs. Stylesheets and other static assets therefore never pass
through here and are handled by ``config.storage.DevCacheBustingStaticFilesStorage``
instead. This middleware covers everything else -- rendered HTML, redirects and
error pages -- which browsers would otherwise heuristically cache off
``Last-Modified`` when no ``Cache-Control`` is present.

Production (``DEBUG=False``) keeps its own cache policy untouched.
"""

from django.conf import settings


class DevNoCacheMiddleware:
    """Mark every development response as non-cacheable."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.DEBUG and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, must-revalidate, max-age=0"
        return response