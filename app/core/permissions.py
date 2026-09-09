from typing import List, Callable, Any
from app.core.exceptions import ForbiddenException


class PermissionChecker:
    """Dependency callable to check if user has specific required permissions."""

    def __init__(self, required_permissions: List[str]):
        self.required_permissions = required_permissions

    def __call__(self, user_permissions: List[str]) -> bool:
        for perm in self.required_permissions:
            if perm not in user_permissions:
                raise ForbiddenException(f"Missing required permission: {perm}")
        return True


def has_permission(*permissions: str) -> PermissionChecker:
    return PermissionChecker(list(permissions))
