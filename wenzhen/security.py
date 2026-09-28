"""CSRF 防护与安全响应头。"""

import secrets

from flask import abort, current_app, request, session


def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def csrf_protect():
    if request.method != "POST" or not current_app.config.get("CSRF_ENABLED", True):
        return
    expected = session.get("_csrf")
    given = request.form.get("_csrf", "")
    if not expected or not secrets.compare_digest(expected, given):
        abort(400, description="页面已过期，请刷新后重试。")


def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response
