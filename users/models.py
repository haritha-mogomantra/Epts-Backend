# ===========================================================
# users/models.py
# ===========================================================

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError
from django.utils.crypto import get_random_string
from datetime import timedelta, datetime
import uuid
import os


# ===========================================================
# PASSWORD HISTORY MODEL
# ===========================================================
class PasswordHistory(models.Model):
    """
    Track last 5 passwords to prevent reuse.
    """
    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='password_history'
    )
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Password History"
        verbose_name_plural = "Password Histories"
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        # Avoid accessing user attributes that might not be fully saved in unusual cases.
        try:
            uname = getattr(self.user, 'username', None) or str(self.user_id)
        except Exception:
            uname = str(self.user_id)
        return f"{uname} - {self.created_at.strftime('%Y-%m-%d')}"

    @classmethod
    def add_password(cls, user, password_hash):
        """Store password hash, keep only the latest 5."""
        # Ensure the user has a PK before associating the history
        if not user.pk:
            return
        cls.objects.create(user=user, password_hash=password_hash)
        # Keep only the latest 5 entries
        old = cls.objects.filter(user=user).order_by('-created_at')[5:]
        if old:
            cls.objects.filter(id__in=[p.id for p in old]).delete()


# ===========================================================
# USER MANAGER
# ===========================================================
class UserManager(BaseUserManager):
    """Custom user manager handling secure emp_id generation."""

    def generate_emp_id(self):
        """Generate sequential employee ID (EMP0001, EMP0002...)."""
        with transaction.atomic():
            # This uses the stored emp_id string; ensure numeric parse is guarded.
            result = self.model.objects.select_for_update().aggregate(max_emp_id=Max('emp_id'))
            last_emp_id = result.get('max_emp_id')

            if last_emp_id and isinstance(last_emp_id, str) and last_emp_id.startswith("EMP"):
                try:
                    num = int(last_emp_id.replace("EMP", ""))
                    return f"EMP{num + 1:04d}"
                except (ValueError, AttributeError):
                    pass
            return "EMP0001"

    def create_user(self, username=None, password=None, **extra_fields):
        """Create a regular user with secure defaults."""
        emp_id = extra_fields.get("emp_id") or self.generate_emp_id()
        username = username or emp_id

        if not password:
            password = get_random_string(
                length=12,
                allowed_chars="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
            )

        extra_fields["emp_id"] = emp_id
        extra_fields.setdefault("is_active", True)

        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.temp_password = password
        user.save(using=self._db)


        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "user_creation.log"), "a") as f:
                f.write(f"[{datetime.now()}] Created user {username} ({emp_id})\n")
        except Exception:
            # Do not break user creation if logging fails
            pass

        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "Admin")
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("force_password_change", False)

        if not email:
            raise ValueError("Superuser must have an email.")
        if not password:
            raise ValueError("Superuser must have a password.")

        return self.create_user(email=email, password=password, **extra_fields)


