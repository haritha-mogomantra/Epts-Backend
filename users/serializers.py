# ===========================================================
# users/serializers.py
# ===========================================================

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import get_user_model
from django.db import transaction, models
from django.utils.crypto import get_random_string   
from django.core.mail import send_mail              
from django.conf import settings                
from django.utils import timezone
import random
import string
import logging
import re
from datetime import datetime, date
from employee.models import Department, Employee 

User = get_user_model()
logger = logging.getLogger("users")


# ===========================================================
# 1. LOGIN SERIALIZER (username / emp_id / email)
# ===========================================================
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Secure login serializer supporting username, emp_id, or email.
    Avoids authenticate() to prevent FK errors.
    """

    username_field = "username"
    LOCK_DURATION_HOURS = 2
    LOCK_THRESHOLD = 5

    def validate(self, attrs):
        """
        Enhanced login validation supporting username, emp_id, or email.
        Handles lockout, failed attempts, and JWT token creation.
        """
        login_input = attrs.get("username")  # DRF-SimpleJWT always passes username key
        password = attrs.get("password")

        if not login_input or not password:
            raise serializers.ValidationError(
                {"detail": "Both username (emp_id/username/email) and password are required."}
            )

        # 🔍 Try to match user by username OR emp_id OR email
        user = User.objects.filter(
            models.Q(username__iexact=login_input)
            | models.Q(emp_id__iexact=login_input)
            | models.Q(email__iexact=login_input)
        ).first()

        if not user:
            raise serializers.ValidationError({"detail": "Invalid username or password."})

        # 🔒 Account lock validation
        if getattr(user, "account_locked", False):
            if getattr(user, "locked_at", None):
                elapsed = timezone.now() - user.locked_at
                remaining = max(0, self.LOCK_DURATION_HOURS * 3600 - elapsed.total_seconds())
                if remaining > 0:
                    hrs, mins = divmod(int(remaining // 60), 60)
                    raise serializers.ValidationError(
                        {"detail": f"Account locked. Try again after {hrs}h {mins}m."}
                    )
                else:
                    if hasattr(user, "unlock_account"):
                        user.unlock_account()

        # 🔐 Password validation
        if not user.check_password(password):
            # Increment failed attempt counter
            user.failed_login_attempts += 1
            remaining = self.LOCK_THRESHOLD - user.failed_login_attempts

            # Lock account if threshold reached
            if user.failed_login_attempts >= self.LOCK_THRESHOLD:
                user.lock_account()
                raise serializers.ValidationError(
                    {"detail": f"Account locked. Try again after {self.LOCK_DURATION_HOURS} hours."}
                )

            user.save(update_fields=["failed_login_attempts"])

            # 🧠 Show alert only for last 3 attempts (3, 2, 1 remaining)
            if remaining <= 3:
                raise serializers.ValidationError(
                    {"detail": f"Invalid credentials. {remaining} attempt(s) left."}
                )
            else:
                # For first two failed attempts, show simple invalid message
                raise serializers.ValidationError({"detail": "Invalid credentials."})


        # ✅ Success: Reset failed attempts and unlock if needed
        if hasattr(user, "reset_login_attempts"):
            user.reset_login_attempts()
        else:
            user.failed_login_attempts = 0
            user.account_locked = False
            user.locked_at = None
            user.save(update_fields=["failed_login_attempts", "account_locked", "locked_at"])

        #if getattr(user, "force_password_change", False):
            #raise serializers.ValidationError({
                #"force_password_change": True,
                #"detail": "Password change required before login."
            #})

        # 🕒 Update last login
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])

        # 🪪 Generate JWT tokens
        refresh = self.get_token(user)
        access = refresh.access_token

        data = {
            "refresh": str(refresh),
            "access": str(access),
            "user": {
                "id": user.id,
                "emp_id": user.emp_id,
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "role": user.role,
                "department": user.department.name if user.department else None,
                "manager": user.manager.username if user.manager else None,
                "status": user.status,
                "is_verified": getattr(user, "is_verified", False),
                "is_active": user.is_active,
            },
        }
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["emp_id"] = user.emp_id
        token["role"] = user.role
        return token


# ===========================================================
# 2. REGISTER SERIALIZER (Signal-Free, Employee Sync)
# ===========================================================
class RegisterSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField(read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    temp_password = serializers.CharField(read_only=True)

    # Accept flexible department fields
    department = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    department_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    department_name_input = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    manager = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    joining_date = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id",
            "emp_id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "department",
            "department_code",
            "department_name_input",
            "department_name",
            "manager",
            "phone",
            "role",
            "status",
            "joining_date",
            "temp_password",
        ]
        read_only_fields = ["id", "emp_id", "temp_password"]

    # ---------------- Computed Fields ----------------
    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.last_name or ''}".strip()

    # ---------------- Field Validations ----------------
    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already exists.")
        return value

    def validate_phone(self, value):
        if value and User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Phone number already exists.")
        return value

    def validate_joining_date(self, value):
        """Must be valid date in past or today."""
        if not value:
            return None
        try:
            jd = value if isinstance(value, date) else datetime.strptime(value, "%Y-%m-%d").date()
        except Exception:
            raise serializers.ValidationError("joining_date must be in YYYY-MM-DD format.")
        if jd > timezone.now().date():
            raise serializers.ValidationError("joining_date cannot be in the future.")
        return jd

    def validate(self, attrs):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            role = getattr(request.user, "role", "")
            if not (request.user.is_superuser or role in ["Admin", "Manager"]):
                raise serializers.ValidationError({"permission": "Only Admin or Manager can create users."})
        return attrs

    # ---------------- CREATE USER ----------------
    @transaction.atomic
    def create(self, validated_data):
        dept_value = (
            validated_data.pop("department", None)
            or validated_data.pop("department_code", None)
            or validated_data.pop("department_name_input", None)
        )
        manager_value = validated_data.pop("manager", None)
        joining_date_value = validated_data.pop("joining_date", None)

        joining_date = None
        if joining_date_value:
            joining_date = self.validate_joining_date(joining_date_value)

        # Resolve Department
        if not dept_value:
            raise serializers.ValidationError({"department": "Department is required."})
        dept_value = str(dept_value).strip()
        department_instance = Department.objects.filter(
            models.Q(code__iexact=dept_value)
            | models.Q(name__iexact=dept_value)
            | models.Q(id__iexact=dept_value)
        ).first()
        if not department_instance:
            raise serializers.ValidationError({"department": f"Department '{dept_value}' not found."})
        if not getattr(department_instance, "is_active", True):
            raise serializers.ValidationError({"department": f"Department '{department_instance.name}' is inactive."})

        # Resolve Manager (active, not deleted, valid role)
        manager_employee = None
        if manager_value:
            manager_value = str(manager_value).strip()
            manager_user = (
                User.objects.filter(emp_id__iexact=manager_value).first()
                or User.objects.filter(username__iexact=manager_value).first()
            )
            if not manager_user:
                raise serializers.ValidationError({"manager": f"Manager '{manager_value}' not found as User."})

            manager_employee = Employee.objects.filter(user=manager_user).first()
            if manager_employee and getattr(manager_employee, "is_deleted", False):
                raise serializers.ValidationError({
                    "manager": f"Manager '{manager_user.username}' exists but is deleted. Cannot assign deleted manager."
                })
            if not manager_employee:
                if manager_user.role not in ["Manager", "Admin"]:
                    raise serializers.ValidationError({
                        "manager": f"Manager '{manager_user.username}' must have role 'Manager' or 'Admin'."
                    })
                if not manager_user.is_active:
                    raise serializers.ValidationError({
                        "manager": f"Manager '{manager_user.username}' is inactive and cannot be assigned."
                    })
                manager_employee = Employee.objects.create(
                    user=manager_user,
                    department=manager_user.department,
                    manager=None,
                    role=manager_user.role,
                    status="Active",
                    joining_date=getattr(manager_user, "joining_date", timezone.now().date()),
                )

        # Generate Emp ID
        last_user = User.objects.select_for_update().order_by("-id").first()
        last_num = int(last_user.emp_id.replace("EMP", "")) if last_user and last_user.emp_id else 0
        new_emp_id = f"EMP{last_num + 1:04d}"

        # Temporary Password
        first_name = validated_data.get("first_name", "User").capitalize()
        random_part = "".join(random.choices(string.ascii_letters + string.digits, k=4))
        temp_password = f"{first_name}@{random_part}"

        # Create User
        user = User.objects.create_user(
            emp_id=new_emp_id,
            password=temp_password,
            department=department_instance,
            manager=manager_user if manager_value else None,
            **validated_data,
        )
        user.force_password_change = True
        if joining_date:
            user.joining_date = joining_date
        user.save()

        # Create Employee
        emp_kwargs = {
            "user": user,
            "department": department_instance,
            "manager": manager_employee,
            "status": "Active",
            "joining_date": joining_date or timezone.now().date(),
        }
        Employee.objects.create(**emp_kwargs)

        user.temp_password = temp_password
        logger.info(f"User {user.emp_id} created successfully with temp password.")
        return user

    # ---------------- RESPONSE FORMAT ----------------
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["temp_password"] = getattr(instance, "temp_password", None)
        if instance.department:
            rep["department"] = instance.department.name
            rep["department_code"] = getattr(instance.department, "code", None)
        if instance.manager:
            rep["manager"] = instance.manager.username
        return rep


# ===========================================================
# 3. CHANGE PASSWORD SERIALIZER (Enhanced)
# ===========================================================
class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters long.")
        if not re.search(r"[A-Z]", value):
            raise serializers.ValidationError("Include at least one uppercase letter.")
        if not re.search(r"[a-z]", value):
            raise serializers.ValidationError("Include at least one lowercase letter.")
        if not re.search(r"\d", value):
            raise serializers.ValidationError("Include at least one digit.")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", value):
            raise serializers.ValidationError("Include at least one special character.")
        return value

    def validate(self, attrs):
        if attrs.get("new_password") != attrs.get("confirm_password"):
            raise serializers.ValidationError({"confirm_password": "New and confirm password must match."})
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.force_password_change = False
        user.save(update_fields=["password", "force_password_change"])
        logger.info(f"Password changed successfully for {user.emp_id}")
        return {"message": "Password changed successfully!"}


# ===========================================================
# 4. PROFILE SERIALIZER
# ===========================================================
class ProfileSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    full_name = serializers.SerializerMethodField(read_only=True)
    joining_date = serializers.SerializerMethodField(read_only=True)
    manager = serializers.CharField(source="manager.username", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "emp_id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "department",
            "department_name",
            "manager",
            "phone",
            "status",
            "joining_date",
            "is_verified",
            "is_active",
        ]
        read_only_fields = [
            "id",
            "emp_id",
            "username",
            "email",
            "full_name",
            "department_name",
            "manager",
            "joining_date",
            "is_verified",
            "is_active",
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.last_name or ''}".strip()

    def get_joining_date(self, obj):
        jd = getattr(obj, "joining_date", None)
        return jd.isoformat() if hasattr(jd, "isoformat") else jd

    def update(self, instance, validated_data):
        for field in ["first_name", "last_name", "department", "phone", "status", "role"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance



class RegeneratePasswordSerializer(serializers.Serializer):
    """
    Admin-only serializer to generate a temporary password for a user.
    Returns temp password in response only if settings.DEBUG is True.
    """
    emp_id = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate(self, attrs):
        emp_id = (attrs.get("emp_id") or "").strip()
        email = (attrs.get("email") or "").strip().lower()

        if not emp_id and not email:
            raise serializers.ValidationError("Either 'emp_id' or 'email' is required.")

        user = None
        if emp_id:
            user = User.objects.filter(emp_id__iexact=emp_id).first()
        if not user and email:
            user = User.objects.filter(email__iexact=email).first()

        if not user:
            raise serializers.ValidationError("User not found with provided credentials.")

        if not user.is_active:
            raise serializers.ValidationError("Cannot reset password for inactive user.")

        attrs["user"] = user
        return attrs

    @transaction.atomic
    def save(self, **kwargs):
        user = self.validated_data["user"]

        # generate a secure temp password
        new_password = get_random_string(
            length=10,
            allowed_chars="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        )

        # set password and force password change
        user.set_password(new_password)
        user.temp_password = new_password
        user.force_password_change = True
        user.save(update_fields=["password", "temp_password", "force_password_change"])

        # Attempt to send email; if fails (or not configured), fallback to logging/console
        mail_sent = False
        try:
            send_mail(
                subject="EPTS Password Regenerated",
                message=(
                    f"Hello {user.get_full_name()},\n\n"
                    f"Your password has been regenerated by an Admin.\n"
                    f"Temporary Password: {new_password}\n\n"
                    f"Please log in and change your password immediately.\n\n"
                    f"Regards,\nEPTS Admin Team"
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com"),
                recipient_list=[user.email],
                fail_silently=False,
            )
            mail_sent = True
        except Exception as e:
            # log and fallback to console output so dev can copy the password
            logger.warning(f"Failed to send regenerated password email to {user.emp_id}: {e}")
            # Print to console when email backend not set / for local dev
            try:
                print(f"[EPTS] Regenerated password for {user.emp_id}: {new_password}")
            except Exception:
                # if printing fails, still continue
                pass

        logger.info(f"Temporary password regenerated for user {user.emp_id} by Admin.")

        return {
            "emp_id": user.emp_id,
            "email": user.email,
            "message": f"Temporary password regenerated successfully for {user.emp_id}.",
            # Only include actual temp password in response when debugging locally
            "temp_password": new_password if settings.DEBUG else "Hidden (Production)",
            "force_password_change": True,
            "email_sent": mail_sent,
        }
    

# ===========================================================
# 5. ADMIN LOGIN DETAILS SERIALIZER (UPDATED)
# ===========================================================
class LoginDetailsSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    department = serializers.CharField(source="department.name", read_only=True)
    last_login = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", allow_null=True)
    date_joined = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S")
    temp_password = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "emp_id",
            "full_name",
            "username",
            "email",
            "role",
            "department",
            "status",
            "is_active",
            "last_login",
            "date_joined",
            "temp_password",
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.last_name or ''}".strip()

    def get_temp_password(self, obj):
        """
        Only return temp password for admins or when DEBUG=True.
        Otherwise, hide it for security.
        """
        request = self.context.get("request")
        if settings.DEBUG:
            return obj.temp_password
        if request and hasattr(request, "user"):
            if request.user.is_staff or getattr(request.user, "role", "") in ["Admin", "Manager"]:
                return obj.temp_password
        return None
