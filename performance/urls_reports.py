# ===========================================================
# performance/urls_reports.py
# ===========================================================
from django.urls import path
from .views_reports import (
    PerformanceReportView,
    PerformanceExcelExportView,
    EmployeePerformancePDFView,
    ManagerWiseWeeklyReportView,
)

app_name = "performance-reports"

urlpatterns = [
    path("reports/", PerformanceReportView.as_view(), name="performance-report"),
    path("reports/export-excel/", PerformanceExcelExportView.as_view(), name="export-excel"),
    path("reports/<str:emp_id>/export-pdf/", EmployeePerformancePDFView.as_view(), name="export-pdf"),
    path("reports/manager/", ManagerWiseWeeklyReportView.as_view(), name="manager-weekly-report"),
]