# ===========================================================
# USER MODEL
# ===========================================================
class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model for EPTS (Employee Performance Tracking System).
    Aligned with frontend forms and APIs.
    """

    ROLE_CHOICES = [
        ("Admin", "Admin"),
        ("Manager", "Manager"),
        ("Employee", "Employee"),
    ]

    # ---------- CORE ----------
    emp_id = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Auto-generated employee ID (EMP0001, EMP0002, etc.)"
    )
    username = models.CharField(max_length=150, unique=True, db_index=True)
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="Employee",
        db_index=True,
        help_text="User role"
    )

    temp_password = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Stores auto-generated temporary password for testing/logging."
    )

    # ---------- CONTACT ----------
    phone = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        validators=[RegexValidator(r"^\+?\d{7,15}$", "Enter a valid phone number.")],
    )

    # ---------- ORGANIZATION ----------
    department = models.ForeignKey(
        "employee.Department",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='direct_reports',
        limit_choices_to={'role__in': ['Manager', 'Admin']},
    )

    joining_date = models.DateField(default=timezone.now)

    # ---------- EMAIL VERIFICATION ----------
    is_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=100, null=True, blank=True)
    verification_token_created = models.DateTimeField(null=True, blank=True)

    # ---------- SECURITY ----------
    failed_login_attempts = models.PositiveIntegerField(default=0)
    account_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)
    force_password_change = models.BooleanField(default=False)

    # ---------- DJANGO FLAGS ----------
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    # ---------- AUDIT ----------
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---------- MANAGER ----------
    objects = UserManager()

    USERNAME_FIELD = "emp_id"
    REQUIRED_FIELDS = ["email", "username"]

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["emp_id"]
        indexes = [
            models.Index(fields=["username", "email", "emp_id"]),
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self):
        # Keep __str__ lightweight and safe (avoid accessing FKs that may be problematic)
        try:
            full = self.get_full_name()
            return f"{full} ({self.emp_id})" if full else f"{self.username} ({self.emp_id})"
        except Exception:
            return f"{self.username or self.emp_id}"

    # ======================================================
    # BASIC METHODS
    # ======================================================
    def get_full_name(self):
        try:
            name = f"{self.first_name} {self.last_name}".strip()
            return name if name else self.username
        except Exception:
            return self.username

    def get_short_name(self):
        return self.first_name or self.username

    # ======================================================
    # VALIDATION
    # ======================================================
    def clean(self):
        """
        Model-level validation.
        Avoid deep FK traversal when PK not yet assigned.
        """
        super().clean()

        # Employees should have a department (this uses the in-memory field too)
        if self.role == "Employee" and not self.department:
            raise ValidationError({'department': 'Employees must belong to a department.'})

        # Prevent user being their own manager (use manager_id to avoid FK object resolution)
        if self.manager_id and self.id and self.manager_id == self.id:
            raise ValidationError({'manager': 'User cannot be their own manager.'})

        # Circular manager check only when PK exists (can't reliably check before PK)
        if self.pk and self.manager_id:
            visited = {self.pk}
            current = self.manager
            while current:
                # defensive: stop if no PK on manager
                if not getattr(current, 'pk', None):
                    break
                if current.pk in visited:
                    raise ValidationError({'manager': 'Circular manager relationship detected.'})
                visited.add(current.pk)
                current = current.manager

    def save(self, *args, **kwargs):
        """
        Save with safe validation: if object has a PK, run full_clean.
        If creating (no PK yet), run only light validation to avoid relationship access that requires PK.
        """
        # Light validation: we can still check certain constraints without forcing FK resolution
        # For create (no PK) call full_clean but tolerant: wrap in try/except to avoid FK lookups
        if self.pk:
            # On update, run full validation
            self.full_clean()
        else:
            # On create, run basic clean() but guard against FK problems
            try:
                # call clean() - it's guarded to avoid deep FK traversal if pk is missing
                self.clean()
            except ValidationError:
                # re-raise ValidationError so invalid data is not saved
                raise
            except Exception:
                # swallow other exceptions during create-time validation to avoid PK-related crashes;
                # let DB constraints report issues. This is defensive to avoid the PK relationship error.
                pass

        super().save(*args, **kwargs)

    # ======================================================
    # ACCOUNT STATUS & ROLE HELPERS
    # ======================================================
    @property
    def status(self):
        if self.account_locked:
            return "Locked"
        return "Active" if self.is_active else "Inactive"

    def is_admin(self):
        return self.role == "Admin" or self.is_superuser

    def is_manager(self):
        return self.role == "Manager"

    def is_employee(self):
        return self.role == "Employee"

    # ======================================================
    # ACCOUNT LOCKOUT & LOGIN ATTEMPTS
    # ======================================================
    def lock_account(self):
        self.account_locked = True
        self.is_active = False
        self.locked_at = timezone.now()
        self.save(update_fields=["account_locked", "is_active", "locked_at"])

    def unlock_account(self):
        self.account_locked = False
        self.is_active = True
        self.failed_login_attempts = 0
        self.locked_at = None
        self.save(update_fields=["account_locked", "is_active", "failed_login_attempts", "locked_at"])

    def increment_failed_attempts(self):
        # Auto-unlock if lock period expired
        if self.account_locked and self.locked_at:
            if timezone.now() >= self.locked_at + timedelta(hours=2):
                self.unlock_account()
                return
        if self.account_locked:
            return
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.lock_account()
        else:
            self.save(update_fields=["failed_login_attempts"])

    def reset_login_attempts(self):
        if self.failed_login_attempts > 0 or self.account_locked:
            self.failed_login_attempts = 0
            self.account_locked = False
            self.locked_at = None
            self.save(update_fields=["failed_login_attempts", "account_locked", "locked_at"])

    # ======================================================
    # PASSWORD MANAGEMENT
    # ======================================================
    def set_password(self, raw_password):
        from django.contrib.auth.hashers import check_password, make_password

        # If user exists in DB, check last 5 password hashes
        if self.pk:
            recent_passwords = PasswordHistory.objects.filter(user=self).order_by('-created_at')[:5]
            for old_pw in recent_passwords:
                try:
                    if check_password(raw_password, old_pw.password_hash):
                        raise ValidationError("Cannot reuse any of your last 5 passwords.")
                except Exception:
                    # ignore broken history rows rather than block password set
                    continue

        super().set_password(raw_password)

        # Store in password history only after user has a PK
        if self.pk:
            try:
                PasswordHistory.add_password(self, make_password(raw_password))
            except Exception:
                # don't fail password set if history fails
                pass

    def mark_password_changed(self):
        self.force_password_change = False
        self.save(update_fields=['force_password_change'])

    # ======================================================
    # EMAIL VERIFICATION
    # ======================================================
    def generate_verification_token(self):
        self.verification_token = str(uuid.uuid4())
        self.verification_token_created = timezone.now()
        # Save token fields without triggering deep validation
        self.save(update_fields=['verification_token', 'verification_token_created'])
        return self.verification_token

    def verify_email(self, token):
        # Validate token
        if not self.verification_token or self.verification_token != token:
            return False
        if not self.verification_token_created:
            return False
        if timezone.now() > self.verification_token_created + timedelta(hours=24):
            return False
        self.is_verified = True
        self.verification_token = None
        self.verification_token_created = None
        self.save(update_fields=['is_verified', 'verification_token', 'verification_token_created'])
        return True

