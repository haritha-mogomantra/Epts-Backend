# ===========================================================
# employee/serializers.py
# ===========================================================
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction, models
from django.utils import timezone
from .models import Department, Employee
import re, csv, io, os
from datetime import datetime, date
from django.utils import timezone

User = get_user_model()


# ===========================================================
# DEPARTMENT SERIALIZER
# ===========================================================
class DepartmentSerializer(serializers.ModelSerializer):
    employee_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Department
        fields = [
            "id", "code", "name", "description", "is_active",
            "employee_count", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "employee_count"]

    def get_employee_count(self, obj):
        return obj.employees.filter(status="Active").count()

    def validate_name(self, value):
        qs = Department.objects.filter(name__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Department with this name already exists.")
        return value.strip().title()

    def validate_code(self, value):
        if not value:
            return value
        qs = Department.objects.filter(code__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Department code already exists.")
        return value.strip().upper()

    def validate_is_active(self, value):
        if self.instance and not value:
            if Employee.objects.filter(department=self.instance, status="Active").exists():
                raise serializers.ValidationError("Cannot deactivate a department with active employees.")
        return value


# ===========================================================
# USER SUMMARY SERIALIZER
# ===========================================================
class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "emp_id", "first_name", "last_name", "full_name", "email", "role"]

    def get_full_name(self, obj):
        return f"{obj.first_name or ''} {obj.last_name or ''}".strip()


# ===========================================================
# EMPLOYEE SERIALIZER (Read-Only)
# ===========================================================
class EmployeeSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    department = DepartmentSerializer(read_only=True)

    emp_id = serializers.ReadOnlyField(source="user.emp_id")
    full_name = serializers.SerializerMethodField(read_only=True)
    email = serializers.ReadOnlyField(source="user.email")
    role = serializers.ReadOnlyField(source="user.role")
    department_name = serializers.ReadOnlyField(source="department.name")
    department_code = serializers.ReadOnlyField(source="department.code")
    manager_name = serializers.SerializerMethodField(read_only=True)
    team_size = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id", "user", "emp_id", "full_name", "email", "contact_number",
            "department", "department_name", "department_code",
            "role", "manager", "manager_name", "designation",
            "status", "joining_date", "team_size",
            "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_full_name(self, obj):
        return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()

    def get_manager_name(self, obj):
        if obj.manager and hasattr(obj.manager, "user"):
            return f"{obj.manager.user.first_name} {obj.manager.user.last_name}".strip()
        return "Not Assigned"

    def get_team_size(self, obj):
        return Employee.objects.filter(manager=obj).count()


# ===========================================================
# EMPLOYEE CREATE / UPDATE SERIALIZER
# ===========================================================
class EmployeeCreateUpdateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(write_only=True)
    first_name = serializers.CharField(write_only=True)
    last_name = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES, write_only=True)
    department_code = serializers.CharField(write_only=True, required=False)
    manager = serializers.CharField(write_only=True, required=False, allow_blank=True)
    emp_id = serializers.ReadOnlyField(source="user.emp_id")

    class Meta:
        model = Employee
        fields = [
            "id", "email", "emp_id", "first_name", "last_name", "role",
            "contact_number", "department_code", "manager", "designation",
            "status", "joining_date",
        ]

    def validate_first_name(self, value):
        if not value or not re.match(r"^[A-Za-z\s]+$", value.strip()):
            raise serializers.ValidationError("First name must contain only alphabets and spaces.")
        return value.strip().title()

    def validate_last_name(self, value):
        if not value or not re.match(r"^[A-Za-z\s]+$", value.strip()):
            raise serializers.ValidationError("Last name must contain only alphabets and spaces.")
        return value.strip().title()
    
    def validate_dob(self, value):
        today = date.today()

        # Must not be in the future
        if value > today:
            raise serializers.ValidationError("Date of birth cannot be in the future.")

        # Must be at least 18 years old
        min_age_date = today.replace(year=today.year - 18)
        if value > min_age_date:
            raise serializers.ValidationError("Employee must be at least 18 years old.")

        return value

    def validate_joining_date(self, value):
        today = date.today()

        # Cannot join in the future
        if value > today:
            raise serializers.ValidationError("Joining date cannot be in the future.")

        # Compare with DOB if available
        dob = self.initial_data.get("dob") or getattr(self.instance, "dob", None)
        if dob:
            if isinstance(dob, str):
                dob = date.fromisoformat(dob)

            if value <= dob:
                raise serializers.ValidationError("Joining date must be after the date of birth.")

        return value

    def validate_contact_number(self, value):
        if not value:
            return value
        pattern = r"^\+91[6-9]\d{9}$"
        if not re.match(pattern, value):
            raise serializers.ValidationError("Contact number must start with +91 and be valid.")
        qs = Employee.objects.filter(contact_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This contact number is already used.")
        return value
    

    def validate(self, attrs):
        # department_code must be present for creation
        if self.instance is None and not attrs.get("department_code"):
            raise serializers.ValidationError({"department_code": "Department code is required."})

        # Ensure contact_number is a string (not int) if provided
        contact = attrs.get("contact_number")
        if contact is not None and not isinstance(contact, str):
            raise serializers.ValidationError({"contact_number": "Contact number must be a string."})

        # Status must be a valid choice string
        status_val = attrs.get("status")
        if status_val is not None and status_val not in ["Active", "Inactive", "On Leave"]:
            raise serializers.ValidationError({"status": "Invalid status. Use: Active / Inactive / On Leave."})

        return attrs


    @transaction.atomic
    def create(self, validated_data):
        dept_code = validated_data.pop("department_code", None)
        manager_emp_id = validated_data.pop("manager", None)
        email = validated_data.pop("email")
        first_name = validated_data.pop("first_name")
        last_name = validated_data.pop("last_name")
        role = validated_data.pop("role")

        # Validate role explicitly
        if role not in ["Admin", "Manager", "Employee"]:
            raise serializers.ValidationError({"role": f"Invalid role '{role}'."})

        # Department is required for Employee creation
        if not dept_code:
            raise serializers.ValidationError({"department_code": "Department code is required."})

        department = Department.objects.filter(
            models.Q(code__iexact=dept_code) | models.Q(name__iexact=dept_code) | models.Q(id__iexact=dept_code)
        ).first()
        if not department:
            raise serializers.ValidationError({"department_code": f"Department '{dept_code}' not found."})
        if not department.is_active:
            raise serializers.ValidationError({"department_code": f"Department '{dept_code}' is inactive."})

        # Manager validation
        manager = None
        if manager_emp_id:
            manager = Employee.objects.filter(user__emp_id__iexact=manager_emp_id).first()
            if not manager:
                raise serializers.ValidationError({"manager": f"Manager '{manager_emp_id}' not found."})
            mgr_role = getattr(manager.user, "role", None)
            if mgr_role not in ["Manager", "Admin"]:
                raise serializers.ValidationError({"manager": "Assigned manager must have role 'Manager' or 'Admin'."})

        # Email uniqueness
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": "User with this email already exists."})

        # Create user and employee
        user = User.objects.create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
            department=department,
        )

        employee = Employee.objects.create(
            user=user,
            department=department,
            manager=manager,
            role=role,
            **validated_data,
        )
        return employee
    

    @transaction.atomic
    def update(self, instance, validated_data):
        department_code = validated_data.pop("department_code", None)
        manager_emp_id = validated_data.pop("manager", None)
        role = validated_data.get("role", instance.role)

        # Update Department
        if department_code:
            department = Department.objects.filter(
                models.Q(code__iexact=department_code) | models.Q(name__iexact=department_code)
            ).first()
            if not department:
                raise serializers.ValidationError({"department_code": f"Department '{department_code}' not found."})
            instance.department = department

        # Update Manager
        if manager_emp_id:
            manager = Employee.objects.filter(user__emp_id__iexact=manager_emp_id).first()
            if not manager:
                raise serializers.ValidationError({"manager": f"Manager '{manager_emp_id}' not found."})
            if manager.user.role not in ["Manager", "Admin"]:
                raise serializers.ValidationError({"manager": "Assigned manager must be Manager/Admin."})
            instance.manager = manager

        # Update User fields (first_name, last_name, email if present)
        user = instance.user
        if "first_name" in validated_data:
            user.first_name = validated_data.pop("first_name")
        if "last_name" in validated_data:
            user.last_name = validated_data.pop("last_name")
        if "email" in validated_data:
            user.email = validated_data.pop("email")
        user.role = role  # keep role synced
        user.save()

        # Update Employee fields
        instance.role = role
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance


