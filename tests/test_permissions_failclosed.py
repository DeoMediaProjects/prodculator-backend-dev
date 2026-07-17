"""Security regression: admin permissions must fail CLOSED.

A row that predates the `role` column (or carries a NULL/blank role) must never
be treated as a master_admin. See DEV handover §7.1. The default on AdminUser
is an empty role, which is not a key in ROLE_PERMISSIONS, so every permission
check returns False.
"""

from app.core.permissions import ROLE_PERMISSIONS, has_permission
from app.modules.admin.schemas import AdminUser

ALL_PERMISSIONS = set().union(*ROLE_PERMISSIONS.values())


def test_admin_user_defaults_to_no_role():
    """A role-less admin row hydrates to an empty role, not master_admin."""
    admin = AdminUser(id="x", email="ghost@example.com")
    assert admin.role == ""


def test_blank_role_grants_zero_permissions():
    admin = AdminUser(id="x", email="ghost@example.com")  # no role
    for perm in ALL_PERMISSIONS:
        assert has_permission(admin.role, perm) is False


def test_unknown_role_grants_zero_permissions():
    assert has_permission("not_a_real_role", "canManageAdmins") is False


def test_master_admin_still_has_full_access():
    admin = AdminUser(id="x", email="boss@example.com", role="master_admin")
    for perm in ALL_PERMISSIONS:
        assert has_permission(admin.role, perm) is True
