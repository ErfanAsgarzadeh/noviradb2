import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ktcPlanning.financial_services import (
    allocate_cost_transaction_to_milestones,
    approve_task_delivery,
    evaluate_milestone_eligibility,
    get_task_delivery_attachment_capabilities,
    get_task_financial_status,
    reject_task_delivery,
    register_transaction,
    submit_task_delivery,
)
from ktcPlanning.models import (
    Assignment,
    CostTransaction,
    CostTransactionMilestoneAllocation,
    PaymentMilestone,
    PaymentTransaction,
    Resource,
    TaskDelivery,
    TaskDeliveryAttachment,
    TaskFinancialPlan,
)
from tests.factories import assign_role, make_company_admin, make_member, make_project, make_revision, make_task


def evidence_file(name="evidence.pdf", content=b"%PDF-1.4 evidence", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class TaskDeliveryAttachmentAcceptanceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="task_delivery_acceptance_media_")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.editor = make_company_admin()
        self.project = make_project(creator=self.editor, scope="intra_unit")
        self.revision = make_revision(self.project, creator=self.editor, approved=True)
        self.task, _ = make_task(self.project, self.revision, title="Delivery Acceptance Task")
        self.reviewer = make_member()
        assign_role(self.task, self.revision, self.reviewer, role="reviewer")
        self.viewer = make_member()
        assign_role(self.task, self.revision, self.viewer, role="executor")
        self.outsider = make_member()
        self.plan = TaskFinancialPlan.objects.create(
            task=self.task,
            direction=TaskFinancialPlan.DIRECTION_PAYABLE,
            contract_amount=Decimal("1000.00"),
            currency="IRR",
            status=TaskFinancialPlan.STATUS_ACTIVE,
            created_by=self.editor,
        )
        self.first_milestone = PaymentMilestone.objects.create(
            financial_plan=self.plan,
            title="Start",
            sequence=1,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_START,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("300.00"),
        )
        self.delivery_milestone = PaymentMilestone.objects.create(
            financial_plan=self.plan,
            title="Before delivery",
            sequence=2,
            trigger_type=PaymentMilestone.TRIGGER_BEFORE_DELIVERY,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("500.00"),
        )
        PaymentMilestone.objects.create(
            financial_plan=self.plan,
            title="Closeout",
            sequence=3,
            trigger_type=PaymentMilestone.TRIGGER_TASK_COMPLETION,
            amount_type=PaymentMilestone.AMOUNT_FIXED,
            fixed_amount=Decimal("200.00"),
        )
        resource = Resource.objects.create(code="ACC-COST", name="Acceptance Cost", resource_type=Resource.COST)
        assignment = Assignment.objects.create(
            revision=self.revision,
            task=self.task,
            resource=resource,
            units_percent=Decimal("100.00"),
        )
        self.cost_transaction = CostTransaction.objects.create(
            project=self.project,
            revision=self.revision,
            task=self.task,
            assignment=assignment,
            financial_plan=self.plan,
            transaction_type="COST",
            amount=Decimal("800.00"),
            transaction_date=timezone.localdate(),
            created_by=self.editor,
        )
        allocate_cost_transaction_to_milestones(self.cost_transaction)
        register_transaction(
            self.first_milestone,
            PaymentTransaction.TYPE_PAYMENT,
            Decimal("100.00"),
            timezone.localdate(),
            self.editor,
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def make_delivery(self, delivery_status=TaskDelivery.STATUS_DRAFT, reference="DEL-ACC"):
        return TaskDelivery.objects.create(
            project=self.project,
            task=self.task,
            status=delivery_status,
            delivery_reference=reference,
            created_by=self.editor,
        )

    def upload(self, delivery, user=None, file_obj=None, description=""):
        return self.client_for(user or self.editor).post(
            reverse("task-delivery-attachments", kwargs={"pk": delivery.id}),
            {"file": file_obj or evidence_file(), "description": description},
            format="multipart",
        )

    def test_upload_to_draft_and_rejected_files_is_accepted_and_canonical(self):
        draft = self.make_delivery()
        draft_response = self.upload(draft, file_obj=evidence_file("../unsafe evidence.pdf"), description="A")
        draft_attachment = TaskDeliveryAttachment.objects.get()
        rejected = self.make_delivery(TaskDelivery.STATUS_REJECTED, reference="DEL-REJ")
        rejected_response = self.upload(rejected, file_obj=evidence_file("rejected.png", b"png", "image/png"))

        self.assertEqual(draft_response.status_code, status.HTTP_201_CREATED, draft_response.data)
        self.assertEqual(draft_response.data["delivery"]["attachment_count"], 1)
        self.assertTrue(draft_response.data["delivery"]["has_attachments"])
        self.assertEqual(draft_attachment.original_filename, "unsafe_evidence.pdf")
        self.assertEqual(rejected_response.status_code, status.HTTP_201_CREATED, rejected_response.data)

    def test_submitted_and_approved_attachments_are_immutable(self):
        draft = self.make_delivery()
        attachment_id = self.upload(draft).data["attachment"]["id"]
        submitted = submit_task_delivery(draft, user=self.editor)
        submitted_upload = self.upload(submitted, file_obj=evidence_file("submitted.pdf"))
        submitted_delete = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))
        approved, _ = approve_task_delivery(submitted, user=self.reviewer)
        approved_upload = self.upload(approved, file_obj=evidence_file("approved.pdf"))
        approved_delete = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))

        self.assertEqual(submitted_upload.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(submitted_delete.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(approved_upload.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(approved_delete.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(get_task_delivery_attachment_capabilities(approved, self.editor), {"can_upload": False, "can_delete": False})

    def test_delete_from_draft_and_rejected_cleans_physical_file(self):
        draft = self.make_delivery()
        draft_attachment_id = self.upload(draft).data["attachment"]["id"]
        draft_attachment = TaskDeliveryAttachment.objects.get(pk=draft_attachment_id)
        draft_path = draft_attachment.file.path
        rejected = self.make_delivery(TaskDelivery.STATUS_REJECTED, reference="DEL-REJ")
        rejected_attachment_id = self.upload(rejected, file_obj=evidence_file("rejected.pdf")).data["attachment"]["id"]
        rejected_attachment = TaskDeliveryAttachment.objects.get(pk=rejected_attachment_id)
        rejected_path = rejected_attachment.file.path

        draft_delete = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": draft_attachment_id}))
        rejected_delete = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": rejected_attachment_id}))

        self.assertEqual(draft_delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(rejected_delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskDeliveryAttachment.objects.filter(pk=draft_attachment_id).exists())
        self.assertFalse(TaskDeliveryAttachment.objects.filter(pk=rejected_attachment_id).exists())
        self.assertFalse(TaskDeliveryAttachment._meta.get_field("file").storage.exists(draft_path))
        self.assertFalse(TaskDeliveryAttachment._meta.get_field("file").storage.exists(rejected_path))

    def test_unauthorized_upload_download_and_delete_are_scoped(self):
        delivery = self.make_delivery()
        attachment_id = self.upload(delivery).data["attachment"]["id"]
        outsider = self.client_for(self.outsider)

        upload_response = outsider.post(
            reverse("task-delivery-attachments", kwargs={"pk": delivery.id}),
            {"file": evidence_file("outsider.pdf")},
            format="multipart",
        )
        download_response = outsider.get(reverse("task-delivery-attachment-download", kwargs={"pk": attachment_id}))
        delete_response = outsider.delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))

        self.assertEqual(upload_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(download_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(delete_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_file_validation_rejects_unsafe_and_oversized_files_without_rows(self):
        delivery = self.make_delivery()
        unsafe = self.upload(delivery, file_obj=evidence_file("run.exe", b"x", "application/x-msdownload"))
        script = self.upload(delivery, file_obj=evidence_file("script.js", b"alert(1)", "application/javascript"))
        archive = self.upload(delivery, file_obj=evidence_file("data.zip", b"zip", "application/zip"))
        oversized = self.upload(delivery, file_obj=evidence_file("large.pdf", b"x" * (10 * 1024 * 1024 + 1)))

        self.assertEqual(unsafe.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(script.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(archive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(oversized.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TaskDeliveryAttachment.objects.count(), 0)

    def test_download_metadata_and_list_summary_are_permission_aware(self):
        delivery = self.make_delivery()
        attachment_id = self.upload(delivery, file_obj=evidence_file("handover.pdf"), description="handover").data["attachment"]["id"]
        editor = self.client_for(self.editor)
        viewer = self.client_for(self.viewer)

        list_response = editor.get(reverse("task-delivery-list"), {"task_id": str(self.task.id)})
        detail_response = editor.get(reverse("task-delivery-detail", kwargs={"pk": delivery.id}))
        download_response = viewer.get(reverse("task-delivery-attachment-download", kwargs={"pk": attachment_id}))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data[0]["attachment_count"], 1)
        self.assertEqual(list_response.data[0]["attachments"], [])
        self.assertEqual(detail_response.data["attachments"][0]["original_filename"], "handover.pdf")
        self.assertEqual(download_response.status_code, status.HTTP_200_OK)
        self.assertIn("handover.pdf", download_response["Content-Disposition"])
        self.assertTrue(detail_response.data["attachments"][0]["download_url"].startswith("/api/planning/task-delivery-attachments/"))

    def test_rejected_replacement_flow_and_approval_gate(self):
        draft = self.make_delivery()
        evidence_a_id = self.upload(draft, file_obj=evidence_file("evidence-a.pdf")).data["attachment"]["id"]
        submitted = submit_task_delivery(draft, user=self.editor)
        rejected = reject_task_delivery(submitted, user=self.reviewer, reason="Replace evidence")
        delete_a = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": evidence_a_id}))
        upload_b = self.upload(rejected, file_obj=evidence_file("evidence-b.pdf"))
        submitted_again = submit_task_delivery(rejected, user=self.editor)
        approved, summaries = approve_task_delivery(submitted_again, user=self.reviewer)

        self.assertEqual(delete_a.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(upload_b.status_code, status.HTTP_201_CREATED, upload_b.data)
        self.assertEqual(TaskDeliveryAttachment.objects.count(), 1)
        self.assertEqual(TaskDeliveryAttachment.objects.get().original_filename, "evidence-b.pdf")
        self.assertEqual(approved.status, TaskDelivery.STATUS_APPROVED)
        self.assertTrue(summaries)
        self.assertEqual(evaluate_milestone_eligibility(self.delivery_milestone)["reason"], "delivery_approved")

    def test_attachments_have_no_financial_side_effects_and_approval_unlocks_allocation(self):
        delivery = self.make_delivery()
        before = {
            "allocation_count": CostTransactionMilestoneAllocation.objects.count(),
            "cost_count": CostTransaction.objects.count(),
            "cost_amount": CostTransaction.objects.aggregate(total=Sum("amount"))["total"],
            "paid": get_task_financial_status(self.task)["total_paid"],
            "payment_count": PaymentTransaction.objects.count(),
            "eligibility": evaluate_milestone_eligibility(self.delivery_milestone)["reason"],
        }
        upload_response = self.upload(delivery)
        attachment_id = upload_response.data["attachment"]["id"]
        delete_response = self.client_for(self.editor).delete(reverse("task-delivery-attachment-detail", kwargs={"pk": attachment_id}))
        after_attachment = {
            "allocation_count": CostTransactionMilestoneAllocation.objects.count(),
            "cost_count": CostTransaction.objects.count(),
            "cost_amount": CostTransaction.objects.aggregate(total=Sum("amount"))["total"],
            "paid": get_task_financial_status(self.task)["total_paid"],
            "payment_count": PaymentTransaction.objects.count(),
            "eligibility": evaluate_milestone_eligibility(self.delivery_milestone)["reason"],
        }
        submitted = submit_task_delivery(delivery, user=self.editor)
        approve_task_delivery(submitted, user=self.reviewer)
        after_approval = {
            "allocation_count": CostTransactionMilestoneAllocation.objects.count(),
            "cost_count": CostTransaction.objects.count(),
            "cost_amount": CostTransaction.objects.aggregate(total=Sum("amount"))["total"],
            "paid": get_task_financial_status(self.task)["total_paid"],
            "payment_count": PaymentTransaction.objects.count(),
            "eligibility": evaluate_milestone_eligibility(self.delivery_milestone)["reason"],
        }

        self.assertEqual(upload_response.status_code, status.HTTP_201_CREATED, upload_response.data)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(after_attachment, before)
        self.assertEqual(before["allocation_count"], 1)
        self.assertEqual(after_approval["allocation_count"], 2)
        self.assertEqual(after_approval["cost_count"], 1)
        self.assertEqual(after_approval["cost_amount"], Decimal("800.00"))
        self.assertEqual(after_approval["paid"], before["paid"])
        self.assertEqual(after_approval["payment_count"], before["payment_count"])
        self.assertEqual(after_approval["eligibility"], "delivery_approved")

    def test_capability_contract_is_consistent(self):
        draft = self.make_delivery()
        rejected = self.make_delivery(TaskDelivery.STATUS_REJECTED, reference="DEL-REJ")
        submitted = submit_task_delivery(self.make_delivery(reference="DEL-SUB"), user=self.editor)
        approved, _ = approve_task_delivery(submitted, user=self.reviewer)

        self.assertEqual(get_task_delivery_attachment_capabilities(draft, self.editor), {"can_upload": True, "can_delete": True})
        self.assertEqual(get_task_delivery_attachment_capabilities(rejected, self.editor), {"can_upload": True, "can_delete": True})
        self.assertEqual(get_task_delivery_attachment_capabilities(submitted, self.editor), {"can_upload": False, "can_delete": False})
        self.assertEqual(get_task_delivery_attachment_capabilities(approved, self.editor), {"can_upload": False, "can_delete": False})