# ===========================================================
# COMMON IMAGE VALIDATION
# ===========================================================
def validate_image_file(value):
    if value:
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png"]:
            raise serializers.ValidationError("Only JPG and PNG images are allowed.")
        if value.size > 2 * 1024 * 1024:  # 2MB limit
            raise serializers.ValidationError("Profile picture size must not exceed 2MB.")
    return value


# ===========================================================
# ADMIN PROFILE SERIALIZER
# ===========================================================
class AdminProfileSerializer(serializers.ModelSerializer):
    emp_id = serializers.CharField(source="user.emp_id", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    department = serializers.CharField(source="department.name", read_only=True)
    department_code = serializers.ReadOnlyField(source="department.code")
    role = serializers.CharField(read_only=True)
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "emp_id", "first_name", "last_name", "email", "role",
            "department", "department_code", "designation", "joining_date", "status",
            "contact_number", "dob", "profile_picture", "profile_picture_url",
            "address_line1", "city", "state", "pincode",
        ]
        read_only_fields = ["emp_id", "department", "role", "status"]

    def get_profile_picture_url(self, obj):
        request = self.context.get("request")
        if obj.profile_picture and hasattr(obj.profile_picture, "url"):
            return request.build_absolute_uri(obj.profile_picture.url) if request else obj.profile_picture.url
        return None

    def validate_profile_picture(self, value):
        return validate_image_file(value)

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        user = instance.user
        for field, value in user_data.items():
            setattr(user, field, value)
        user.save()

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


