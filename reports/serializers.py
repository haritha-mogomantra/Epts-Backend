# reports/serializers.py
from rest_framework import serializers
from performance.models import PerformanceEvaluation
from feedback.models import GeneralFeedback, ManagerFeedback, ClientFeedback
from employee.models import Employee
from .models import CachedReport
from decimal import Decimal, InvalidOperation
from typing import Any, Dict


# =====================================================
# MIXIN — Standardized Score Rounding
# =====================================================
class ScoreMixin:
    """Provides consistent rounding for numeric scores."""

    def round_score(self, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            # Use Decimal for consistent rounding, fallback to float
            return float(Decimal(value).quantize(Decimal("0.01")))
        except (InvalidOperation, TypeError, ValueError):
            try:
                return round(float(value), 2)
            except Exception:
                return 0.0


# =====================================================
# 1. BASIC EMPLOYEE SERIALIZER (Used Across Reports)
# =====================================================
class SimpleEmployeeSerializer(serializers.ModelSerializer, ScoreMixin):
    full_name = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True)
    emp_id = serializers.CharField(source="user.emp_id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "emp_id", "full_name", "email", "department_name"]

    def get_full_name(self, obj):
        user = getattr(obj, "user", None)
        if not user:
            return "-"
        return f"{user.first_name or ''} {user.last_name or ''}".strip()


# =====================================================
# 2. WEEKLY REPORT SERIALIZER
# =====================================================
class WeeklyReportSerializer(serializers.Serializer, ScoreMixin):
    """Represents a single week's consolidated employee performance."""
    emp_id = serializers.CharField()
    employee_full_name = serializers.CharField()
    department = serializers.CharField()
    manager_full_name = serializers.CharField(required=False, allow_null=True, default="-")
    total_score = serializers.FloatField()
    average_score = serializers.FloatField()
    feedback_avg = serializers.FloatField(required=False, allow_null=True, default=0)
    week_number = serializers.IntegerField()
    year = serializers.IntegerField()
    rank = serializers.IntegerField(allow_null=True, required=False)
    remarks = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def validate_average_score(self, value):
        if not (0 <= value <= 100):
            raise serializers.ValidationError("average_score must be between 0 and 100.")
        return value

    def validate_feedback_avg(self, value):
        if value is None:
            return 0.0
        if not (0 <= value <= 10) and not (0 <= value <= 100):
            # allow either 0-10 or 0-100 depending on frontend; this is permissive but safe
            raise serializers.ValidationError("feedback_avg seems out of expected bounds.")
        return value

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # be defensive: ensure keys exist
        rep["average_score"] = self.round_score(rep.get("average_score", 0))
        rep["feedback_avg"] = self.round_score(rep.get("feedback_avg", 0))
        rep["total_score"] = self.round_score(rep.get("total_score", 0))
        return rep


# =====================================================
# 3. MONTHLY REPORT SERIALIZER
# =====================================================
class MonthlyReportSerializer(serializers.Serializer, ScoreMixin):
    """Aggregated monthly performance and feedback summary."""
    emp_id = serializers.CharField()
    employee_full_name = serializers.CharField()
    department = serializers.CharField()
    manager_full_name = serializers.CharField(required=False, allow_null=True, default="-")
    month = serializers.IntegerField()
    year = serializers.IntegerField()
    avg_score = serializers.FloatField()
    feedback_avg = serializers.FloatField(required=False, allow_null=True, default=0)
    best_week = serializers.IntegerField(required=False, allow_null=True)
    best_week_score = serializers.FloatField(required=False, allow_null=True)

    def validate_month(self, value):
        if not (1 <= value <= 12):
            raise serializers.ValidationError("Month must be between 1 and 12.")
        return value

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["avg_score"] = self.round_score(rep.get("avg_score", 0))
        rep["feedback_avg"] = self.round_score(rep.get("feedback_avg", 0))
        rep["best_week_score"] = self.round_score(rep.get("best_week_score", 0))
        return rep


# =====================================================
# 4. EMPLOYEE HISTORY SERIALIZER
# =====================================================
class EmployeeHistorySerializer(serializers.Serializer, ScoreMixin):
    """Weekly trend view for an employee’s performance timeline."""
    week_number = serializers.IntegerField()
    year = serializers.IntegerField()
    average_score = serializers.FloatField()
    feedback_avg = serializers.FloatField(required=False, allow_null=True, default=0)
    remarks = serializers.CharField(allow_null=True, required=False)
    rank = serializers.IntegerField(allow_null=True, required=False)
    manager_full_name = serializers.CharField(required=False, allow_null=True, default="-")
    department = serializers.CharField(required=False, allow_null=True, default="-")
    emp_id = serializers.CharField(required=False, allow_null=True, default="-")


    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["average_score"] = self.round_score(rep.get("average_score", 0))
        rep["feedback_avg"] = self.round_score(rep.get("feedback_avg", 0))
        return rep


# =====================================================
# 5. MANAGER-WISE REPORT SERIALIZER
# =====================================================
class ManagerReportSerializer(serializers.Serializer, ScoreMixin):
    """Weekly report for all employees under a specific manager."""
    manager_full_name = serializers.CharField(required=False, allow_null=True, default="-")
    emp_id = serializers.CharField()
    employee_full_name = serializers.CharField()
    department = serializers.CharField()
    total_score = serializers.FloatField()
    average_score = serializers.FloatField()
    feedback_avg = serializers.FloatField(required=False, allow_null=True, default=0)
    week_number = serializers.IntegerField()
    year = serializers.IntegerField()
    rank = serializers.IntegerField(allow_null=True, required=False)
    remarks = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["average_score"] = self.round_score(rep.get("average_score", 0))
        rep["feedback_avg"] = self.round_score(rep.get("feedback_avg", 0))
        rep["total_score"] = self.round_score(rep.get("total_score", 0))
        return rep


# =====================================================
# 6. DEPARTMENT-WISE REPORT SERIALIZER
# =====================================================
class DepartmentReportSerializer(serializers.Serializer, ScoreMixin):
    """Weekly report across all employees in a department."""
    department_name = serializers.CharField()
    emp_id = serializers.CharField()
    employee_full_name = serializers.CharField()
    manager_full_name = serializers.CharField(required=False, allow_null=True, default="-")
    total_score = serializers.FloatField()
    average_score = serializers.FloatField()
    feedback_avg = serializers.FloatField(required=False, allow_null=True, default=0)
    week_number = serializers.IntegerField()
    year = serializers.IntegerField()
    rank = serializers.IntegerField(allow_null=True, required=False)
    remarks = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["average_score"] = self.round_score(rep.get("average_score", 0))
        rep["feedback_avg"] = self.round_score(rep.get("feedback_avg", 0))
        rep["total_score"] = self.round_score(rep.get("total_score", 0))
        return rep


# =====================================================
# 7. CACHED REPORT SERIALIZER (DB MODEL)
# =====================================================
class CachedReportSerializer(serializers.ModelSerializer):
    """Handles serialization of precomputed/cached reports."""

    generated_by_full_name = serializers.SerializerMethodField(read_only=True)
    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True, default="-")
    period_display = serializers.SerializerMethodField(read_only=True)
    report_label = serializers.SerializerMethodField(read_only=True)
    export_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CachedReport
        fields = [
            "id", "report_type", "year", "week_number", "month",
            "manager", "department", "payload", "file_path",
            "generated_at", "generated_by", "generated_by_name",
            "generated_by_full_name", "is_active", "period_display",
            "report_label", "export_type",
        ]
        read_only_fields = [
            "id", "generated_at", "generated_by_name",
            "generated_by_full_name", "period_display", "report_label", "export_type",
        ]

    def get_generated_by_full_name(self, obj):
        user = getattr(obj, "generated_by", None)
        if user:
            return f"{user.first_name or ''} {user.last_name or ''}".strip()
        return "-"

    def get_period_display(self, obj):
        """Readable period for dashboards and exports."""
        try:
            return obj.get_period_display()
        except Exception:
            return ""

    def get_report_label(self, obj):
        """Return contextual label for frontend cards."""
        try:
            return obj.report_scope
        except Exception:
            return obj.report_type

    def get_export_type(self, obj):
        try:
            return obj.export_type
        except Exception:
            return "-"


