# ===========================================================
# performance/views.py
# ===========================================================
from rest_framework import viewsets, permissions, status, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from django.db.models import Max, F, Avg, Window, Count
from django.db.models.functions import Rank
from django.db import IntegrityError
from django.utils import timezone
from .models import PerformanceEvaluation
from .serializers import (
    PerformanceEvaluationSerializer,
    PerformanceCreateUpdateSerializer,
    PerformanceDashboardSerializer,
    PerformanceRankSerializer,
)
from employee.models import Employee, Department
from notifications.models import Notification


# ===========================================================
# PERFORMANCE VIEWSET (CRUD + FILTERS)
# ===========================================================
class PerformanceEvaluationViewSet(viewsets.ModelViewSet):
    """
    CRUD APIs for Performance Evaluations.
    - Admin: Full Access
    - Manager: Own Team
    - Employee: Own Records
    """

    queryset = PerformanceEvaluation.objects.select_related(
        "employee__user", "evaluator", "department"
    )
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["review_date", "total_score", "average_score"]
    ordering = ["-review_date"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return PerformanceCreateUpdateSerializer
        return PerformanceEvaluationSerializer

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, "role", "").lower()
        qs = super().get_queryset()

        if role == "manager":
            return qs.filter(employee__manager__user=user)
        elif role == "employee":
            return qs.filter(employee__user=user)
        return qs

    # --------------------------------------------------------
    # CREATE — Auto Rank Trigger + Notification
    # --------------------------------------------------------
    def create(self, request, *args, **kwargs):
        role = getattr(request.user, "role", "").lower()
        if role not in ["admin", "manager"]:
            return Response(
                {"error": "Only Admin or Manager can create evaluations."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()
            instance.auto_rank_trigger()
        except IntegrityError:
            return Response(
                {"error": "Performance record already exists for this week and evaluator."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"error": "An unexpected error occurred while saving evaluation."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Optional Notification
        try:
            Notification.objects.create(
                employee=instance.employee.user,
                message=f"Your weekly performance for {instance.evaluation_period} has been published.",
                auto_delete=True,
            )
        except Exception as e:
            pass

        return Response(
            {
                "message": "Performance evaluation recorded successfully.",
                "data": {
                    "employee_name": f"{instance.employee.user.first_name} {instance.employee.user.last_name}".strip(),
                    "emp_id": instance.employee.user.emp_id,
                    "department_name": getattr(instance.department, "name", None),
                    "average_score": instance.average_score,
                    "rank": instance.rank,
                    "evaluation_period": instance.evaluation_period,
                    "remarks": instance.remarks,
                },
            },
            status=status.HTTP_201_CREATED,
        )


# ===========================================================
# GET PERFORMANCE RECORDS BY EMPLOYEE ID
# ===========================================================
class EmployeePerformanceByIdView(APIView):
    """Return all performance evaluations for a specific employee."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, emp_id):
        role = getattr(request.user, "role", "").lower()
        if role not in ["admin", "manager", "employee"]:
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        try:
            emp = Employee.objects.select_related("user", "department").get(user__emp_id=emp_id)
        except Employee.DoesNotExist:
            return Response({"error": f"Employee '{emp_id}' not found."}, status=status.HTTP_404_NOT_FOUND)

        if role == "employee" and request.user.emp_id != emp_id:
            return Response(
                {"error": "Employees can only view their own performance data."},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = PerformanceEvaluation.objects.filter(employee=emp).select_related("employee__user", "department")

        week = request.query_params.get("week")
        year = request.query_params.get("year")
        if week:
            qs = qs.filter(week_number=week)
        if year:
            qs = qs.filter(year=year)

        if not qs.exists():
            return Response(
                {"message": f"No performance data found for employee {emp_id}."},
                status=status.HTTP_200_OK,
            )

        ranked_qs = qs.annotate(computed_rank=Window(expression=Rank(), order_by=F("average_score").desc()))
        serializer = PerformanceEvaluationSerializer(ranked_qs, many=True)

        return Response(
            {
                "employee": {
                    "emp_id": emp.user.emp_id,
                    "employee_name": f"{emp.user.first_name} {emp.user.last_name}".strip(),
                    "department_name": getattr(emp.department, "name", "-"),
                },
                "record_count": ranked_qs.count(),
                "evaluations": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ===========================================================
# PERFORMANCE SUMMARY (Admin / Manager Dashboard)
# ===========================================================
class PerformanceSummaryView(APIView):
    """Weekly summary of departments and leaderboard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        role = getattr(request.user, "role", "").lower()
        if role not in ["admin", "manager"]:
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        latest_year = PerformanceEvaluation.objects.aggregate(max_year=Max("year"))["max_year"]
        if not latest_year:
            return Response({"message": "No performance data yet."}, status=status.HTTP_200_OK)

        latest_week = PerformanceEvaluation.objects.filter(year=latest_year).aggregate(max_week=Max("week_number"))[
            "max_week"
        ]
        if not latest_week:
            return Response({"message": "No weekly data found."}, status=status.HTTP_200_OK)

        qs = PerformanceEvaluation.objects.filter(year=latest_year, week_number=latest_week).select_related(
            "employee__user", "department"
        )

        overall_avg = round(qs.aggregate(Avg("average_score"))["average_score__avg"] or 0, 2)
        dept_summary = qs.values("department__name").annotate(avg_score=Avg("average_score")).order_by("-avg_score")

        departments = [
            {"department_name": d["department__name"] or "N/A", "average_score": round(d["avg_score"], 2)}
            for d in dept_summary
        ]

        top_3 = qs.order_by("-average_score")[:3]
        weak_3 = qs.order_by("average_score")[:3]

        top_serialized = PerformanceRankSerializer(top_3, many=True).data
        weak_serialized = PerformanceRankSerializer(weak_3, many=True).data

        response = {
            "evaluation_period": f"Week {latest_week}, {latest_year}",
            "overall_average": overall_avg,
            "department_summary": departments,
            "top_3": top_serialized,
            "weak_3": weak_serialized,
        }

        if request.query_params.get("include_rankings", "false").lower() == "true":
            ranked_qs = qs.annotate(rank_position=Window(expression=Rank(), order_by=F("average_score").desc()))
            leaderboard = PerformanceRankSerializer(ranked_qs[:10], many=True).data
            response["leaderboard"] = leaderboard

        return Response(response, status=status.HTTP_200_OK)


# ===========================================================
# EMPLOYEE DASHBOARD (Self Performance Trend)
# ===========================================================
class EmployeeDashboardView(APIView):
    """Displays logged-in employee’s personal performance trend."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        try:
            employee = Employee.objects.select_related("user").get(user=user)
        except Employee.DoesNotExist:
            return Response({"error": "Employee profile not found."}, status=status.HTTP_404_NOT_FOUND)

        records = PerformanceEvaluation.objects.filter(employee=employee).order_by("-review_date")
        if not records.exists():
            return Response({"message": "No performance data found."}, status=status.HTTP_200_OK)

        avg_score = round(records.aggregate(Avg("average_score"))["average_score__avg"] or 0, 2)
        best = records.order_by("-average_score").first()
        serializer = PerformanceDashboardSerializer(records, many=True)

        return Response(
            {
                "employee": {
                    "emp_id": user.emp_id,
                    "employee_name": f"{user.first_name} {user.last_name}".strip(),
                },
                "overall_average": avg_score,
                "best_week": {
                    "evaluation_period": best.evaluation_period,
                    "average_score": best.average_score,
                },
                "trend_data": list(records.values("week_number", "average_score").order_by("week_number")),
                "evaluations": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ===========================================================
# ADMIN / MANAGER: VIEW SPECIFIC EMPLOYEE PERFORMANCE
# ===========================================================
class EmployeePerformanceView(APIView):
    """View all evaluations for a given employee."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, emp_id):
        role = getattr(request.user, "role", "").lower()
        if role not in ["admin", "manager"]:
            return Response(
                {"error": "Only Admin or Manager can view this data."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            emp = Employee.objects.select_related("user", "department", "manager__user").get(user__emp_id=emp_id)
        except Employee.DoesNotExist:
            return Response({"error": f"Employee '{emp_id}' not found."}, status=status.HTTP_404_NOT_FOUND)

        qs = PerformanceEvaluation.objects.filter(employee=emp).order_by("-review_date")
        period = request.query_params.get("evaluation_period")
        if period:
            qs = qs.filter(evaluation_period__iexact=period)

        if not qs.exists():
            return Response({"message": "No records found."}, status=status.HTTP_200_OK)

        serializer = PerformanceEvaluationSerializer(qs, many=True)
        header = {
            "emp_id": emp.user.emp_id,
            "employee_name": f"{emp.user.first_name} {emp.user.last_name}".strip(),
            "department_name": getattr(emp.department, "name", None),
            "manager_name": (
                f"{emp.manager.user.first_name} {emp.manager.user.last_name}".strip()
                if emp.manager else None
            ),
            "available_weeks": list(qs.values_list("evaluation_period", flat=True)),
        }

        return Response({"header": header, "evaluations": serializer.data}, status=status.HTTP_200_OK)


# ===========================================================
# ORGANIZATION PERFORMANCE DASHBOARD (NEW)
# ===========================================================
class PerformanceDashboardView(APIView):
    """
    GET /api/performance/dashboard/
    Returns top performers, weak performers, and department-level averages.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            evaluations = PerformanceEvaluation.objects.select_related("employee__user", "department")
            if not evaluations.exists():
                return Response({"message": "No performance data available."}, status=status.HTTP_200_OK)

            total_employees = evaluations.values("employee").distinct().count()
            total_departments = Department.objects.filter(is_active=True).count()
            org_avg = round(evaluations.aggregate(avg=Avg("average_score"))["avg"] or 0, 2)

            # Department averages
            dept_scores = (
                evaluations.values("department__name")
                .annotate(avg_score=Avg("average_score"))
                .order_by("-avg_score")
            )
            department_average_scores = [
                {
                    "department": d["department__name"],
                    "avg_score": round(d["avg_score"], 2) if d["avg_score"] else 0,
                }
                for d in dept_scores
                if d["department__name"]
            ]

            # Top and weak performers
            employee_scores = (
                evaluations.values(
                    "employee__user__emp_id",
                    "employee__user__first_name",
                    "employee__user__last_name",
                    "department__name",
                )
                .annotate(avg_score=Avg("average_score"))
                .order_by("-avg_score")
            )

            top_3_employees = [
                {
                    "emp_id": e["employee__user__emp_id"],
                    "name": f"{e['employee__user__first_name']} {e['employee__user__last_name']}".strip(),
                    "department": e["department__name"],
                    "average_score": round(e["avg_score"], 2),
                }
                for e in employee_scores[:3]
            ]

            weak_3_employees = [
                {
                    "emp_id": e["employee__user__emp_id"],
                    "name": f"{e['employee__user__first_name']} {e['employee__user__last_name']}".strip(),
                    "department": e["department__name"],
                    "average_score": round(e["avg_score"], 2),
                }
                for e in employee_scores.reverse()[:3]
            ]

            return Response(
                {
                    "organization_average_score": org_avg,
                    "total_departments": total_departments,
                    "total_employees": total_employees,
                    "top_3_employees": top_3_employees,
                    "weak_3_employees": weak_3_employees,
                    "department_average_scores": department_average_scores,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