# ===========================================================
# MANAGER PROFILE SERIALIZER
# ===========================================================
class ManagerProfileSerializer(serializers.ModelSerializer):
    emp_id = serializers.CharField(source="user.emp_id", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    department = serializers.CharField(source="department.name", read_only=True)
    department_code = serializers.ReadOnlyField(source="department.code")
    role = serializers.CharField(read_only=True)
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "emp_id", "first_name", "last_name", "email", "role", "gender",
            "department", "department_code", "designation", "joining_date", "status",
            "contact_number", "dob", "profile_picture", "profile_picture_url",
            "address_line1", "address_line2", "city", "state", "pincode",
        ]
        read_only_fields = ["emp_id", "department", "role", "status"]

    def get_profile_picture_url(self, obj):
        request = self.context.get("request")
        if obj.profile_picture and hasattr(obj.profile_picture, "url"):
            return request.build_absolute_uri(obj.profile_picture.url) if request else obj.profile_picture.url
        return None

    def validate_profile_picture(self, value):
        return validate_image_file(value)

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        user = instance.user
        for field, value in user_data.items():
            setattr(user, field, value)
        user.save()

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


# ===========================================================
# EMPLOYEE PROFILE SERIALIZER
# ===========================================================
class EmployeeProfileSerializer(serializers.ModelSerializer):
    emp_id = serializers.CharField(source="user.emp_id", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    department = serializers.CharField(source="department.name", read_only=True)
    department_code = serializers.ReadOnlyField(source="department.code")
    role = serializers.CharField(read_only=True)
    manager_name = serializers.SerializerMethodField()
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "emp_id", "first_name", "last_name", "email", "gender",
            "contact_number", "dob", "profile_picture", "profile_picture_url",
            "role", "department", "department_code", "designation", "project_name",
            "joining_date", "reporting_manager_name", "manager_name", "status",
            "address_line1", "address_line2", "city", "state", "pincode",
        ]
        read_only_fields = ["emp_id", "department", "department_code", "role", "status", "manager_name"]

    def get_manager_name(self, obj):
        if obj.manager and hasattr(obj.manager, "user"):
            return f"{obj.manager.user.first_name} {obj.manager.user.last_name}".strip()
        return obj.reporting_manager_name or "Not Assigned"

    def get_profile_picture_url(self, obj):
        request = self.context.get("request")
        if obj.profile_picture and hasattr(obj.profile_picture, "url"):
            return request.build_absolute_uri(obj.profile_picture.url) if request else obj.profile_picture.url
        return None

    def validate_profile_picture(self, value):
        return validate_image_file(value)

    @transaction.atomic
    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        for attr, val in user_data.items():
            setattr(instance.user, attr, val)
        instance.user.save()

        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save()
        return instance


