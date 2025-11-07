# ===========================================================
# employee/models.py
# ===========================================================
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
import os

# Use the AUTH_USER_MODEL string to avoid import-time circular issues
User = settings.AUTH_USER_MODEL


# ===========================================================
# Department Model
# ===========================================================
class Department(models.Model):
    """Represents organizational departments."""

    code = models.CharField(
        max_length=10,
        unique=True,
        help_text="Short department code (e.g., ENG01)"
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Department name (e.g., Engineering)"
    )
    description = models.TextField(blank=True, null=True, help_text="Optional department description.")
    employee_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Department"
        verbose_name_plural = "Departments"
        indexes = [models.Index(fields=["code"]), models.Index(fields=["name"])]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def clean(self):
        """Basic validation for department code."""
        if not self.code.isalnum():
            raise ValidationError({"code": "Department code must be alphanumeric."})

    def update_employee_count(self):
        """Recalculate and update employee count (only active, not deleted)."""
        self.employee_count = self.employees.filter(status="Active", is_deleted=False).count()
        # update both employee_count and updated_at for explicitness
        self.save(update_fields=["employee_count", "updated_at"])


# ===========================================================
# Employee Model
# ===========================================================
class Employee(models.Model):
    """Represents employee records linked to the User model."""

    STATUS_CHOICES = [
        ("Active", "Active"),
        ("Inactive", "Inactive"),
        ("On Leave", "On Leave"),
    ]

    ROLE_CHOICES = [
        ("Admin", "Admin"),
        ("Manager", "Manager"),
        ("Employee", "Employee"),
    ]

    # -----------------------------------------------------------
    # Core Relationships
    # -----------------------------------------------------------
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        help_text="Linked Django User account."
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
        help_text="Department this employee belongs to."
    )
    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members",
        help_text="Reporting manager for this employee."
    )

    # -----------------------------------------------------------
    # Professional Fields
    # -----------------------------------------------------------
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="Employee")
    designation = models.CharField(max_length=100, blank=True, null=True)
    project_name = models.CharField(max_length=150, blank=True, null=True, help_text="Project the employee is working on")
    reporting_manager_name = models.CharField(max_length=150, blank=True, null=True, help_text="Reporting manager's name")
    joining_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Active")

    # -----------------------------------------------------------
    # Personal Information
    # -----------------------------------------------------------
    contact_number = models.CharField(max_length=15, blank=True, null=True)
    gender = models.CharField(max_length=10, blank=True, null=True)
    dob = models.DateField(blank=True, null=True, help_text="Date of birth (DD-MM-YYYY)")
    profile_picture = models.ImageField(
        upload_to="profile_pics/%Y/%m/%d",
        blank=True,
        null=True,
        help_text="Profile picture (JPG/PNG)"
    )

    # -----------------------------------------------------------
    # Address Information
    # -----------------------------------------------------------
    address_line1 = models.CharField(max_length=255, blank=True, null=True)
    address_line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=12, blank=True, null=True)

    # -----------------------------------------------------------
    # System Flags
    # -----------------------------------------------------------
    location = models.CharField(max_length=100, blank=True, null=True)
    is_deleted = models.BooleanField(default=False, help_text="Soft delete flag for employee.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # -----------------------------------------------------------
    # Meta Configuration
    # -----------------------------------------------------------
    class Meta:
        ordering = ["user__emp_id"]
        verbose_name = "Employee"
        verbose_name_plural = "Employees"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["department"]),
            models.Index(fields=["role"]),
            models.Index(fields=["is_deleted"]),
        ]

    # -----------------------------------------------------------
    # Utility Properties
    # -----------------------------------------------------------
    @property
    def emp_id(self):
        """Return the emp_id stored on the related User (if available)."""
        return getattr(self.user, "emp_id", None)

    def __str__(self):
        full_name = f"{self.user.first_name} {self.user.last_name}".strip()
        return f"{self.emp_id or '-'} - {full_name or self.user.username}"

    def get_full_name(self):
        """Return user's full name or username fallback."""
        return f"{self.user.first_name} {self.user.last_name}".strip() or self.user.username

    def get_department_name(self):
        return self.department.name if self.department else "-"

    def get_role_display_name(self):
        return dict(self.ROLE_CHOICES).get(self.role, "Employee")

    @property
    def manager_name(self):
        """
        Convenient property returning manager display name or the reporting_manager_name fallback.
        Useful in serializers/templates where manager may be null.
        """
        if self.manager and hasattr(self.manager, "user"):
            return f"{self.manager.user.first_name} {self.manager.user.last_name}".strip()
        return self.reporting_manager_name or "Not Assigned"

    @property
    def team_size(self):
        """Return number of direct reports for this employee (fast DB query)."""
        return Employee.objects.filter(manager=self, is_deleted=False).count()

    # -----------------------------------------------------------
    # Validation
    # -----------------------------------------------------------
    def clean(self):
        """
        Model-level validation. Serializer also validates, but we keep strict checks
        here as an additional safety-net for any direct model operations.
        """
        # A serializer may set this attribute when it already validated data
        # Prevent double validation if flagged.
        if hasattr(self, "_validated_from_serializer"):
            return

        if self.is_deleted:
            raise ValidationError({"employee": "❌ This employee record has been deleted. No modifications allowed."})

        if not self.user or not getattr(self.user, "email", None):
            raise ValidationError({"user": "Linked User must have a valid email."})

        # Email uniqueness across employees (excluding current record)
        if Employee.objects.exclude(id=self.id).filter(user__email=self.user.email).exists():
            raise ValidationError({"user": "An employee with this email already exists."})

        # Manager cannot be self
        if self.manager and self.manager == self:
            raise ValidationError({"manager": "An employee cannot be their own manager."})

        # Manager must be Manager/Admin
        if self.manager:
            manager_role = getattr(self.manager, "role", None)
            if manager_role not in ["Manager", "Admin"]:
                raise ValidationError({"manager": "Assigned manager must have role 'Manager' or 'Admin'."})

        # Department required for an employee
        if not self.department:
            raise ValidationError({"department": "Employee must belong to a department."})

        # Date checks
        if self.joining_date and self.joining_date > timezone.now().date():
            raise ValidationError({"joining_date": "Joining date cannot be in the future."})

        if self.dob and self.dob > timezone.now().date():
            raise ValidationError({"dob": "Date of birth cannot be in the future."})

        # Pincode numeric
        if self.pincode and not self.pincode.isdigit():
            raise ValidationError({"pincode": "Pincode must contain only digits."})

        # Profile picture extension check
        if self.profile_picture:
            ext = os.path.splitext(self.profile_picture.name)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png"]:
                raise ValidationError({"profile_picture": "Only JPG and PNG images are allowed."})

    # -----------------------------------------------------------
    # Save Override
    # -----------------------------------------------------------
    def save(self, *args, **kwargs):
        """
        Ensure validations and department-count consistency while saving.
        The serializer sets _validated_from_serializer to bypass redundant full_clean.
        """
        # Allow soft-delete updates to bypass validation safely
        if kwargs.get("update_fields") == ["is_deleted", "status"]:
            super().save(*args, **kwargs)
            return

        if self.is_deleted:
            raise ValidationError({"employee": "❌ Cannot modify a deleted employee."})

        if not hasattr(self, "_validated_from_serializer"):
            # full_clean will call clean() and field validators
            self.full_clean()

        is_new = self.pk is None
        super().save(*args, **kwargs)

        # Update department count for the current department
        if self.department:
            self.department.update_employee_count()

        # If department changed, update the old department count too
        if not is_new and hasattr(self, "_old_department_id"):
            old_dept = Department.objects.filter(id=self._old_department_id).first()
            if old_dept and old_dept != self.department:
                old_dept.update_employee_count()

    def __init__(self, *args, **kwargs):
        """
        Track the old department id to adjust counts on department move.
        """
        super().__init__(*args, **kwargs)
        self._old_department_id = self.department_id

    # -----------------------------------------------------------
    # Soft Delete Logic
    # -----------------------------------------------------------
    def soft_delete(self):
        """Soft delete the employee and deactivate the user account."""
        if self.is_deleted:
            raise ValidationError({"employee": "This employee is already deleted."})

        self.is_deleted = True
        self.status = "Inactive"

        if self.user:
            # Deactivate related user
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])

        # Save only the changed fields
        super().save(update_fields=["is_deleted", "status"])

        if self.department:
            self.department.update_employee_count()
