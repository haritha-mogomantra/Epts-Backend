# ===========================================================
# performance/serializers.py 
# ===========================================================
from rest_framework import serializers
from django.utils import timezone
from django.contrib.auth import get_user_model
from .models import PerformanceEvaluation
from employee.models import Department, Employee

User = get_user_model()


# ===========================================================
# SIMPLE / RELATED SERIALIZERS
# ===========================================================
class SimpleUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "emp_id", "first_name", "last_name", "full_name", "email", "role"]

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.last_name or ''}".strip()


class SimpleDepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "code"]


class SimpleEmployeeSerializer(serializers.ModelSerializer):
    user = SimpleUserSerializer(read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    full_name = serializers.SerializerMethodField(read_only=True)
    manager_name = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id", "user", "designation", "status", "role",
            "department_name", "full_name", "manager_name",
        ]

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip()

    def get_manager_name(self, obj):
        if obj.manager and obj.manager.user:
            mgr = obj.manager.user
            return f"{mgr.first_name} {mgr.last_name}".strip()
        return "-"


# ===========================================================
# READ-ONLY SERIALIZER (List / Detail)
# ===========================================================
class PerformanceEvaluationSerializer(serializers.ModelSerializer):
    employee_summary = serializers.SerializerMethodField()
    week = serializers.IntegerField(source="week_number", read_only=True)
    department_name = serializers.SerializerMethodField()
    employee_name = serializers.SerializerMethodField() 
    evaluator_name = serializers.SerializerMethodField()
    employee = SimpleEmployeeSerializer(read_only=True)
    employee_emp_id = serializers.CharField(write_only=True, required=False)
    evaluator = SimpleUserSerializer(read_only=True)
    department = SimpleDepartmentSerializer(read_only=True)


    metrics = serializers.SerializerMethodField()

    week_start = serializers.SerializerMethodField()
    week_end = serializers.SerializerMethodField()
    week_label = serializers.SerializerMethodField()
    display_period = serializers.SerializerMethodField()


    def get_employee_summary(self, obj):
        emp = obj.employee
        u = emp.user

        return {
            "emp_id": u.emp_id,
            "full_name": f"{u.first_name} {u.last_name}".strip(),
            "department_name": emp.department.name if emp.department else None,
            "manager_name": (
                f"{emp.manager.user.first_name} {emp.manager.user.last_name}".strip()
                if emp.manager and emp.manager.user else "-"
            )
        }


    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id",
            "employee",
            "employee_summary",
            "employee_emp_id",
            "evaluator",
            "department",
            "evaluation_type",
            "review_date",
            "evaluation_period",
            "week_number",
            "week",
            "year",
            "metrics",
            "total_score",
            "average_score",
            "rank",
            "remarks",
            "department_name",
            "employee_name",
            "evaluator_name",
            "week_start",
            "week_end",
            "week_label",
            "display_period",
        ]

    # ---------- METRICS (Frontend expects simplified keys) ----------
    def get_metrics(self, obj):
        return {
            "communication_skills": obj.communication_skills,
            "communication_skills_comment": obj.communication_skills_comment,

            "multitasking": obj.multitasking,
            "multitasking_comment": obj.multitasking_comment,

            "team_skills": obj.team_skills,
            "team_skills_comment": obj.team_skills_comment,

            "technical_skills": obj.technical_skills,
            "technical_skills_comment": obj.technical_skills_comment,

            "job_knowledge": obj.job_knowledge,
            "job_knowledge_comment": obj.job_knowledge_comment,

            "productivity": obj.productivity,
            "productivity_comment": obj.productivity_comment,

            "creativity": obj.creativity,
            "creativity_comment": obj.creativity_comment,

            "work_quality": obj.work_quality,
            "work_quality_comment": obj.work_quality_comment,

            "professionalism": obj.professionalism,
            "professionalism_comment": obj.professionalism_comment,

            "work_consistency": obj.work_consistency,
            "work_consistency_comment": obj.work_consistency_comment,

            "attitude": obj.attitude,
            "attitude_comment": obj.attitude_comment,

            "cooperation": obj.cooperation,
            "cooperation_comment": obj.cooperation_comment,

            "dependability": obj.dependability,
            "dependability_comment": obj.dependability_comment,

            "attendance": obj.attendance,
            "attendance_comment": obj.attendance_comment,

            "punctuality": obj.punctuality,
            "punctuality_comment": obj.punctuality_comment,
        }


    # ---------- CUSTOM FIELDS (department, employee, evaluator) ----------
    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def get_employee_name(self, obj):
        if obj.employee and obj.employee.user:
            return f"{obj.employee.user.first_name} {obj.employee.user.last_name}".strip()
        return None

    def get_evaluator_name(self, obj):
        if obj.evaluator:
            return f"{obj.evaluator.first_name} {obj.evaluator.last_name}".strip()
        return None
    
        # ---------- WEEK RANGE CALCULATIONS ----------
    def get_week_start(self, obj):
        from datetime import date
        try:
            return date.fromisocalendar(obj.year, obj.week_number, 1)
        except:
            return None

    def get_week_end(self, obj):
        from datetime import date, timedelta
        try:
            start = date.fromisocalendar(obj.year, obj.week_number, 1)
            return start + timedelta(days=6)
        except:
            return None

    def get_week_label(self, obj):
        return f"Week {obj.week_number}"

    def get_display_period(self, obj):
        # Returns: "Week 47 (18 Nov - 24 Nov 2025)"
        try:
            start = self.get_week_start(obj)
            end = self.get_week_end(obj)
            if not start or not end:
                return None
            return f"Week {obj.week_number} ({start.strftime('%d %b')} - {end.strftime('%d %b %Y')})"
        except:
            return None



    # ---------- CREATE ----------
    def create(self, validated_data):
        emp_id = validated_data.pop("employee_emp_id", None)

        from employee.models import Employee
        employee = None

        if emp_id:
            try:
                employee = Employee.objects.get(emp_id=emp_id)
            except Employee.DoesNotExist:
                raise serializers.ValidationError({"employee_emp_id": f"Employee ID '{emp_id}' not found."})

        validated_data["employee"] = employee

        week = validated_data.get("week_number")
        year = validated_data.get("year")
        evaluation_type = validated_data.get("evaluation_type")

        # REAL duplicate check (correct)
        if PerformanceEvaluation.objects.filter(
            employee=employee,
            week_number=week,
            year=year,
            evaluation_type=evaluation_type,
        ).exists():
            raise serializers.ValidationError({
                "duplicate": f"Evaluation already exists for {employee.emp_id} (Week {week}, {year}, {evaluation_type})."
            })

        return super().create(validated_data)

    # ---------- UPDATE ----------
    def update(self, instance, validated_data):
        emp_id = validated_data.pop("employee_emp_id", None)
        metrics_data = self.initial_data.get("metrics", {})

        if emp_id:
            from employee.models import Employee
            try:
                validated_data["employee"] = Employee.objects.get(emp_id=emp_id)
            except Employee.DoesNotExist:
                raise serializers.ValidationError({"employee_emp_id": f"Employee ID '{emp_id}' not found."})

        # Apply metric updates safely (and persist)
        for key, value in metrics_data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)

        # Update other fields like total_score, remarks, etc.
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    def to_representation(self, instance):
        rep = super().to_representation(instance)

        emp = instance.employee
        user = emp.user if emp else None

        # ALWAYS fetch fresh dynamic employee details
        rep["employee_emp_id"] = user.emp_id if user else None

        rep["employee_name"] = (
            f"{user.first_name} {user.last_name}".strip()
            if user else None
        )

        rep["department_name"] = (
            emp.department.name if emp and emp.department else None
        )

        # Manager fetch (fully dynamic)
        if emp and emp.manager and emp.manager.user:
            mgr = emp.manager.user
            rep["manager_name"] = f"{mgr.first_name} {mgr.last_name}".strip()
        else:
            rep["manager_name"] = "-"

        # Evaluator name
        if instance.evaluator:
            ev = instance.evaluator
            rep["evaluator_name"] = f"{ev.first_name} {ev.last_name}".strip()

        # Week / Year consistency
        rep["week"] = instance.week_number
        rep["year"] = instance.year

        return rep

