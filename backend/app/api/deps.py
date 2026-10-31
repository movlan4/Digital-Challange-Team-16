"""Shared FastAPI dependencies: engine access and role-based authorization."""
from fastapi import Depends, Request

from app.engine import TransitEngine
from app.models import Identity, Role
from app.services.auth import SESSION_COOKIE, AuthError


def get_engine(request: Request) -> TransitEngine:
    return request.app.state.engine


def current_user(request: Request, engine: TransitEngine = Depends(get_engine)) -> Identity:
    identity = engine.auth.resolve(request.cookies.get(SESSION_COOKIE))
    if identity is None:
        raise AuthError("Sign-in required")
    return identity


def require_roles(*roles: Role):
    """401 when signed out, 403 when signed in with a role that may not use the endpoint."""
    def dependency(identity: Identity = Depends(current_user)) -> Identity:
        if identity.role not in roles:
            raise AuthError("Your account does not have access to this area", 403)
        return identity
    return dependency


require_passenger = require_roles(Role.PASSENGER)
require_operator = require_roles(Role.OPERATOR, Role.ADMIN)  # the master admin can also run operations
require_admin = require_roles(Role.ADMIN)
require_signed_in = current_user
