# راهنمای اجرای تست‌های KTC

## ساختار فایل‌ها

```
DjangoProjectKTC/
├── pytest.ini
└── tests/
    ├── conftest.py              ← تنظیمات pytest
    ├── factories.py             ← ابزارهای ساخت داده تست
    ├── test_models.py           ← تست لایه مدل
    ├── test_approval_workflow.py ← تست جریان تایید گزارش (مهم‌ترین)
    ├── test_project_revision_api.py ← تست API پروژه و ریویژن
    ├── test_cpm_engine.py       ← تست موتور CPM
    └── test_evm_engine.py       ← تست موتور EVM
```

## نصب پیش‌نیازها

```bash
pip install pytest pytest-django pytest-cov
```

## اجرا

```bash
# همه تست‌ها
pytest 

# یک فایل خاص
pytest tests/test_approval_workflow.py

# یک کلاس خاص
pytest tests/test_approval_workflow.py::TestIntraUnitApproval

# یک تست خاص
pytest tests/test_approval_workflow.py::TestIntraUnitApproval::test_reviewer_approves_and_report_becomes_final

# با گزارش coverage
pytest tests/ --cov=ktcPlanning --cov=CustomUser --cov-report=html
```

## پوشش تست‌ها

| فایل | موضوع | تعداد تست |
|------|--------|-----------|
| `test_models.py` | OrgUnit, CustomUser, Project, Revision, TaskReportLog | ~18 |
| `test_approval_workflow.py` | جریان تایید ۲ مرحله‌ای + bypass + reject | ~22 |
| `test_project_revision_api.py` | CRUD پروژه، قفل ریویژن، gantt-data | ~15 |
| `test_cpm_engine.py` | CPM، مسیر بحرانی، cycle detection | ~10 |
| `test_evm_engine.py` | PV، EVM، SPI/CPI | ~10 |

## نکات مهم

### URL names مورد نیاز
تست‌ها از این نام‌های URL استفاده می‌کنند (DRF Router آن‌ها را خودکار می‌سازد):

```python
reverse("project-list")
reverse("project-detail", kwargs={"pk": ...})
reverse("revision-list")
reverse("revision-detail", kwargs={"pk": ...})
reverse("revision-approve", kwargs={"pk": ...})
reverse("revision-gantt-data", kwargs={"pk": ...})
reverse("revision-run-cpm", kwargs={"pk": ...})
reverse("task-report-list")
reverse("task-report-detail", kwargs={"pk": ...})
reverse("task-report-approve", kwargs={"pk": ...})
reverse("task-report-reject", kwargs={"pk": ...})
reverse("variance-report-calculate")
reverse("activity-list")
```

اگر router با `basename` متفاوتی register شده، نام‌ها را در `conftest.py` تنظیم کنید.

### دیتابیس تست
تست‌ها از SQLite `:memory:` استفاده می‌کنند — هیچ دیتابیس خارجی نیاز نیست.

### fixtures
هر تست `@pytest.mark.django_db` دارد و دیتابیس بین تست‌ها پاک می‌شود.
