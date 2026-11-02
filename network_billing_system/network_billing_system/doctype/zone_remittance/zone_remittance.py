import frappe
from frappe.model.document import Document

from network_billing_system.portal_rules import normalize_phone, payment_id
from network_billing_system.zone_finance import FINANCE_ROLES, ensure_funds, lock_zone, money
from network_billing_system.recipient_rules import destination_details


class ZoneRemittance(Document):
    def validate(self):
        frappe.only_for(FINANCE_ROLES)
        self.currency = "KES"
        if self.is_new():
            self.status = "Draft"
            self.provider_transfer_id = None
        if money(self.amount) <= 0 or money(self.fee_allowance) < 0:
            frappe.throw("Enter a positive amount and a nonnegative fee allowance.")
        if self.provider_environment not in ("live", "sandbox"):
            frappe.throw("Select live or sandbox.")
        if self.withdrawal_method not in ("Kopo Kopo API", "External Withdrawal"):
            frappe.throw("Select a withdrawal method.")
        if self.withdrawal_method == "External Withdrawal":
            self.external_reference = str(self.external_reference or "").strip()
            if not all((self.external_reference, self.withdrawn_at, self.withdrawal_evidence)) or str(self.external_verified) not in ("1", "True"):
                frappe.throw("Enter the external reference, withdrawal date and evidence, and confirm provider verification.")
            if not frappe.db.get_value("File", {
                "file_url": self.withdrawal_evidence, "is_private": 1
            }, "name"):
                frappe.throw("Attach a private file as withdrawal evidence.")
        else:
            self.external_reference = None
            if not self.recipient:
                frappe.throw("Select a registered remittance recipient before sending money.")
            recipient = frappe.get_doc("Remittance Recipient", self.recipient)
            if (not recipient.enabled or recipient.registration_status != "Registered"
                    or recipient.zone != self.zone
                    or recipient.provider_environment != self.provider_environment):
                frappe.throw("Select an enabled registered recipient matching this zone and environment.")
            for field in (
                "destination_type", "recipient_name", "phone_number", "network",
                "account_name", "account_number", "bank_branch_ref", "till_number",
                "paybill_number", "paybill_account_number", "provider_reference",
            ):
                self.set(field, recipient.get(field))
        if self.destination_type == "mobile_wallet":
            self.phone_number = normalize_phone(self.phone_number).lstrip("+")
            if self.network != "Safaricom":
                frappe.throw("Only Safaricom M-Pesa remittances are currently supported.")
        elif self.destination_type == "bank_account":
            if not all((self.account_name, self.account_number)):
                frappe.throw("Enter the bank account name and account number.")
            if self.withdrawal_method == "Kopo Kopo API" and not self.bank_branch_ref:
                frappe.throw("Enter the account name, account number and Kopo Kopo bank branch reference.")
        elif self.destination_type in ("till", "paybill", "merchant_wallet", "merchant_bank_account"):
            try:
                destination_details(self)
            except ValueError as exc:
                frappe.throw(str(exc))
        else:
            frappe.throw("Select a supported remittance destination type.")
        if not self.source_identifier or not self.recipient_name:
            frappe.throw("Enter the Kopo Kopo source identifier and recipient name.")
        if self.docstatus == 0:
            self.status = "Draft"
            self.provider_transfer_id = None

    def before_submit(self):
        self.status = "Draft"
        self.provider_transfer_id = None
        self.last_error = ""
        if self.withdrawal_method == "External Withdrawal":
            try:
                self.provider_transfer_id = payment_id(self.external_reference)
            except ValueError:
                # Some provider statement references are not transfer UUIDs.
                # The separate unique external reference still prevents duplicates.
                self.provider_transfer_id = None

    def on_submit(self):
        frappe.only_for(FINANCE_ROLES)
        if self.withdrawal_method == "External Withdrawal":
            lock_zone(self.zone)
            self.db_set("status", "Paid")
            return
        # The draft is excluded from the balance, so reserve only after the
        # per-zone lock and funds check. Submission does not transfer money.
        ensure_funds(self.zone, self.provider_environment, money(self.amount) + money(self.fee_allowance))
        self.db_set("status", "Queued")

    def before_cancel(self):
        frappe.only_for(FINANCE_ROLES)
        lock_zone(self.zone)
        current = frappe.db.get_value("Zone Remittance", self.name, "status", for_update=True)
        if current not in ("Queued", "Failed"):
            frappe.throw("A sent or uncertain remittance cannot be cancelled. Reconcile it first.")

    def on_cancel(self):
        self.db_set("status", "Cancelled")
