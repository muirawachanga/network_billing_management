# Copyright (c) 2021, stephen and contributors
# For license information, please see license.txt

# import frappe
from __future__ import unicode_literals
import frappe
from network_billing_system.network_billing_system.data import DataImport


class ImportCaptiveCode(DataImport):
    def get_file_column_count(self):
        return 1

    def import_data(self, data):
        not_imported = 0
        imported = 0
        for row in data:
            try:
                doc = frappe.get_doc({"doctype": "Captive Portal Code"})
                doc.code = row[0].strip()
                doc.expiry_duration = self.expiry_duration
                doc.insert()
                self.write_log("Code : {0} imported successfully.".format(row[0]))
            except Exception as er:
                not_imported += 1
                self.write_log(
                    "Code: {0} Not Imported beacuse: {1}".format(row[0], er)
                )
            else:
                imported += 1
        return imported, not_imported

