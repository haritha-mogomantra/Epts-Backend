# ===========================================================
# performance/views_reports.py
# ===========================================================
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone

from .models import PerformanceEvaluation
from employee.models import Employee, Department
from .serializers import PerformanceEvaluationSerializer
from .utils_export import generate_excel_report, generate_pdf_report


# ===========================================================
# Weekly / Department / Manager Report
# ===========================================================
class PerformanceReportView(generics.ListAPIView):
    serializer_class = PerformanceEvaluationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = PerformanceEvaluation.objects.select_related(
            "employee__user", "department", "employee__manager__user"
        )

        filter_type = self.request.query_params.get("filter", "weekly").lower()
        value = self.request.query_params.get("value", None)

        if user.role == "Manager":
            qs = qs.filter(employee__manager__user=user)
        elif user.role == "Employee":
            qs = qs.filter(employee__user=user)

        if filter_type == "weekly" and value:
            qs = qs.filter(week_number=value)
        elif filter_type == "department" and value:
            qs = qs.filter(department__code__iexact=value)
        elif filter_type == "manager" and value:
            qs = qs.filter(employee__manager__user__emp_id__iexact=value)

        return qs.order_by("-year", "-week_number")

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "count": len(serializer.data),
            "results": serializer.data
        })


# ===========================================================
# Excel Export (All or Filtered)
# ===========================================================
class PerformanceExcelExportView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = PerformanceEvaluation.objects.select_related("employee__user", "department")
        filter_type = request.query_params.get("filter")
        value = request.query_params.get("value")

        if filter_type == "department" and value:
            qs = qs.filter(department__code__iexact=value)
        elif filter_type == "manager" and value:
            qs = qs.filter(employee__manager__user__emp_id__iexact=value)
        elif filter_type == "week" and value:
            qs = qs.filter(week_number=value)

        filename = f"performance_report_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return generate_excel_report(qs, filename)
    

# ===========================================================
# Individual PDF Report (Employee)
# ===========================================================
class EmployeePerformancePDFView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, emp_id):
        employee = get_object_or_404(Employee, user__emp_id__iexact=emp_id)
        evaluations = PerformanceEvaluation.objects.filter(employee=employee).order_by("-year", "-week_number")
        return generate_pdf_report(employee, evaluations)



# ===========================================================
# Manager-wise Weekly Performance Report
# ===========================================================
from rest_framework.exceptions import ValidationError

class ManagerWiseWeeklyReportView(generics.ListAPIView):
    serializer_class = PerformanceEvaluationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        manager_name = self.request.query_params.get("manager_name")
        week = self.request.query_params.get("week")
        year = self.request.query_params.get("year")

        if not manager_name:
            raise ValidationError({"manager_name": "Manager name is required."})

        # Split full name → first and last
        parts = manager_name.split()
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        # Get manager
        manager = Employee.objects.filter(
            user__first_name__iexact=first_name,
            user__last_name__iexact=last_name,
            user__role="Manager"
        ).first()

        if not manager:
            raise ValidationError({"manager_name": f"Manager '{manager_name}' not found."})

        qs = PerformanceEvaluation.objects.filter(employee__manager=manager)

        if week:
            qs = qs.filter(week_number=week)
        if year:
            qs = qs.filter(year=year)

        return qs.order_by("-year", "-week_number")