# ===========================================================
# CREATE / UPDATE SERIALIZER (Fixed for "employee": "EMPxxxx" input)
# ===========================================================
class PerformanceCreateUpdateSerializer(serializers.ModelSerializer):
    # Accept both 'employee' and 'employee_emp_id' inputs
    employee = serializers.CharField(write_only=True, required=False)
    employee_emp_id = serializers.CharField(write_only=True, required=False)
    evaluator_emp_id = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    department_code = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    week = serializers.IntegerField(write_only=True, required=False)
    year = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id", "employee", "employee_emp_id", "evaluator_emp_id", "department_code",
            "evaluation_type", "review_date", "evaluation_period",
            "week_number", "year", "week",

            # Metrics
            "communication_skills", "multitasking", "team_skills",
            "technical_skills", "job_knowledge", "productivity", "creativity",
            "work_quality", "professionalism", "work_consistency",
            "attitude", "cooperation", "dependability", "attendance",
            "punctuality",

            # COMMENT FIELDS
            "communication_skills_comment", "multitasking_comment", "team_skills_comment",
            "technical_skills_comment", "job_knowledge_comment", "productivity_comment",
            "creativity_comment", "work_quality_comment", "professionalism_comment",
            "work_consistency_comment", "attitude_comment", "cooperation_comment",
            "dependability_comment", "attendance_comment", "punctuality_comment",

            "remarks",
        ]

    # ---------------------- Validations ----------------------
    def validate(self, attrs):
        # Accept employee via either "employee" or "employee_emp_id"
        emp_value = attrs.get("employee") or attrs.get("employee_emp_id")
        if not emp_value:
            raise serializers.ValidationError({"employee": "Employee ID is required."})

        try:
            emp = Employee.objects.select_related("user", "department").get(user__emp_id__iexact=emp_value)
        except Employee.DoesNotExist:
            raise serializers.ValidationError({"employee": f"Employee with emp_id '{emp_value}' not found."})
        self.context["employee"] = emp

        # Evaluator handling
        evaluator_value = attrs.get("evaluator_emp_id")
        if evaluator_value:
            try:
                evaluator = User.objects.get(emp_id__iexact=evaluator_value)
            except User.DoesNotExist:
                raise serializers.ValidationError({"evaluator_emp_id": f"Evaluator '{evaluator_value}' not found."})
            self.context["evaluator"] = evaluator
        else:
            request = self.context.get("request")
            if request and hasattr(request, "user"):
                self.context["evaluator"] = request.user

        # Department handling
        dept_code = attrs.get("department_code")
        if dept_code:
            try:
                dept = Department.objects.get(code__iexact=dept_code, is_active=True)
            except Department.DoesNotExist:
                raise serializers.ValidationError({"department_code": f"Department '{dept_code}' not found or inactive."})
            self.context["department"] = dept
        else:
            self.context["department"] = emp.department

        # Prevent duplicate evaluations only when creating new record
        if not self.instance:
            week_number = (
                attrs.get("week_number")
                or attrs.get("week")
                or self.initial_data.get("week")
            )

            year = (
                attrs.get("year")
                or self.initial_data.get("year")
            )

            try:
                week_number = int(week_number)
                year = int(year)
            except:
                raise serializers.ValidationError({"week": "Valid week and year are required."})

            attrs["week_number"] = week_number
            attrs["year"] = year

            # Remove week
            attrs.pop("week", None)



            evaluation_type = attrs.get("evaluation_type", "Manager")

            existing = PerformanceEvaluation.objects.filter(
                employee=emp,
                week_number=week_number,
                year=year,
                evaluation_type=evaluation_type
            )


            if existing.exists():
                raise serializers.ValidationError({
                    "duplicate": f"Evaluation already exists for {emp.user.emp_id} (Week {week_number}, {year}, {evaluation_type})."
                })

        # Metric validation — ensure 0–100 integer values
        for field, value in attrs.items():
            if field.endswith("_skills") or field in [
                "job_knowledge", "productivity", "creativity",
                "work_quality", "professionalism", "work_consistency",
                "attitude", "cooperation", "dependability", "attendance", "punctuality"
            ]:
                try:
                    num = int(value)
                except (TypeError, ValueError):
                    raise serializers.ValidationError({field: "Metric must be an integer between 0–100."})
                if not (0 <= num <= 100):
                    raise serializers.ValidationError({field: "Metric must be between 0–100."})

        # Role restriction: Only Admin or Manager can evaluate
        request = self.context.get("request")
        if request and hasattr(request.user, "role"):
            user_role = str(request.user.role).lower()
            if user_role not in ["admin", "manager"]:
                raise serializers.ValidationError(
                    {"error": "Only Admin or Manager can submit evaluations."}
                )


        if not self.instance:
            attrs["week_number"] = week_number
            attrs["year"] = year
            
        return attrs

    # ---------------------- Create ----------------------
    def create(self, validated_data):
        emp = self.context.get("employee")
        evaluator = self.context.get("evaluator")
        department = self.context.get("department")

        for f in ["employee", "employee_emp_id", "evaluator_emp_id", "department_code", "week"]:
            validated_data.pop(f, None)


        # -----------------------------
        # MERGE TOP-LEVEL METRIC FIELDS
        # -----------------------------
        metric_fields = {
            "communication_skills", "multitasking", "team_skills", "technical_skills",
            "job_knowledge", "productivity", "creativity", "work_quality",
            "professionalism", "work_consistency", "attitude", "cooperation",
            "dependability", "attendance", "punctuality"
        }

        # MERGE flat fields
        for k in metric_fields:
            if k in self.initial_data:
                validated_data[k] = int(self.initial_data.get(k, 0))

        # MERGE nested metrics object 
        metrics = self.initial_data.get("metrics") or {}
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                if k in metric_fields:
                    validated_data[k] = int(v)

        # -----------------------------
        # Create instance with metrics
        # -----------------------------
        instance = PerformanceEvaluation.objects.create(
            employee=emp,
            evaluator=evaluator,
            department=department,
            **validated_data,
        )

        # -----------------------------
        # MERGE COMMENT FIELDS
        # -----------------------------
        comment_fields = [
            "communication_skills_comment", "multitasking_comment", "team_skills_comment",
            "technical_skills_comment", "job_knowledge_comment", "productivity_comment",
            "creativity_comment", "work_quality_comment", "professionalism_comment",
            "work_consistency_comment", "attitude_comment", "cooperation_comment",
            "dependability_comment", "attendance_comment", "punctuality_comment"
        ]

        # From nested metrics object
        metrics_comments = self.initial_data.get("metrics", {})
        for field in comment_fields:
            if field in metrics_comments:
                setattr(instance, field, metrics_comments[field])

        # From flat POST level (optional)
        for field in comment_fields:
            if field in self.initial_data:
                setattr(instance, field, self.initial_data[field])


        # -----------------------------
        # RECALCULATE TOTAL & AVG
        # -----------------------------
        instance.calculate_total_score()
        instance.save()

        # -----------------------------
        # UPDATE RANK
        # -----------------------------
        try:
            instance.auto_rank_trigger()
        except:
            pass

        instance.refresh_from_db()
        return instance

    # ---------------------- Update ----------------------
    def update(self, instance, validated_data):
        """
        Update metrics + other fields, recalc score, recalc rank,
        and always return fresh updated values.
        """

        validated_data.pop("week", None)

        # ---------------------------------------
        # Apply non-metric fields
        # ---------------------------------------
        for attr, value in validated_data.items():
            if attr in ["employee", "employee_emp_id", "evaluator_emp_id", "department_code"]:
                continue
            setattr(instance, attr, value)

        # ---------------------------------------
        # Merge all metric fields (top-level + nested)
        # ---------------------------------------
        metric_fields = {
            "communication_skills", "multitasking", "team_skills", "technical_skills",
            "job_knowledge", "productivity", "creativity", "work_quality",
            "professionalism", "work_consistency", "attitude", "cooperation",
            "dependability", "attendance", "punctuality"
        }

        # TOP-LEVEL metrics 
        for key in metric_fields:
            if key in self.initial_data:
                setattr(instance, key, int(self.initial_data.get(key, 0)))

        # Nested metrics object (metrics={...})
        metrics_obj = self.initial_data.get("metrics") or {}
        if isinstance(metrics_obj, dict):
            for key, value in metrics_obj.items():
                if key in metric_fields:
                    setattr(instance, key, int(value))

        
        # -----------------------------
        # MERGE COMMENT FIELDS
        # -----------------------------
        comment_fields = [
            "communication_skills_comment", "multitasking_comment", "team_skills_comment",
            "technical_skills_comment", "job_knowledge_comment", "productivity_comment",
            "creativity_comment", "work_quality_comment", "professionalism_comment",
            "work_consistency_comment", "attitude_comment", "cooperation_comment",
            "dependability_comment", "attendance_comment", "punctuality_comment"
        ]

        # From nested metrics object
        metrics_comments = self.initial_data.get("metrics", {})
        for field in comment_fields:
            if field in metrics_comments:
                setattr(instance, field, metrics_comments[field])

        # From flat POST level (optional)
        for field in comment_fields:
            if field in self.initial_data:
                setattr(instance, field, self.initial_data[field])


        # ---------------------------------------
        # Recalculate totals
        # ---------------------------------------
        instance.calculate_total_score()
        instance.save()

        # ---------------------------------------
        # Update Rank
        # ---------------------------------------
        try:
            instance.auto_rank_trigger()
        except:
            pass

        instance.refresh_from_db()

        return instance


