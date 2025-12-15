# ===========================================================
# employee/views.py
# ===========================================================

from rest_framework import viewsets, status, permissions, filters
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from django.contrib.auth import get_user_model
from rest_framework.pagination import PageNumberPagination
from django.db import models, transaction
from django.db.models import Q, F, Func, Value, CharField, DateField
from django.db.models.functions import Coalesce, Concat
from django.db.models.functions import Coalesce, Concat, NullIf


from .models import Department, Employee
from .serializers import (
    DepartmentSerializer,
    EmployeeSerializer,
    EmployeeCreateUpdateSerializer,
    EmployeeCSVUploadSerializer,
    AdminProfileSerializer,
    ManagerProfileSerializer,
    EmployeeProfileSerializer,  
)

User = get_user_model()


# ===========================================================
# PAGINATION
# ===========================================================
class DefaultPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "count": self.page.paginator.count,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "current_page": self.page.number,
            "total_pages": self.page.paginator.num_pages,
            "results": data
        })


# ===========================================================
# DEPARTMENT VIEWSET
# ===========================================================
class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all().order_by("name")
    serializer_class = DepartmentSerializer
    lookup_field = "code"
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "code"]
    ordering_fields = ["name", "created_at", "code"]

    def get_queryset(self):
        qs = super().get_queryset()

        include_inactive = self.request.query_params.get("include_inactive", "").lower()
        user = self.request.user

        # Admin can view all departments
        if include_inactive == "true" and (user.is_superuser or getattr(user, "role", "") == "Admin"):
            return qs

        # Default: only active departments
        return qs.filter(is_active=True)

    def _is_admin(self, request):
        return request.user.is_superuser or getattr(request.user, "role", "") == "Admin"

    def create(self, request, *args, **kwargs):
        if not self._is_admin(request):
            return Response({"error": "Only Admins can create departments."}, status=status.HTTP_403_FORBIDDEN)

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not self._is_admin(request):
            return Response({"error": "Only Admins can update departments."}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not self._is_admin(request):
            return Response({"error": "Only Admins can delete departments."}, status=status.HTTP_403_FORBIDDEN)

        force_delete = request.query_params.get("force", "").lower() == "true"
        if force_delete:
            instance.delete()
            return Response({"message": f"Department '{instance.name}' permanently deleted."},
                            status=status.HTTP_204_NO_CONTENT)

        if instance.employees.filter(status="Active", is_deleted=False).exists():
            return Response({"error": "Cannot deactivate department with active employees."},
                            status=status.HTTP_400_BAD_REQUEST)

        instance.is_active = False
        instance.save(update_fields=["is_active"])
        return Response({"message": f"Department '{instance.name}' deactivated successfully."},
                        status=status.HTTP_200_OK)


# ===========================================================
# EMPLOYEE VIEWSET
# ===========================================================
class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.select_related("user", "department", "manager").prefetch_related("team_members")
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = DefaultPagination
    lookup_field = "emp_id"
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status"]
    search_fields = [
        "user__first_name", "user__last_name", "user__emp_id",
        "designation", "contact_number", "department__name"
    ]
    ordering_fields = [
        "user__emp_id",
        "full_name",
        "designation",
        "project_name",
        "manager_name",
        "department__name",
        "joining_sort",
        "user__email",
    ]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return EmployeeCreateUpdateSerializer
        return EmployeeSerializer

    def _has_admin_rights(self, user):
        return user.is_superuser or getattr(user, "role", "") in ["Admin", "Manager"]

    def get_queryset(self):
        request = self.request
        user = request.user
        qs = Employee.objects.select_related("user", "department", "manager")


        role = getattr(user, "role", "")
        if role == "Manager":
            qs = qs.filter(manager__user=user)
        elif role == "Employee":
            qs = qs.filter(user=user)

        department_param = request.query_params.get("department")
        if department_param:
            dept_qs = Department.objects.filter(
                Q(name__iexact=department_param)
                | Q(code__iexact=department_param)
                | Q(id__iexact=department_param)
            )
            dept = dept_qs.first()
            qs = qs.filter(department=dept) if dept else qs.filter(department__name__icontains=department_param)

        manager_param = request.query_params.get("manager")
        if manager_param:
            manager_emp = Employee.objects.select_related("user").filter(
                Q(user__emp_id__iexact=manager_param) | Q(user__username__iexact=manager_param)
            ).first()
            qs = qs.filter(manager=manager_emp) if manager_emp else qs.none()

        role_param = request.query_params.get("role")
        if role_param:
            qs = qs.filter(user__role__iexact=role_param.strip())

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status__iexact=status_param.strip())

        qs = qs.annotate(
            full_name=Concat(
                "user__first_name",
                Value(" "),
                "user__last_name"
            ),
            manager_name=Concat(
                Coalesce(F("manager__user__first_name"), Value("")),
                Value(" "),
                Coalesce(F("manager__user__last_name"), Value("")),
                output_field=CharField()
            ),
            # ASC key → empty managers sorted LAST
            manager_sort_key_asc=Coalesce(
                NullIf(
                    Concat(
                        Coalesce(F("manager__user__first_name"), Value("")),
                        Value(" "),
                        Coalesce(F("manager__user__last_name"), Value("")),
                    ),
                    Value("")
                ),
                Value("ZZZZZZZZ"),    # ensures empty names go last on ASC
                output_field=CharField()
            ),

            # DESC key → empty managers still sorted LAST
            manager_sort_key_desc=Coalesce(
                NullIf(
                    Concat(
                        Coalesce(F("manager__user__first_name"), Value("")),
                        Value(" "),
                        Coalesce(F("manager__user__last_name"), Value("")),
                    ),
                    Value("")
                ),
                Value("00000000"),    # ensures empty names go last on DESC
                output_field=CharField()
            ),
            joining_sort=F("joining_date")
        )

        return qs


    def list(self, request, *args, **kwargs):
        """
        Ensures pagination always respects search + filters + ordering together.
        This is enterprise-standard DRF behavior enforcement.
        """

        queryset = self.filter_queryset(self.get_queryset())

        # ===================== CUSTOM NULLS-LAST SORTING FIX ======================
        ordering = request.query_params.get("ordering")

        if ordering == "manager_name":
            queryset = queryset.order_by("manager_sort_key_asc")

        elif ordering == "-manager_name":
            queryset = queryset.order_by("-manager_sort_key_desc")

        # ==========================================================================

        # Continue normal flow
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)


        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
 

    def get_object(self):
        emp_id = self.kwargs.get("emp_id")

        # RULE 1 — If starts with EMP, format is always valid (NO LENGTH CHECK)
        if emp_id.upper().startswith("EMP") and not emp_id[3:].isdigit():
            # Example: EMP, EMP0, EMP00, EMPA → valid format but incomplete → return None
            return None

        # RULE 2 — Query actual employee table
        try:
            return Employee.objects.select_related("user", "department", "manager").get(
                user__emp_id__iexact=emp_id
            )
        except Employee.DoesNotExist:
            raise NotFound(detail=f"Employee with emp_id '{emp_id}' not found.")


    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        employee.refresh_from_db()

        total_count = Employee.objects.filter(is_deleted=False).count()

        return Response({
            "message": "Employee created successfully.",
            "employee": EmployeeSerializer(employee, context={"request": request}).data,
            "total_employees": total_count  
        }, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        employee = self.get_object()
        if employee.is_deleted:
            return Response({"error": "This employee has been deleted. No updates allowed."},
                            status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        if getattr(user, "role", "") == "Manager" and employee.manager and employee.manager.user != user:
            return Response({"error": "Managers can update only their own team members."},
                            status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(employee, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        employee.refresh_from_db()

        return Response({"message": "Employee updated successfully.",
                         "employee": EmployeeSerializer(employee, context={"request": request}).data},
                        status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        employee = self.get_object()

        # 🚫 If already deleted, stop here
        if employee.is_deleted:
            return Response(
                {"error": "Employee is already deleted."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 🚫 If already inactive, prevent duplicate updates
        if employee.status == "Inactive":
            return Response({"error": "Employee is already inactive."}, status=400)

        # 👍 Mark only inactive (NOT soft delete)
        employee.status = "Inactive"

        # Deactivate login account
        if employee.user:
            employee.user.is_active = False
            employee.user.save(update_fields=["is_active"])

        employee.save(update_fields=["status"])

        return Response({"message": "Employee marked inactive"}, status=200)


    @action(detail=False, methods=["GET"], url_path="managers")
    def list_managers(self, request):
        dept_code = request.query_params.get("department_code")

        managers = Employee.objects.select_related("user", "department").filter(
            Q(user__role__in=["Manager", "Admin"]),
            is_deleted=False,
            status="Active"
        )

        #FILTER BY DEPARTMENT WHEN PROVIDED
        if dept_code:
            managers = managers.filter(department__code=dept_code)

        managers = managers.order_by("user__first_name")

        return Response([
            {
                "emp_id": emp.emp_id,
                "full_name": f"{emp.user.first_name} {emp.user.last_name}".strip(),
                "department": emp.department.code
            }
            for emp in managers
        ], status=status.HTTP_200_OK)
# ===========================================================
# ADMIN PROFILE VIEW
# ===========================================================
class AdminProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)


        # AUTO-CREATE EMPLOYEE PROFILE FOR ADMIN IF MISSING
        employee = getattr(user, "employee_profile", None)

        if not employee:
            # Try active department first, fallback to first available
            dept = (
                Department.objects.filter(name__iexact="Administration").first()
                or Department.objects.filter(code__iexact="ADMIN").first()
                or Department.objects.filter(is_active=True).first()
            )

            employee = Employee.objects.create(
                user=user,
                role="Admin",
                department=dept,
                designation="Administrator"
            )

        serializer = AdminProfileSerializer(employee, context={"request": request})
        data = serializer.data

        return Response({
            "personal": data.get("personal", {}),
            "professional": data.get("professional", {}),
            "address": data.get("address", {})
        }, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request):
        user = request.user

        # User can update only their own profile
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)

        # AUTO-CREATE EMPLOYEE PROFILE IF MISSING
        employee = getattr(user, "employee_profile", None)
        if not employee:
            dept = (
                Department.objects.filter(name__iexact="Administration").first()
                or Department.objects.filter(code__iexact="ADMIN").first()
                or Department.objects.filter(is_active=True).first()
            )
            employee = Employee.objects.create(
                user=user,
                role="Admin",
                department=dept,
                designation="Administrator"
            )

        serializer = AdminProfileSerializer(employee, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        data = serializer.data

        return Response({
            "profile_picture_url": data.get("personal", {}).get("profile_picture_url"),
            **data  # return original grouped sections
        }, status=status.HTTP_200_OK)


    def put(self, request):
        return self.patch(request)

# ===========================================================
# MANAGER PROFILE VIEW
# ===========================================================
class ManagerProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)

        
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ManagerProfileSerializer(employee, context={"request": request})
        data = serializer.data
        return Response({
            "profile_picture_url": data.get("profile_picture_url"),   # manager serializer is flat
            **data
        }, status=status.HTTP_200_OK)


    @transaction.atomic
    def patch(self, request):
        user = request.user
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)


        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ManagerProfileSerializer(employee, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        return self.patch(request)


# ===========================================================
# EMPLOYEE PROFILE VIEW
# ===========================================================
class EmployeeProfileView(APIView):
    """API for Employee personal profile (view/update)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)


        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found for this user."}, status=status.HTTP_404_NOT_FOUND)

        serializer = EmployeeProfileSerializer(employee, context={"request": request})
        data = serializer.data
        return Response({
            "profile_picture_url": data.get("profile_picture_url"),
            **data
        }, status=status.HTTP_200_OK)


    @transaction.atomic
    def patch(self, request):
        user = request.user
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Profile not found for this user."},
                            status=status.HTTP_404_NOT_FOUND)


        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = EmployeeProfileSerializer(employee, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        return self.patch(request)


# ===========================================================
# EMPLOYEE BULK CSV UPLOAD VIEW
# ===========================================================
class EmployeeCSVUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
            #if not (request.user.is_superuser or getattr(request.user, "role", "") == "Admin"):
                #return Response({"error": "Only Admins can upload employee CSV files."},
                            #status=status.HTTP_403_FORBIDDEN)

        serializer = EmployeeCSVUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        return Response({
            "message": "Employee CSV processed successfully.",
            "uploaded_count": result.get("success_count", 0),
            "errors": result.get("errors", []),
        }, status=status.HTTP_201_CREATED)
    

    @action(detail=False, methods=["POST"], url_path="upload_csv")
    @transaction.atomic
    def upload_csv(self, request):
        serializer = EmployeeCSVUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        total_employees = Employee.objects.filter(is_deleted=False).count()

        return Response({
            "message": f"CSV processed successfully. {result.get('success_count', 0)} added.",
            "errors": result.get("errors", []),
            "total_employees": total_employees  # aSSllows frontend to jump to last page
        }, status=status.HTTP_200_OK)
