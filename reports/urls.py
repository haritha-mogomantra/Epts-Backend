# ===============================================
# reports/urls.py
# ===============================================

from django.urls import path
from .views import (
    LatestWeekView,
    WeeklyReportView,
    MonthlyReportView,
    DepartmentReportView,
    ManagerReportView,
    ExportWeeklyExcelView,
    ExportMonthlyExcelView,
    PrintPerformanceReportView, 
    CachedReportListView,
    CachedReportArchiveView,
    CachedReportRestoreView,
    
)

# Namespace
app_name = "reports"

# ===========================================================
# ROUTE SUMMARY
# ===========================================================
"""
Reporting & Analytics Endpoints:
-------------------------------------------------------------
🔹 /api/reports/weekly/                     → Weekly consolidated report
🔹 /api/reports/monthly/                    → Monthly consolidated report
🔹 /api/reports/department/                 → Department-wise weekly report
🔹 /api/reports/manager/                    → Manager-wise weekly report (placeholder)
🔹 /api/reports/export/weekly-excel/        → Weekly Excel export (.xlsx)
🔹 /api/reports/export/monthly-excel/       → Monthly Excel export (.xlsx)
🔹 /api/reports/print/<emp_id>/             → Employee-specific PDF report
🔹 /api/reports/cache/                      → Cached report listing
🔹 /api/reports/cache/<id>/archive/         → Archive cached report
🔹 /api/reports/cache/<id>/restore/         → Restore cached report
-------------------------------------------------------------
All routes are authenticated (Admin/Manager access).
"""

# ===========================================================
# URL Patterns
# ===========================================================
urlpatterns = [
    # Weekly & Monthly Reports
    path("weekly/", WeeklyReportView.as_view(), name="weekly_report"),
    path("monthly/", MonthlyReportView.as_view(), name="monthly_report"),

    # Department & Manager Reports
    path("department/", DepartmentReportView.as_view(), name="department_report"),
    path("manager/", ManagerReportView.as_view(), name="manager_report"),

    # Excel Exports
    path("export/weekly-excel/", ExportWeeklyExcelView.as_view(), name="export_weekly_excel"),
    path("export/monthly-excel/", ExportMonthlyExcelView.as_view(), name="export_monthly_excel"),

    # PDF Export (New)
    path("print/<str:emp_id>/", PrintPerformanceReportView.as_view(), name="print_employee_report"),

    # Cached Reports
    path("cache/", CachedReportListView.as_view(), name="cached_reports_dashboard"),
    path("cache/<int:pk>/archive/", CachedReportArchiveView.as_view(), name="cached_report_archive"),
    path("cache/<int:pk>/restore/", CachedReportRestoreView.as_view(), name="cached_report_restore"),

    path("latest-week/", LatestWeekView.as_view(), name="latest_week"),
]
