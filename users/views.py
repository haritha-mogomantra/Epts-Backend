# ===========================================================
# users/views.py
# ===========================================================

from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework import generics, status, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction, models
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
import re

from employee.models import Employee, Department
from .serializers import (
    CustomTokenObtainPairSerializer,
    RegisterSerializer,
    ProfileSerializer,
    ChangePasswordSerializer,
    RegeneratePasswordSerializer,
    LoginDetailsSerializer
)

User = get_user_model()


# ===========================================================
# HELPER PERMISSION FUNCTIONS
# ===========================================================
def is_admin(user):
    return user.is_superuser or getattr(user, "role", "") == "Admin"

def is_manager(user):
    return getattr(user, "role", "") == "Manager"

def is_admin_or_manager(user):
    return user.is_superuser or getattr(user, "role", "") in ["Admin", "Manager"]


# ===========================================================
# 1. LOGIN
# ===========================================================
class ObtainTokenPairView(TokenObtainPairView):
    """POST /api/users/login/ — Login via emp_id, username, or email."""
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)

        except Exception as e:
            # 🧠 Extract and format specific error messages
            error_text = str(e)

            # Normalize text for easier matching
            error_lower = error_text.lower()

            # 🧩 Logic for clean, context-aware messages
            if "account locked" in error_lower:
                message = "Account locked. Try again after 2 hours."
            elif "too many failed attempts" in error_lower:
                message = "Account locked. Try again after 2 hours."
            elif "attempt(s) left" in error_lower:
                # Cleanly extract the remaining-attempt message from serializer
                message = (
                    error_text.replace("{'detail': [ErrorDetail(string='", "")
                    .replace("', code='invalid')]}", "")
                    .replace("{'detail': ", "")
                    .replace("}", "")
                    .replace("[", "")
                    .replace("]", "")
                    .strip()
                )

            elif "invalid credentials" in error_lower:
                message = "Invalid credentials."
            else:
                message = "Invalid credentials."

            return Response(
                {
                    "success": False,
                    "message": message,
                    "error": error_text,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ Success block
        data = serializer.validated_data
        user_data = data.get("user", {})

        return Response(
            {
                "success": True,
                "token": data.get("access"),       # access token
                "refresh": data.get("refresh"),    # refresh token
                "role": user_data.get("role"),
                "username": user_data.get("username"),
                "emp_id": user_data.get("emp_id"),
                "message": "Login successful.",
            },
            status=status.HTTP_200_OK,
        )


# ===========================================================
# 2. REFRESH TOKEN
# ===========================================================
class RefreshTokenView(TokenRefreshView):
    permission_classes = [AllowAny]


# ===========================================================
# 3. REGISTER USER (Admin / Manager)
# ===========================================================
class RegisterView(generics.CreateAPIView):
    """POST /api/users/register/"""
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        current_user = request.user

        if not is_admin_or_manager(current_user):
            return Response({"error": "Access denied. Admin or Manager only."}, status=403)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data.get("email")

        if User.objects.filter(email__iexact=email).exists():
            return Response({"error": "Email already exists."}, status=400)

        # Create User (serializer handles this safely)
        user = serializer.save()

        # Auto-fill joining_date if missing
        if not user.joining_date:
            user.joining_date = timezone.now().date()
            user.save(update_fields=["joining_date"])

        # -----------------------------------------------------------
        # Employee Auto-Sync (Safe get_or_create)
        # -----------------------------------------------------------
        emp_defaults = {
            "department": user.department,
            "role": getattr(user, "role", "Employee"),
            "status": "Active" if user.is_active else "Inactive",
            "joining_date": user.joining_date or timezone.now().date(),
        }

        # Resolve manager (Employee instance)
        if getattr(user, "manager", None):
            mgr_emp = Employee.objects.filter(user=user.manager, is_deleted=False).first()
            if mgr_emp:
                emp_defaults["manager"] = mgr_emp

        employee_obj, created = Employee.objects.get_or_create(
            user=user,
            defaults=emp_defaults
        )

        # If previously soft-deleted → restore
        if not created and getattr(employee_obj, "is_deleted", False):
            employee_obj.is_deleted = False
            employee_obj.status = emp_defaults["status"]
            employee_obj.department = emp_defaults["department"]
            employee_obj.role = emp_defaults["role"]
            employee_obj.manager = emp_defaults.get("manager")
            employee_obj.joining_date = emp_defaults["joining_date"]
            employee_obj.save(update_fields=["is_deleted", "status", "department", "role", "manager", "joining_date"])

        # -----------------------------------------------------------
        # Send Email Notification (optional)
        # -----------------------------------------------------------
        try:
            send_mail(
                subject="EPTS Account Created",
                message=(
                    f"Hello {user.get_full_name()},\n\n"
                    f"Your EPTS account has been created.\n"
                    f"Employee ID: {user.emp_id}\n"
                    f"Temporary Password: {getattr(user, 'temp_password', 'N/A')}\n\n"
                    f"Please log in and change your password.\n\n"
                    f"Regards,\nEPTS Admin Team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Email send failed for {user.emp_id}: {e}")

        logger.info(f"User {user.emp_id} registered by {current_user.emp_id}")

        return Response(
            {
                "message": "User registered successfully.",
                "user": ProfileSerializer(user).data,
                "temp_password": getattr(user, "temp_password", None) if settings.DEBUG else None,
            },
            status=201,
        )


# ===========================================================
# 4. CHANGE PASSWORD
# ===========================================================
class ChangePasswordView(APIView):
    """
    POST /api/users/change-password/
    Allows authenticated users to securely change their password.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        # 1️⃣ Validate required fields
        if not all([old_password, new_password, confirm_password]):
            return Response(
                {"message": "All fields (old_password, new_password, confirm_password) are required.", "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2️⃣ Validate old password
        if not user.check_password(old_password):
            return Response(
                {"message": "Old password is incorrect.", "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3️⃣ Prevent using the same password
        if old_password == new_password:
            return Response(
                {"message": "New password cannot be the same as the old password.", "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 4️⃣ Match confirmation
        if new_password != confirm_password:
            return Response(
                {"message": "New password and confirm password do not match.", "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 5️⃣ Enforce password complexity
        pattern = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&])[A-Za-z\d@$!%*#?&]{8,}$"
        if not re.match(pattern, new_password):
            return Response(
                {"message": "Password must be at least 8 characters long and include uppercase, lowercase, number, and special character.", "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 6️⃣ Attempt password change — catch password-reuse validation
        try:
            user.set_password(new_password)  # uses PasswordHistory logic in your model
            user.force_password_change = False
            user.save(update_fields=["password", "force_password_change"])
        except ValidationError as ve:
            return Response(
                {"message": str(ve.message), "status": "error"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"message": "Unexpected error while changing password.", "status": "error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


        return Response(
            {"message": "Password changed successfully!", "status": "success"},
            status=status.HTTP_200_OK
        )
# ===========================================================
# 5. PROFILE (GET / PATCH)
# ===========================================================
class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ProfileSerializer(request.user).data, status=200)

    def patch(self, request):
        user = request.user
        editable = {"first_name", "last_name", "email", "phone"}
        updates = {f: v for f, v in request.data.items() if f in editable}

        if not updates:
            return Response({"message": "No valid fields to update."}, status=400)

        if "email" in updates:
            try:
                validate_email(updates["email"])
            except ValidationError:
                return Response({"error": "Invalid email format."}, status=400)
            if User.objects.exclude(id=user.id).filter(email__iexact=updates["email"]).exists():
                return Response({"error": "Email already exists."}, status=400)

        if "phone" in updates and User.objects.exclude(id=user.id).filter(phone=updates["phone"]).exists():
            return Response({"error": "Phone already exists."}, status=400)

        for field, value in updates.items():
            setattr(user, field, value)

        user.save(update_fields=list(updates.keys()))

        return Response(
            {"message": "Profile updated successfully.", "user": ProfileSerializer(user).data},
            status=200,
        )


# ===========================================================
# 6. ROLE LIST
# ===========================================================
class RoleListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"roles": [r for r, _ in User.ROLE_CHOICES]}, status=200)


# ===========================================================
# 7. USER LIST (Admin Only)
# ===========================================================
class UserPagination(PageNumberPagination):
    page_size = 20


class UserListView(generics.ListAPIView):
    queryset = User.objects.select_related("department", "manager").order_by("emp_id")
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "emp_id", "email", "first_name", "last_name", "status"]
    ordering_fields = ["emp_id", "username", "joining_date"]
    pagination_class = UserPagination

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if "status" in params:
            qs = qs.filter(is_active=(params["status"].lower() == "active"))
        if "department" in params:
            qs = qs.filter(department__name__icontains=params["department"])
        if "emp_id" in params:
            qs = qs.filter(emp_id__iexact=params["emp_id"])
        return qs

    def list(self, request, *args, **kwargs):
        if not is_admin(request.user):
            return Response({"error": "Access denied. Admins only."}, status=403)
        return super().list(request, *args, **kwargs)


# ===========================================================
# 8. ADMIN RESET PASSWORD
# ===========================================================
@api_view(["POST"])
@permission_classes([IsAdminUser])
@transaction.atomic
def reset_password(request):
    emp_id = request.data.get("emp_id")
    if not emp_id:
        return Response({"error": "emp_id is required."}, status=400)

    user = User.objects.filter(emp_id=emp_id).first()
    if not user:
        return Response({"error": "User not found."}, status=404)

    new_password = get_random_string(10, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*")
    user.set_password(new_password)
    user.force_password_change = True
    user.save(update_fields=["password", "force_password_change"])

    try:
        send_mail(
            subject="EPTS Password Reset",
            message=(
                f"Hello {user.get_full_name()},\n\n"
                f"Your password has been reset by Admin ({request.user.emp_id}).\n"
                f"Temporary Password: {new_password}\n\n"
                f"Please log in and change your password.\n\n"
                f"Regards,\nEPTS Admin Team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        pass

    data = {"message": f"Password reset successfully for {user.emp_id}.", "force_password_change": True}
    if settings.DEBUG:
        data["temp_password"] = new_password
    return Response(data, status=200)


# ===========================================================
# 9. USER DETAIL (Admin CRUD)
# ===========================================================
class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, emp_id):
        return User.objects.filter(emp_id=emp_id).first()

    def get(self, request, emp_id):
        user = self.get_object(emp_id)
        if not user:
            return Response({"error": "User not found."}, status=404)
        return Response(ProfileSerializer(user).data, status=200)

    @transaction.atomic
    def patch(self, request, emp_id):
        admin = request.user
        if not is_admin(admin):
            return Response({"error": "Access denied. Admins only."}, status=403)

        user = self.get_object(emp_id)
        if not user:
            return Response({"error": "User not found."}, status=404)

        editable_fields = ["first_name", "last_name", "email", "phone", "is_verified", "is_active", "department", "manager"]
        updates = {f: v for f, v in request.data.items() if f in editable_fields}

        # Manager handling
        if "manager" in updates:
            mgr_val = updates.pop("manager")
            manager = User.objects.filter(emp_id__iexact=mgr_val).first() or User.objects.filter(username__iexact=mgr_val).first()
            if not manager:
                return Response({"error": f"Manager '{mgr_val}' not found."}, status=400)
            updates["manager"] = manager

        for f, v in updates.items():
            setattr(user, f, v)
        user.save(update_fields=list(updates.keys()))

        # Reflect updates in Employee table
        Employee.objects.filter(user=user).update(
            department=user.department,
            manager=user.manager,
            status="Active" if user.is_active else "Inactive"
        )

        return Response({"message": "User updated successfully.", "user": ProfileSerializer(user).data}, status=200)

    @transaction.atomic
    def delete(self, request, emp_id):
        admin = request.user
        if not is_admin(admin):
            return Response({"error": "Access denied. Admins only."}, status=403)

        user = self.get_object(emp_id)
        if not user:
            return Response({"error": "User not found."}, status=404)
        if user == admin:
            return Response({"error": "You cannot deactivate your own account."}, status=400)
        if user.role == "Admin":
            return Response({"error": "Cannot deactivate another Admin account."}, status=400)

        user.is_active = False
        user.save(update_fields=["is_active"])
        Employee.objects.filter(user=user).update(status="Inactive")

        return Response({"message": f"User '{emp_id}' deactivated successfully."}, status=200)



# ===========================================================
# 10. ADMIN — REGENERATE PASSWORD (Console or Email)
# ===========================================================
@api_view(["POST"])
@permission_classes([AllowAny])  
@transaction.atomic
def regenerate_password(request, emp_id=None):
    """
    POST /api/users/regenerate-password/<emp_id>/
    or
    POST /api/users/regenerate-password/

    Allows Admin to generate a new temporary password for an employee.

    Request Body (optional if emp_id in URL):
    {
        "emp_id": "EMP0002"   # or
        "email": "hr.manager@example.com"
    }
    """

    # ✅ Allow both URL and request body parameters
    emp_id = emp_id or request.data.get("emp_id")
    email = request.data.get("email")

    # Validation
    if not emp_id and not email:
        return Response({"error": "Either 'emp_id' or 'email' is required."}, status=400)

    # Lookup user
    user = None
    if emp_id:
        user = User.objects.filter(emp_id__iexact=emp_id).first()
    elif email:
        user = User.objects.filter(email__iexact=email).first()

    if not user:
        return Response({"error": "User not found."}, status=404)

    if not user.is_active:
        return Response({"error": "Cannot regenerate password for inactive user."}, status=400)

    # Generate a new secure temporary password
    first_name = user.first_name or "User"
    random_part = get_random_string(length=4, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    new_password = f"{first_name}@{random_part}"


    user.set_password(new_password)
    user.temp_password = new_password
    user.force_password_change = True
    user.save(update_fields=["password", "temp_password", "force_password_change"])

    # Log or send via email
    if hasattr(settings, "EMAIL_BACKEND") and "console" in settings.EMAIL_BACKEND:
        print("\n" + "=" * 50)
        print(f"TEMP PASSWORD GENERATED FOR: {user.emp_id}")
        print(f"User: {user.get_full_name()} ({user.email})")
        print(f"Temporary Password: {new_password}")
        print(f"Generated by Admin: {getattr(request.user, 'emp_id', 'SYSTEM')}")
        print("=" * 50 + "\n")
    else:
        try:
            send_mail(
                subject="EPTS Temporary Password Regeneration",
                message=(
                    f"Hello {user.get_full_name()},\n\n"
                    f"Your temporary password has been regenerated by Admin ({request.user.emp_id}).\n"
                    f"Temporary Password: {new_password}\n\n"
                    f"Please log in and change it immediately.\n\n"
                    f"Regards,\nEPTS Admin Team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass

    # IMPORTANT: keep production-safe visibility — only show raw password in DEBUG
    visible_password = new_password if settings.DEBUG else "Hidden (Production)"

    response_data = {
        "emp_id": user.emp_id,
        "email": user.email,
        "message": f"Temporary password regenerated successfully for {user.emp_id}.",
        "temp_password": new_password if settings.DEBUG else "Hidden (Production)",
        # <-- Added for frontend compatibility (so your React's emp.password will work)
        "password": visible_password,
        "force_password_change": True,
    }

    return Response(response_data, status=200)



# ===========================================================
# 11. ADMIN — LOGIN DETAILS LIST
# ===========================================================
class AdminUserListView(generics.ListAPIView):
    """
    GET /api/users/login-details/
    Lists all users with login metadata for admin view.
    """
    queryset = User.objects.select_related("department").order_by("emp_id")
    serializer_class = LoginDetailsSerializer
    permission_classes = [AllowAny]
    pagination_class = UserPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "emp_id", "email", "first_name", "last_name", "role"]
    ordering_fields = ["emp_id", "role", "date_joined", "last_login"]

    def list(self, request, *args, **kwargs):
        user_info = getattr(request.user, "emp_id", "Anonymous")
        return super().list(request, *args, **kwargs)
    

    def get_serializer_context(self):
        """Pass request to serializer for permission-based visibility."""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context



# ===========================================================
# GET EMPLOYEE DETAILS BY EMP_ID  (For Performance Module)
# ===========================================================
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.response import Response
# make sure Employee is already imported: from employee.models import Employee, Department

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_employee_by_id(request, emp_id):
    """
    GET /api/employees/employee/<emp_id>/
    Returns basic employee details used by PerformanceMetrics frontend.
    """
    try:
        # Try to get Employee by emp_id
        employee = Employee.objects.select_related("user", "department", "manager").get(emp_id__iexact=emp_id)

        manager_name = None
        if getattr(employee, "manager", None):
            # manager is Employee instance — attempt to derive name from related user
            mgr_user = getattr(employee.manager, "user", None)
            if mgr_user:
                manager_name = f"{mgr_user.first_name or ''} {mgr_user.last_name or ''}".strip()
            else:
                manager_name = getattr(employee.manager, "full_name", None) or str(employee.manager)

        return Response({
            "id": employee.emp_id,
            "firstname": getattr(employee.user, "first_name", "") or getattr(employee, "firstname", ""),
            "lastname": getattr(employee.user, "last_name", "") or getattr(employee, "lastname", ""),
            "designation": employee.department.name if getattr(employee, "department", None) else "",
            "manager": manager_name or "N/A",
        }, status=status.HTTP_200_OK)

    except Employee.DoesNotExist:
        return Response({"error": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
