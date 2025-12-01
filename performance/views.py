# ===========================================================
# performance/views.py
# ===========================================================
from rest_framework import viewsets, permissions, status, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django.db.models import Max, F, Avg, Window, Count, Q
from django.db.models.functions import Rank, DenseRank
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
from datetime import date
from .models import get_week_range



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
        """
        Return queryset scoped by role and optional week/year filters.

        Query params supported:
        - week  (int)  : ISO week number (also accepts 'week_number')
        - year  (int)

        Behavior:
        - If both week+year provided -> return that week's records.
        - If only week provided -> use most recent year that has that week (fallback to current year).
        - If only year provided -> return whole year (all weeks).
        - If neither provided -> return latest week available in DB (year + week).
        """
        user = self.request.user
        role = getattr(user, "role", "").lower()
        qs = super().get_queryset()

        # role scoping
        if role == "manager":
            qs = qs.filter(employee__manager__user=user)
        elif role == "employee":
            qs = qs.filter(employee__user=user)

        # Accept either 'week' or 'week_number' (frontend may send either)
        req_week = self.request.query_params.get("week") or self.request.query_params.get("week_number")
        req_year = self.request.query_params.get("year")

        # normalize to ints when present
        def to_int(val):
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        week = to_int(req_week)
        year = to_int(req_year)

        # If both provided -> filter by exact week/year
        if week and year:
            return qs.filter(week_number=week, year=year).select_related("employee__user", "department")


        # If week provided but year NOT provided → return empty (DO NOT auto-guess)
        if week and not year:
            return qs.filter(week_number=week, year=timezone.now().year).select_related("employee__user", "department")
            
            # fallback: filter by week number across years (rare)
            return qs.filter(week_number=week).select_related("employee__user", "department")



        # If only year provided -> return entire year (all weeks)
        if year and not week:
            return qs.filter(year=year).select_related("employee__user", "department")

        # If neither provided -> choose latest week available in DB (preferred)
        latest_year = PerformanceEvaluation.objects.aggregate(max_year=Max("year"))["max_year"]
        if latest_year:
            latest_week = PerformanceEvaluation.objects.filter(year=latest_year).aggregate(max_week=Max("week_number"))["max_week"]
            if latest_week:
                return qs.filter(year=latest_year, week_number=latest_week).select_related("employee__user", "department")

        # Last fallback: return qs ordered by review_date
        return qs.select_related("employee__user", "department").order_by("-review_date")


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

        serializer = self.get_serializer(
            data=request.data,
            context={
                "request": request,
                "week_number": request.data.get("week_number"),
                "year": request.data.get("year"),
            }
        )
        serializer.is_valid(raise_exception=True)

        try:
            instance = serializer.save()       # create + calculate scores in model.save()
            instance.refresh_from_db()         # critical: pull updated scores/evaluation_period
            instance.refresh_from_db()         # critical: pull updated rank
        except IntegrityError:
            return Response(
                {"error": "Performance record already exists for this week and evaluator."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"error": "An unexpected error occurred while saving evaluation.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Optional Notification
        try:
            Notification.objects.create(
                employee=instance.employee.user,
                message=f"Your weekly performance for {instance.evaluation_period} has been published.",
                auto_delete=True,
            )
        except Exception:
            pass

        return Response(
            {
                "message": "Performance evaluation recorded successfully.",
                "data": {
                    "evaluation_id": instance.id,
                    "emp_id": instance.employee.user.emp_id,
                    "employee_name": f"{instance.employee.user.first_name} {instance.employee.user.last_name}".strip(),
                    "department_name": getattr(instance.department, "name", None),

                    # ⭐ These were stale before — now corrected
                    "total_score": instance.total_score,
                    "average_score": instance.average_score,
                    "evaluation_period": instance.evaluation_period,
                    "rank": instance.rank,

                    "remarks": instance.remarks,
                },
            },
            status=status.HTTP_201_CREATED,
        )
   
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()

        # Always use the READ-ONLY serializer for GET
        serializer = PerformanceEvaluationSerializer(instance)
        data = serializer.data

        # Ensure metrics always populated
        data["metrics"] = PerformanceEvaluationSerializer(instance).get_metrics(instance)

        # Ensure rank always sent
        data["rank"] = instance.rank

        # Ensure department name appears
        data["department_name"] = getattr(instance.employee.department, "name", None)

        # Ensure employee name appears
        if instance.employee and instance.employee.user:
            data["employee_name"] = (
                f"{instance.employee.user.first_name} {instance.employee.user.last_name}".strip()
            )
            data["employee_emp_id"] = instance.employee.user.emp_id

        # Ensure evaluator name appears
        if instance.evaluator:
            data["evaluator_name"] = (
                f"{instance.evaluator.first_name} {instance.evaluator.last_name}".strip()
            )

        return Response(data)



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

        # normalize numeric filters
        if week:
            try:
                week = int(week)
            except:
                return Response({"error": "Invalid week"}, status=400)
            qs = qs.filter(week_number=week)

        if year:
            try:
                year = int(year)
            except:
                return Response({"error": "Invalid year"}, status=400)
            qs = qs.filter(year=year)


        if not qs.exists():
            return Response(
                {
                    "employee": {
                        "emp_id": emp.user.emp_id,
                        "employee_name": f"{emp.user.first_name} {emp.user.last_name}".strip(),
                        "department_name": getattr(emp.department, "name", "-"),
                    },
                    "record_count": 0,
                    "evaluations": []
                },
                status=status.HTTP_200_OK,
            )

        serializer = PerformanceEvaluationSerializer(qs, many=True)

        return Response(
            {
                "employee": {
                    "emp_id": emp.user.emp_id,
                    "employee_name": f"{emp.user.first_name} {emp.user.last_name}".strip(),
                    "department_name": getattr(emp.department, "name", "-"),
                },
                "record_count": qs.count(),
                "evaluations": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


# ===========================================================
# PERFORMANCE SUMMARY (Admin / Manager Dashboard)
# ===========================================================
class PerformanceSummaryView(APIView):
    """Weekly summary of departments and leaderboard with pagination."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):

        role = getattr(request.user, "role", "").lower()
        if role not in ["admin", "manager"]:
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        # --- Read Optional Week/Year From Frontend ---
        req_week = request.query_params.get("week")
        req_year = request.query_params.get("year")

        req_week = request.query_params.get("week")
        req_year = request.query_params.get("year")

        # normalize incoming values
        if req_week:
            try:
                req_week = int(req_week)
            except:
                return Response({"error": "Invalid week number"}, status=400)

        if req_year:
            try:
                req_year = int(req_year)
            except:
                return Response({"error": "Invalid year"}, status=400)

        if req_week and req_year:
            # Get base dataset for the week (no search yet)
            base_qs = PerformanceEvaluation.objects.filter(
                year=req_year,
                week_number=req_week
            ).select_related("employee__user", "department")

            #  Compute TRUE RANK on full dataset
            ranked = base_qs.annotate(
                full_rank=Window(
                    expression=DenseRank(),
                    order_by=F("total_score").desc()
                )
            ).values("id", "full_rank")

            # Create rank map { evaluation_id : rank }
            rank_map = {row["id"]: row["full_rank"] for row in ranked}

            # Apply search (DO NOT recalculate rank)
            qs = base_qs
            search = request.query_params.get("search", "").strip()
            if search:
                qs = qs.filter(
                    Q(employee__user__emp_id__icontains=search) |
                    Q(employee__user__first_name__icontains=search) |
                    Q(employee__user__last_name__icontains=search) |
                    Q(department__name__icontains=search)
                )

            # Apply sorting using TRUE rank
            sort_by = request.query_params.get("sort_by")
            order = request.query_params.get("order", "asc")
            order_prefix = "-" if order == "desc" else ""

            SORT_MAP = {
                "emp_id": "employee__user__emp_id",
                "full_name": "employee__user__first_name",
                "total_score": "total_score",
                "rank": "full_rank",
            }

            db_field = SORT_MAP.get(sort_by)
            if db_field:
                qs = qs.order_by(f"{order_prefix}{db_field}")
            else:
                qs = qs.order_by("-total_score")

            # Inject TRUE rank
            for obj in qs:
                obj.week_rank = rank_map.get(obj.id)


            start, end = get_week_range(int(req_year), int(req_week))
            evaluation_period = (
                f"Week {req_week} ({start.strftime('%d %b')} - {end.strftime('%d %b %Y')})"
            )

        else:
            today = date.today()
            current_year, current_week, _ = today.isocalendar()

            latest_record = (
                PerformanceEvaluation.objects
                .exclude(year=current_year, week_number=current_week)
                .order_by("-year", "-week_number")
                .first()
            )

            if not latest_record:
                latest_record = (
                    PerformanceEvaluation.objects
                    .order_by("-year", "-week_number")
                    .first()
                )

            if not latest_record:
                return Response({"message": "No performance data available."}, status=status.HTTP_200_OK)

            latest_year = latest_record.year
            latest_week = latest_record.week_number

            # 1️⃣ Base queryset (NO search yet)
            base_qs = PerformanceEvaluation.objects.filter(
                year=latest_year,
                week_number=latest_week
            ).select_related("employee__user", "department")

            # 2️⃣ Compute TRUE RANK on full dataset
            ranked = base_qs.annotate(
                full_rank=Window(
                    expression=DenseRank(),
                    order_by=F("total_score").desc()
                )
            ).values("id", "full_rank")

            # 3️⃣ Rank map
            rank_map = {row["id"]: row["full_rank"] for row in ranked}

            # 4️⃣ Apply search (DO NOT recalc rank)
            qs = base_qs
            search = request.query_params.get("search", "").strip()
            if search:
                qs = qs.filter(
                    Q(employee__user__emp_id__icontains=search) |
                    Q(employee__user__first_name__icontains=search) |
                    Q(employee__user__last_name__icontains=search) |
                    Q(department__name__icontains=search)
                )

            # 5️⃣ Sorting using TRUE rank
            sort_by = request.query_params.get("sort_by")
            order = request.query_params.get("order", "asc")
            order_prefix = "-" if order == "desc" else ""

            SORT_MAP = {
                "emp_id": "employee__user__emp_id",
                "full_name": "employee__user__first_name",
                "total_score": "total_score",
                "rank": "full_rank",
            }

            db_field = SORT_MAP.get(sort_by)
            if db_field:
                qs = qs.order_by(f"{order_prefix}{db_field}")
            else:
                qs = qs.order_by("-total_score")

            # 6️⃣ Inject true rank into each object
            for obj in qs:
                obj.week_rank = rank_map.get(obj.id)

            evaluation_period = f"Week {latest_week}, {latest_year}"


        # ------- ALWAYS INITIALIZE PAGINATOR -------
        paginator = PageNumberPagination()
        paginator.page_size = int(request.query_params.get("page_size", 10))
        result_page = paginator.paginate_queryset(qs, request)

        serializer = PerformanceEvaluationSerializer(result_page, many=True)
        employee_list = serializer.data

        # 🔥 Ensure dynamic fields for all weeks (fix missing data for older weeks)
        for idx, obj in enumerate(result_page):
            row = employee_list[idx]

            # Employee Emp ID
            if obj.employee and obj.employee.user:
                row["employee_emp_id"] = obj.employee.user.emp_id
                row["employee_name"] = f"{obj.employee.user.first_name} {obj.employee.user.last_name}".strip()
            else:
                row["employee_emp_id"] = "-"
                row["employee_name"] = "-"

            # Department Name
            row["department_name"] = (
                obj.employee.department.name
                if obj.employee and obj.employee.department
                else "-"
            )

            # Manager Name
            if obj.employee and obj.employee.manager and obj.employee.manager.user:
                mgr = obj.employee.manager.user
                row["manager_name"] = f"{mgr.first_name} {mgr.last_name}".strip()
            else:
                row["manager_name"] = "-"

            # Evaluation Period (Week label)
            start, end = get_week_range(obj.year, obj.week_number)

            row["display_period"] = (
                f"Week {obj.week_number} ({start.strftime('%d %b')} - {end.strftime('%d %b %Y')})"
            )

        # Inject true rank (week_rank)
        for obj, rank in zip(employee_list, result_page):
            obj["rank"] = rank.week_rank

        # ------- Return Paginated Response -------
        return paginator.get_paginated_response({
            "evaluation_period": evaluation_period,
            "records": employee_list,
        })

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
                "trend_data": list(
                    records.values("week_number", "year", "average_score").order_by("year", "week_number")
                ),
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


        # Normalize filters
        week = request.query_params.get("week")
        year = request.query_params.get("year")
        period = request.query_params.get("evaluation_period")

        # Apply week/year if provided
        if week:
            try:
                week = int(week)
            except:
                return Response({"error": "Invalid week number"}, status=400)
            qs = qs.filter(week_number=week)

        if year:
            try:
                year = int(year)
            except:
                return Response({"error": "Invalid year"}, status=400)
            qs = qs.filter(year=year)

        # Apply evaluation_period filter last — only if week/year not used
        if period and not (week or year):
            qs = qs.filter(evaluation_period__icontains=period)



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
            today = date.today()
            current_year, current_week, _ = today.isocalendar()

            evaluations = (
                PerformanceEvaluation.objects
                .exclude(year=current_year, week_number=current_week)
                .select_related("employee__user", "department")
            )
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

            # Weak 3 employees (lowest average scores)
            weak_employees = employee_scores.order_by("avg_score")[:3]
            weak_3_employees = [
                {
                    "emp_id": e["employee__user__emp_id"],
                    "name": f"{e['employee__user__first_name']} {e['employee__user__last_name']}".strip(),
                    "department": e["department__name"],
                    "average_score": round(e["avg_score"], 2),
                }
                for e in weak_employees
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



# ===========================================================
# GET LATEST WEEK + YEAR (For frontend auto-select)
# ===========================================================
class LatestEvaluationWeekAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        emp_id = request.query_params.get("emp_id")

        if not emp_id:
            return Response({"error": "emp_id required"}, status=400)

        try:
            today = date.today()
            current_year, current_week, _ = today.isocalendar()

            # ✅ Always pick the LATEST COMPLETED week, not current or older cached
            latest_record = (
                PerformanceEvaluation.objects
                .filter(employee__user__emp_id=emp_id)
                .exclude(year=current_year, week_number=current_week)
                .order_by("-year", "-week_number")
                .first()
            )

            if not latest_record:
                return Response({"week": None, "year": None}, status=200)

            return Response({
                "week": latest_record.week_number,
                "year": latest_record.year,
            }, status=200)

        except Exception as e:
            return Response({"error": str(e)}, status=500)


class CheckDuplicatePerformanceAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        emp_id = request.query_params.get("emp_id")
        week = request.query_params.get("week")
        year = request.query_params.get("year")
        evaluation_type = request.query_params.get("evaluation_type", "Manager")

        if not emp_id or not week or not year:
            return Response(
                {"error": "emp_id, week and year are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            week = int(week)
            year = int(year)
        except ValueError:
            return Response(
                {"error": "Invalid week/year format"},
                status=400
            )

        try:
            employee = Employee.objects.get(user__emp_id__iexact=emp_id)
        except Employee.DoesNotExist:
            return Response({"error": "Employee not found"}, status=404)

        exists = PerformanceEvaluation.objects.filter(
            employee=employee,
            week_number=week,
            year=year,
            evaluation_type=evaluation_type
        ).exists()

        return Response({
            "exists": exists,
            "message": "Duplicate record exists" if exists else "No duplicate found"
        })
    

class PerformanceByEmployeeWeekAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        emp_id = request.query_params.get("emp_id")
        week = request.query_params.get("week")
        year = request.query_params.get("year")

        try:
            employee = Employee.objects.get(user__emp_id=emp_id)
        except Employee.DoesNotExist:
            return Response({"error": "Employee not found"}, status=404)

        # ✅ If week/year NOT provided, get latest for that employee
        if not week or not year:
            evaluation = PerformanceEvaluation.objects.filter(
                employee=employee
            ).order_by("-year", "-week_number").first()
        else:
            evaluation = PerformanceEvaluation.objects.filter(
                employee=employee,
                week_number=int(week),
                year=int(year)
            ).first()

        if not evaluation:
            return Response({"metrics": []}, status=200)

        serializer = PerformanceEvaluationSerializer(evaluation)
        return Response(serializer.data, status=200)
