# ===========================================================
# employee/permissions.py
# ===========================================================
from rest_framework import permissions

class IsAdminUserRole(permissions.BasePermission):
    """
    Allows access only to Admin users or Django superusers.
    Checks the custom 'role' field on User model.
    """

    def has_permission(self, request, view):
        user = request.user

        # Allow if not authenticated -> False
        if not user or not user.is_authenticated:
            return False

        # Superusers always allowed
        if user.is_superuser:
            return True

        # Allow only Admin role
        return getattr(user, "role", None) == "Admin"