# =====================================================
# 8. COMBINED / AGGREGATED REPORT SERIALIZER
# =====================================================
class CombinedReportSerializer(serializers.Serializer, ScoreMixin):
    """
    Combines performance + feedback + ranking into a single analytic payload.
    Used for analytics dashboards and Power BI export APIs.
    """
    type = serializers.ChoiceField(choices=["weekly", "monthly", "manager", "department"])
    year = serializers.IntegerField()
    week_or_month = serializers.IntegerField()
    generated_by_full_name = serializers.CharField()
    total_employees = serializers.IntegerField()
    average_org_score = serializers.FloatField()
    top_performers = serializers.ListField(child=serializers.CharField())
    weak_performers = serializers.ListField(child=serializers.CharField())
    feedback_summary = serializers.DictField(child=serializers.FloatField())
    top3_ranking = serializers.ListField(child=serializers.DictField(), required=False)
    weak3_ranking = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure numeric bounds for week/month."""
        report_type = data.get("type")
        period = data.get("week_or_month")

        if report_type in ["weekly", "manager", "department"]:
            if not isinstance(period, int) or not (1 <= period <= 53):
                raise serializers.ValidationError({"week_or_month": "Invalid week number (must be 1–53)."})
        if report_type == "monthly":
            if not isinstance(period, int) or not (1 <= period <= 12):
                raise serializers.ValidationError({"week_or_month": "Invalid month (must be 1–12)."})
        # Ensure feedback_summary numeric values
        for k, v in data.get("feedback_summary", {}).items():
            try:
                float(v)
            except Exception:
                raise serializers.ValidationError({f"feedback_summary.{k}": "Must be numeric."})
        return data

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["average_org_score"] = self.round_score(rep.get("average_org_score", 0))
        # sanitize feedback_summary
        fs = rep.get("feedback_summary") or {}
        rep["feedback_summary"] = {k: self.round_score(v) for k, v in fs.items()}
        return rep
