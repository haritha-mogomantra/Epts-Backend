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
from django.db.models import Q

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
    permission_classes = [permissions.AllowAny]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "code"]
    ordering_fields = ["name", "created_at", "code"]

    def get_queryset(self):
        qs = super().get_queryset()
        include_inactive = self.request.query_params.get("include_inactive", "").lower()
        user = self.request.user
        if include_inactive == "true" and (user.is_superuser or getattr(user, "role", "") == "Admin"):
            return qs
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
    queryset = Employee.objects.select_related("user", "department", "manager").prefetch_related("team_members").filter(is_deleted=False)
    permission_classes = [permissions.AllowAny]
    pagination_class = DefaultPagination
    lookup_field = "user__emp_id"
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status"]
    search_fields = [
        "user__first_name", "user__last_name", "user__emp_id",
        "designation", "contact_number", "department__name"
    ]
    ordering_fields = ["joining_date", "user__first_name", "user__emp_id"]

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

        return qs
 

    def get_object(self):
        emp_id = self.kwargs.get("user__emp_id")
        try:
            employee = (
                Employee.objects
                .select_related("user", "department", "manager")
                .get(user__emp_id__iexact=emp_id)
            )
            if employee.is_deleted:
                raise ValidationError("This employee has been deleted. No further actions allowed.")
            return employee
        except Employee.DoesNotExist:
            raise NotFound(detail=f"Employee with emp_id '{emp_id}' not found.")
        except ValidationError as e:
            raise NotFound(detail=str(e))

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
        emp_id = kwargs.get("user__emp_id")

        try:
            # ✅ Fetch even if model has custom validation
            employee = Employee.objects.select_related("user").get(user__emp_id=emp_id, is_deleted=False)
        except Employee.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        # Directly perform raw DB update to avoid triggering model.save()
        Employee.objects.filter(id=employee.id).update(is_deleted=True)

        return Response({"message": "Employee deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


    @action(detail=False, methods=["GET"], url_path="managers")
    def list_managers(self, request):
        managers = Employee.objects.select_related("user").filter(
            Q(user__role__in=["Manager", "Admin"]),
            is_deleted=False,
            status="Active"
        ).order_by("user__first_name")

        return Response([
            {
                "emp_id": emp.emp_id,
                "full_name": f"{emp.user.first_name} {emp.user.last_name}".strip()
            }
            for emp in managers
        ], status=status.HTTP_200_OK)
# ===========================================================
# ADMIN PROFILE VIEW
# ===========================================================
class AdminProfileView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        user = request.user
        if getattr(user, "role", "") != "Admin":
            return Response({"error": "Only Admins can access this API."}, status=status.HTTP_403_FORBIDDEN)
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AdminProfileSerializer(employee, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request):
        user = request.user
        if getattr(user, "role", "") != "Admin":
            return Response({"error": "Only Admins can update profile."}, status=status.HTTP_403_FORBIDDEN)

        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdminProfileSerializer(employee, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


# ===========================================================
# MANAGER PROFILE VIEW
# ===========================================================
class ManagerProfileView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        user = request.user
        if getattr(user, "role", "") != "Manager":
            return Response({"error": "Only Managers can access this API."}, status=status.HTTP_403_FORBIDDEN)
        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ManagerProfileSerializer(employee, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request):
        user = request.user
        if getattr(user, "role", "") != "Manager":
            return Response({"error": "Only Managers can update profile."}, status=status.HTTP_403_FORBIDDEN)

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
# EMPLOYEE PROFILE VIEW (NEW)
# ===========================================================
class EmployeeProfileView(APIView):
    """API for Employee personal profile (view/update)."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        user = request.user
        if getattr(user, "role", "") != "Employee":
            return Response({"error": "Only Employees can access this API."}, status=status.HTTP_403_FORBIDDEN)

        employee = getattr(user, "employee_profile", None)
        if not employee:
            return Response({"error": "Employee record not found for this user."}, status=status.HTTP_404_NOT_FOUND)

        serializer = EmployeeProfileSerializer(employee, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request):
        user = request.user
        if getattr(user, "role", "") != "Employee":
            return Response({"error": "Only Employees can update profile."}, status=status.HTTP_403_FORBIDDEN)

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
    permission_classes = [permissions.AllowAny]

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
