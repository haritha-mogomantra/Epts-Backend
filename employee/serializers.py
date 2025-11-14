# ===========================================================
# employee/serializers.py
# ===========================================================
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction, models
from django.db.models import Q
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
# EMPLOYEE SERIALIZER (Read + Write)
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
    manager = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id", "user", "emp_id", "full_name", "email", "contact_number",
            "department", "department_name", "department_code",
            "role", "manager", "manager_name", "designation",
            "project_name",
            "status", "joining_date", "status", "team_size",
            "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    # ===========================================================
    # READ HELPERS
    # ===========================================================
    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip()

    def get_manager_name(self, obj):
        if obj.manager and hasattr(obj.manager, "user"):
            return f"{obj.manager.user.first_name} {obj.manager.user.last_name}".strip()
        return "Not Assigned"

    def get_team_size(self, obj):
        return Employee.objects.filter(manager=obj, is_deleted=False).count()
    
    def get_status(self, obj):
        return "Inactive" if obj.is_deleted else (obj.status or "Active")

    # ===========================================================
    # WRITE VALIDATION (accepts emp_id or full name)
    # ===========================================================
    def validate_manager(self, value):

        if not value:
            return None

        # ✅ Try lookup by emp_id first
        manager = Employee.objects.filter(user__emp_id__iexact=value, is_deleted=False).first()
        if manager:
            return manager

        # ✅ Try flexible name-based search (handles 2–3 part names)
        name = value.strip()
        name_parts = name.split()

        if len(name_parts) >= 2:
            first_part = name_parts[0]
            last_part = name_parts[-1]

            manager = Employee.objects.filter(
                Q(user__first_name__icontains=first_part) &
                Q(user__last_name__icontains=last_part),
                is_deleted=False
            ).first()
        else:
            # Fallback: match any name part (first or last)
            manager = Employee.objects.filter(
                Q(user__first_name__icontains=name) |
                Q(user__last_name__icontains=name),
                is_deleted=False
            ).first()

        if manager:
            return manager

        raise serializers.ValidationError(f"Manager '{value}' not found.")

    # ===========================================================
    # OVERRIDE CREATE & UPDATE (to support manager resolution)
    # ===========================================================
    def create(self, validated_data):
        manager = validated_data.pop("manager", None)
        if manager and isinstance(manager, str):
            validated_data["manager"] = self.validate_manager(manager)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        manager = validated_data.pop("manager", None)
        if manager and isinstance(manager, str):
            validated_data["manager"] = self.validate_manager(manager)
        return super().update(instance, validated_data)
    
    def to_representation(self, instance):
        data = super().to_representation(instance)

        # If soft-deleted, force status to Inactive
        if instance.is_deleted:
            data["status"] = "Inactive"

        return data



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

    # ✅ Allow multiple joining_date input formats (handles all business cases)
    joining_date = serializers.DateField(
        input_formats=["%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"],
        required=True
    )

    class Meta:
        model = Employee
        fields = [
            "id", "email", "emp_id", "first_name", "last_name", "role",
            "contact_number", "department_code", "manager", "designation", "project_name",
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

        if value > today:
            raise serializers.ValidationError("Date of birth cannot be in the future.")

        min_age_date = today.replace(year=today.year - 18)
        if value > min_age_date:
            raise serializers.ValidationError("Employee must be at least 18 years old.")

        return value

    def validate_joining_date(self, value):
        today = date.today()

        if value > today:
            raise serializers.ValidationError("Joining date cannot be in the future.")

        dob = self.initial_data.get("dob") or getattr(self.instance, "dob", None)
        if dob:
            if isinstance(dob, str):
                try:
                    dob = datetime.strptime(dob, "%Y-%m-%d").date()
                except ValueError:
                    try:
                        dob = datetime.strptime(dob, "%d-%m-%Y").date()
                    except ValueError:
                        raise serializers.ValidationError("Date of birth must be valid (YYYY-MM-DD or DD-MM-YYYY).")

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
        mandatory_fields = ["first_name", "last_name", "email", "role", "department_code", "joining_date"]
        missing = [f for f in mandatory_fields if not attrs.get(f)]
        if missing:
            raise serializers.ValidationError({
                "error": f"Missing mandatory fields: {', '.join(missing)}"
            })

        dept_code = attrs.get("department_code")
        department = None
        if dept_code:
            department = Department.objects.filter(
                models.Q(code__iexact=dept_code) |
                models.Q(name__iexact=dept_code) |
                models.Q(id__iexact=dept_code)
            ).first()
            if not department:
                raise serializers.ValidationError({
                    "department_code": f"Department '{dept_code}' not found."
                })
            if not department.is_active:
                raise serializers.ValidationError({
                    "department_code": f"Department '{department.name}' is inactive."
                })
        if self.instance is None and not attrs.get("department_code"):
            raise serializers.ValidationError({"department_code": "Department code is required for new employees."})

        email = attrs.get("email")
        if email and User.objects.filter(email__iexact=email).exclude(id=getattr(self.instance, "user_id", None)).exists():
            raise serializers.ValidationError({
                "email": f"User with email '{email}' already exists."
            })

        first_name = (attrs.get("first_name") or "").strip()
        last_name = (attrs.get("last_name") or "").strip()

        if department and Employee.objects.filter(
            user__first_name__iexact=first_name,
            user__last_name__iexact=last_name,
            department=department,
            is_deleted=False
        ).exclude(id=getattr(self.instance, "id", None)).exists():
            raise serializers.ValidationError({
                "error": f"Employee '{first_name} {last_name}' already exists in department '{department.name}'."
            })

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        dept_code = validated_data.pop("department_code", None)
        manager_emp_id = validated_data.pop("manager", None)
        email = validated_data.pop("email")
        first_name = validated_data.pop("first_name").strip().title()
        last_name = validated_data.pop("last_name").strip().title()
        role = validated_data.pop("role")

        if role not in ["Admin", "Manager", "Employee"]:
            raise serializers.ValidationError({"role": f"Invalid role '{role}'."})

        if not dept_code:
            raise serializers.ValidationError({"department_code": "Department code is required."})

        department = Department.objects.filter(
            models.Q(code__iexact=dept_code) | models.Q(name__iexact=dept_code) | models.Q(id__iexact=dept_code)
        ).first()
        if not department:
            raise serializers.ValidationError({"department_code": f"Department '{dept_code}' not found."})
        if not department.is_active:
            raise serializers.ValidationError({"department_code": f"Department '{department.name}' is inactive."})

        if manager_emp_id in ["", None, "None", "null"]:
            manager_emp_id = None

        manager = None
        if manager_emp_id and manager_emp_id.strip():
            manager = Employee.objects.filter(user__emp_id__iexact=manager_emp_id).first()
            if not manager or not getattr(manager.user, "role", None) in ["Manager", "Admin"]:
                raise serializers.ValidationError({"manager": "Assigned manager must have role 'Manager' or 'Admin'."})

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

        if department_code:
            department = Department.objects.filter(
                models.Q(code__iexact=department_code) | models.Q(name__iexact=department_code)
            ).first()
            if not department:
                raise serializers.ValidationError({"department_code": f"Department '{department_code}' not found."})
            instance.department = department

        if manager_emp_id and manager_emp_id.strip():
            from django.db.models import Q

            name = manager_emp_id.strip()
            manager = Employee.objects.filter(user__emp_id__iexact=name, is_deleted=False).first()

            if not manager:
                name_parts = name.split()
                if len(name_parts) >= 2:
                    manager = Employee.objects.filter(
                        Q(user__first_name__icontains=name_parts[0]) &
                        Q(user__last_name__icontains=name_parts[-1]),
                        is_deleted=False
                    ).first()
                else:
                    manager = Employee.objects.filter(
                        Q(user__first_name__icontains=name) |
                        Q(user__last_name__icontains=name),
                        is_deleted=False
                    ).first()

            if not manager:
                raise serializers.ValidationError({"manager": f"Manager '{manager_emp_id}' not found."})
            if manager.user.role not in ["Manager", "Admin"]:
                raise serializers.ValidationError({"manager": "Assigned manager must be Manager/Admin."})

            instance.manager = manager

        user = instance.user
        if "first_name" in validated_data:
            user.first_name = validated_data.pop("first_name")
        if "last_name" in validated_data:
            user.last_name = validated_data.pop("last_name")
        if "email" in validated_data:
            user.email = validated_data.pop("email")
        user.role = role
        user.save()

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
        if value.size > 2 * 1024 * 1024:
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
# EMPLOYEE BULK CSV UPLOAD SERIALIZER (Enhanced)
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

        # Normalize CSV headers (case-insensitive)
        normalized_rows = []
        for row in reader:
            fixed_row = {}
            for key, value in row.items():
                key_clean = key.strip().lower().replace("_", " ").title()
                fixed_row[key_clean] = value.strip() if value else ""
            normalized_rows.append(fixed_row)

        if not normalized_rows:
            raise serializers.ValidationError({"error": "CSV file is empty."})

        # ✅ Validate headers
        required_cols = ["First Name", "Last Name", "Email", "Department Code", "Role", "Joining Date"]
        missing = [col for col in required_cols if col not in normalized_rows[0]]
        if missing:
            raise serializers.ValidationError({"error": f"CSV must contain: {', '.join(required_cols)}"})

        success_count, errors = 0, []
        seen_emails, seen_name_dept = set(), set()

        with transaction.atomic():
            for i, row in enumerate(normalized_rows, start=2):
                try:
                    # Extract & clean values
                    email = row.get("Email", "").lower()
                    first_name = row.get("First Name", "").strip().title()
                    last_name = row.get("Last Name", "").strip().title()
                    dept_code = row.get("Department Code", "").strip()
                    role = row.get("Role", "").capitalize()
                    joining_date_str = row.get("Joining Date", "").strip()
                    contact_number = row.get("Contact Number") or None
                    designation = row.get("Designation") or None
                    project_name = row.get("Project Name") or None
                    manager_emp_id = row.get("Manager") or None

                    # 1️⃣ Mandatory Field Validation
                    if not all([email, first_name, last_name, dept_code, role, joining_date_str]):
                        errors.append(f"Row {i}: Missing one or more mandatory fields.")
                        continue

                    # 2️⃣ Validate Role
                    if role not in ["Admin", "Manager", "Employee"]:
                        errors.append(f"Row {i}: Invalid role '{role}'. Must be Admin/Manager/Employee.")
                        continue

                    # 3️⃣ Prevent duplicate email within file
                    if email in seen_emails:
                        errors.append(f"Row {i}: Duplicate email '{email}' in CSV.")
                        continue
                    seen_emails.add(email)

                    # 4️⃣ Department Validation
                    department = Department.objects.filter(
                        models.Q(code__iexact=dept_code)
                        | models.Q(name__iexact=dept_code)
                        | models.Q(id__iexact=dept_code)
                    ).first()
                    if not department:
                        errors.append(f"Row {i}: Department '{dept_code}' not found.")
                        continue
                    if not department.is_active:
                        errors.append(f"Row {i}: Department '{department.name}' is inactive.")
                        continue

                    # 5️⃣ Validate Email uniqueness in DB
                    if User.objects.filter(email__iexact=email).exists():
                        errors.append(f"Row {i}: Email '{email}' already exists in system.")
                        continue

                    # 6️⃣ Validate Joining Date format
                    try:
                        joining_date = datetime.strptime(joining_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        errors.append(f"Row {i}: Joining Date must be in YYYY-MM-DD format.")
                        continue

                    # 7️⃣ Validate Duplicate Employee (Same Name + Dept)
                    name_dept_key = f"{first_name.lower()}_{last_name.lower()}_{department.id}"
                    if name_dept_key in seen_name_dept:
                        errors.append(f"Row {i}: Duplicate employee '{first_name} {last_name}' in same department in file.")
                        continue
                    seen_name_dept.add(name_dept_key)

                    if Employee.objects.filter(
                        user__first_name__iexact=first_name,
                        user__last_name__iexact=last_name,
                        department=department,
                        is_deleted=False
                    ).exists():
                        errors.append(
                            f"Row {i}: Employee '{first_name} {last_name}' already exists in department '{department.name}'."
                        )
                        continue

                    # 8️⃣ Manager Validation
                    manager = None
                    if manager_emp_id and manager_emp_id not in ["", "None", "null"]:
                        manager = Employee.objects.filter(user__emp_id__iexact=manager_emp_id).first()
                        if not manager:
                            errors.append(f"Row {i}: Manager '{manager_emp_id}' not found.")
                            continue
                        if not getattr(manager.user, "role", None) in ["Manager", "Admin"]:
                            errors.append(f"Row {i}: Manager '{manager_emp_id}' must have role Manager/Admin.")
                            continue

                    # 9️⃣ Contact number validation
                    if contact_number:
                        pattern = r"^\+91[6-9]\d{9}$"
                        if not re.match(pattern, contact_number):
                            errors.append(f"Row {i}: Contact number '{contact_number}' must start with +91 and be valid.")
                            continue

                    # 🔟 Create User & Employee
                    user = User.objects.create_user(
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        role=role,
                        department=department,
                    )

                    Employee.objects.create(
                        user=user,
                        department=department,
                        manager=manager,
                        designation=designation,
                        project_name=project_name,
                        contact_number=contact_number,
                        joining_date=joining_date,
                        role=role,
                        status="Active",
                    )

                    success_count += 1

                except Exception as e:
                    errors.append(f"Row {i}: Unexpected error - {str(e)}")

        return {"success_count": success_count, "errors": errors}