# ===========================================================
# EMPLOYEE BULK CSV UPLOAD SERIALIZER
# ===========================================================
class EmployeeCSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        if not value.name.endswith(".csv"):
            raise serializers.ValidationError("Only CSV files are allowed.")
        return value

    def create(self, validated_data):
        file = validated_data["file"]
        decoded_file = file.read().decode("utf-8")
        io_string = io.StringIO(decoded_file)
        reader = csv.DictReader(io_string)

        required_cols = ["Emp Id", "First Name", "Last Name", "Email", "Dept Code", "Role", "Joining Date"]
        if not all(col in reader.fieldnames for col in required_cols):
            raise serializers.ValidationError({"error": f"CSV must contain: {', '.join(required_cols)}"})

        success_count, errors = 0, []

        with transaction.atomic():
            for i, row in enumerate(reader, start=2):
                emp_id = row.get("Emp Id", "").strip()
                email = row.get("Email", "").strip().lower()
                dept_code = row.get("Dept Code", "").strip()
                first_name = row.get("First Name", "").strip()
                last_name = row.get("Last Name", "").strip()
                role = row.get("Role", "").strip().capitalize()
                joining_date = row.get("Joining Date", "").strip()

                if not (emp_id and email and dept_code and role):
                    errors.append(f"Row {i}: Missing mandatory fields.")
                    continue

                if Employee.objects.filter(user__emp_id__iexact=emp_id).exists():
                    errors.append(f"Row {i}: Employee ID '{emp_id}' already exists.")
                    continue

                if User.objects.filter(email__iexact=email).exists():
                    errors.append(f"Row {i}: Email '{email}' already exists.")
                    continue

                department = Department.objects.filter(code__iexact=dept_code).first()
                if not department:
                    errors.append(f"Row {i}: Department '{dept_code}' not found.")
                    continue

                if role not in ["Admin", "Manager", "Employee"]:
                    errors.append(f"Row {i}: Invalid role '{role}'.")
                    continue

                try:
                    user = User.objects.create_user(
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        role=role,
                        department=department,
                    )
                    user.emp_id = emp_id
                    user.set_password("Default@123")
                    user.save()

                    join_date = None
                    if joining_date:
                        try:
                            join_date = datetime.strptime(joining_date, "%Y-%m-%d").date()
                        except ValueError:
                            pass

                    Employee.objects.create(
                        user=user,
                        department=department,
                        joining_date=join_date or None,
                        status="Active",
                    )
                    success_count += 1

                except Exception as e:
                    errors.append(f"Row {i}: {str(e)}")

        return {"success_count": success_count, "errors": errors}