# ===========================================================
# DASHBOARD / RANKING SERIALIZERS
# ===========================================================
class PerformanceDashboardSerializer(serializers.ModelSerializer):
    emp_id = serializers.ReadOnlyField(source="employee.user.emp_id")
    employee_name = serializers.SerializerMethodField()
    manager_name = serializers.SerializerMethodField()
    department_name = serializers.ReadOnlyField(source="department.name")
    score_display = serializers.SerializerMethodField()
    

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id", "emp_id", "employee_name", "manager_name",
            "department_name", "review_date", "evaluation_period",
            "evaluation_type", "total_score", "average_score",
            "rank", "score_display", "remarks",
        ]

    def get_employee_name(self, obj):
        u = obj.employee.user
        return f"{u.first_name} {u.last_name}".strip()

    def get_manager_name(self, obj):
        if obj.employee.manager and obj.employee.manager.user:
            m = obj.employee.manager.user
            return f"{m.first_name} {m.last_name}".strip()
        return "-"

    def get_score_display(self, obj):
        return f"{obj.total_score} / 1500"


class PerformanceRankSerializer(serializers.ModelSerializer):
    emp_id = serializers.ReadOnlyField(source="employee.user.emp_id")
    full_name = serializers.SerializerMethodField()
    department_name = serializers.ReadOnlyField(source="department.name")
    score_display = serializers.SerializerMethodField()
    

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "emp_id", "full_name", "department_name", "total_score",
            "average_score", "rank", "score_display"
        ]

    def get_full_name(self, obj):
        u = obj.employee.user
        return f"{u.first_name} {u.last_name}".strip()

    def get_score_display(self, obj):
        return f"{obj.total_score} / 1500"
