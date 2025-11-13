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
    department_name = serializers.SerializerMethodField()
    employee_name = serializers.SerializerMethodField() 
    evaluator_name = serializers.SerializerMethodField()
    employee = SimpleEmployeeSerializer(read_only=True)
    employee_emp_id = serializers.CharField(write_only=True, required=False)
    evaluator = SimpleUserSerializer(read_only=True)
    department = SimpleDepartmentSerializer(read_only=True)

    metrics = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id",
            "employee",
            "employee_emp_id",
            "evaluator",
            "department",
            "evaluation_type",
            "review_date",
            "evaluation_period",
            "metrics",
            "total_score",
            "average_score",
            "rank",
            "remarks",
            "department_name",
            "employee_name",
            "evaluator_name",
        ]

    # ---------- METRICS (Frontend expects simplified keys) ----------
    def get_metrics(self, obj):
        """Send metrics as simple keys for frontend mapping"""
        return {
            "communication_skills": obj.communication_skills,
            "multitasking": obj.multitasking,
            "team_skills": obj.team_skills,
            "technical_skills": obj.technical_skills,
            "job_knowledge": obj.job_knowledge,
            "productivity": obj.productivity,
            "creativity": obj.creativity,
            "work_quality": obj.work_quality,
            "professionalism": obj.professionalism,
            "work_consistency": obj.work_consistency,
            "attitude": obj.attitude,
            "cooperation": obj.cooperation,
            "dependability": obj.dependability,
            "attendance": obj.attendance,
            "punctuality": obj.punctuality,
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


    # ---------- CREATE ----------
    def create(self, validated_data):
        emp_id = validated_data.pop("employee_emp_id", None)
        from employee.models import Employee
        employee = None

        if emp_id:
            try:
                employee = Employee.objects.get(emp_id=emp_id)
                validated_data["employee"] = employee
            except Employee.DoesNotExist:
                raise serializers.ValidationError({"employee_emp_id": f"Employee ID '{emp_id}' not found."})

        # ✅ Skip duplicate check if same employee/week/year/type already exists
        evaluation_type = validated_data.get("evaluation_type")
        review_date = validated_data.get("review_date")

        from datetime import date
        import datetime

        if review_date:
            iso_year, iso_week, _ = review_date.isocalendar()
            validated_data["year"] = iso_year
            validated_data["week_number"] = iso_week

            duplicate = (
                PerformanceEvaluation.objects.filter(
                    employee=employee,
                    year=iso_year,
                    week_number=iso_week,
                    evaluation_type=evaluation_type,
                ).exists()
            )

            if duplicate:
                raise serializers.ValidationError({
                    "duplicate": f"Evaluation already exists for {employee.emp_id} (Week {iso_week}, {iso_year}, {evaluation_type})."
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

        # ✅ Apply metric updates safely (and persist)
        for key, value in metrics_data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)

        # ✅ Update other fields like total_score, remarks, etc.
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    # ---------- REPRESENTATION (Response to Frontend) ----------
    def to_representation(self, instance):
        rep = super().to_representation(instance)

        # Ensure department name always appears
        rep["department_name"] = (
            getattr(instance.department, "name", None)
            if hasattr(instance, "department") and instance.department
            else None
        )

        # Include employee name and emp_id for frontend display
        if instance.employee and hasattr(instance.employee, "user"):
            rep["employee_name"] = f"{instance.employee.user.first_name} {instance.employee.user.last_name}".strip()
            rep["employee_emp_id"] = getattr(instance.employee, "emp_id", None)
        else:
            rep["employee_name"] = getattr(instance.employee, "full_name", None)
            rep["employee_emp_id"] = getattr(instance.employee, "emp_id", None)

        # Include evaluator name if present
        if instance.evaluator:
            rep["evaluator_name"] = f"{instance.evaluator.first_name} {instance.evaluator.last_name}".strip()

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

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id", "employee", "employee_emp_id", "evaluator_emp_id", "department_code",
            "evaluation_type", "review_date", "evaluation_period",
            "communication_skills", "multitasking", "team_skills",
            "technical_skills", "job_knowledge", "productivity", "creativity",
            "work_quality", "professionalism", "work_consistency",
            "attitude", "cooperation", "dependability", "attendance",
            "punctuality", "remarks",
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
            review_date = attrs.get("review_date", timezone.now().date())
            evaluation_type = attrs.get("evaluation_type", "Manager")

            week_number = review_date.isocalendar()[1]
            year = review_date.year

            existing = PerformanceEvaluation.objects.filter(
                employee=emp, week_number=week_number, year=year, evaluation_type=evaluation_type
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
            if request.user.role not in ["Admin", "Manager"]:
                raise serializers.ValidationError({"role": "Only Admin or Manager can submit evaluations."})

        return attrs

    # ---------------------- Create ----------------------
    def create(self, validated_data):
        emp = self.context.get("employee")
        evaluator = self.context.get("evaluator")
        department = self.context.get("department")

        for f in ["employee", "employee_emp_id", "evaluator_emp_id", "department_code"]:
            validated_data.pop(f, None)

        instance = PerformanceEvaluation.objects.create(
            employee=emp,
            evaluator=evaluator,
            department=department,
            **validated_data,
        )

        instance.auto_rank_trigger()
        return instance

    # ---------------------- Update ----------------------
    def update(self, instance, validated_data):
        """
        Update performance metrics and recalculate total/average/rank correctly.
        Accepts metrics sent as a nested 'metrics' object in the request body.
        """
        # 1) Metric field names on the model
        metric_fields = {
            "communication_skills": "communication_skills",
            "multitasking": "multitasking",
            "team_skills": "team_skills",
            "technical_skills": "technical_skills",
            "job_knowledge": "job_knowledge",
            "productivity": "productivity",
            "creativity": "creativity",
            "work_quality": "work_quality",
            "professionalism": "professionalism",
            "work_consistency": "work_consistency",
            "attitude": "attitude",
            "cooperation": "cooperation",
            "dependability": "dependability",
            "attendance": "attendance",
            "punctuality": "punctuality",
        }


        # 2) Apply non-metric validated fields first (like review_date, remarks, etc.)
        for attr, value in validated_data.items():
            # Avoid overwriting nested metrics which we handle separately
            if attr in ("employee", "employee_emp_id", "evaluator_emp_id", "department_code"):
                continue
            setattr(instance, attr, value)

        # 3) Pull metrics from initial_data (frontend payload)
        metrics_data = self.initial_data.get("metrics") or {}

        # If frontend provided top-level metric keys (e.g., communication_skills directly), include them
        # (some clients may send both styles)
        for key in list(validated_data.keys()):
            if key in metric_fields.values():
                metrics_data.setdefault(key, validated_data.get(key))

        # 4) Validate and assign metric values
        for k, v in metrics_data.items():
            # normalize key to the model field name
            model_field = metric_fields.get(k, k)  # fallback to k if key already matches model field
            if model_field not in [f.name for f in instance._meta.fields]:
                # ignore unknown keys
                continue

            # validate numeric
            try:
                num = int(v)
            except (TypeError, ValueError):
                raise serializers.ValidationError({k: "Metric must be an integer between 0 and 100."})

            if not (0 <= num <= 100):
                raise serializers.ValidationError({k: "Metric must be between 0 and 100."})

            setattr(instance, model_field, num)

        # 5) Recalculate totals & averages
        instance.calculate_total_score()

        # 6) Persist and recompute ranks
        instance.save()
        try:
            instance.auto_rank_trigger()
        except Exception:
            # don't block the update if ranking fails for some reason
            pass

        # 7) Optionally refresh from DB to get any DB side updates
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
    score_category = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceEvaluation
        fields = [
            "id", "emp_id", "employee_name", "manager_name",
            "department_name", "review_date", "evaluation_period",
            "evaluation_type", "total_score", "average_score",
            "rank", "score_display", "score_category", "remarks",
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
