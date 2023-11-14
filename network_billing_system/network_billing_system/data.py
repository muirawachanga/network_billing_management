# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document


class DataImport(Document):
    def __init__(self, *args, **kwargs):
        self._log = ""
        super(DataImport, self).__init__(*args, **kwargs)
        self._file_column_count = self.get_file_column_count()

    def before_submit(self):
        if self.status != "Successful":
            self._file_column_count = self.get_file_column_count()
            self.run_import()
            self.set("status", "Successful")
            self.set("import_log", self._log)
            # self.save()

    def get_file_column_count(self):
        frappe.throw(
            _(
                "Unknown import file column count! _file_column_count not set. "
                "Program error, please contact support."
            )
        )

    # def submit(self):
    #     if not self.import_file:
    #         frappe.throw(_("Attach file first before submitting."))

    #     self.queue_action("submit", timeout=7200)
    #     frappe.msgprint(
    #         _(
    #             "The import task will run in the background and this view "
    #             "will be updated with the results."
    #         )
    #     )

    def run_import(self):
        from frappe.utils.file_manager import get_file

        fname, fcontent = get_file(self.import_file)

        from frappe.utils.csvutils import read_csv_content

        rows = read_csv_content(fcontent)

        # Skip header..
        data = rows[6:]

        if len(data[0]) != self._file_column_count:
            frappe.throw(
                _(
                    "Invalid File! Number of columns in the file must be {0}".format(
                        self._file_column_count
                    )
                )
            )

        self.write_log("Started data import...")
        imported, not_imported = self.import_data(data)
        total_data = len(data)
        already_existing = total_data - (imported + not_imported)
        self.write_log(
            (
                "Summary of the Import: Total Data In File: {0} ......Total "
                + "Imported: {1}, Failed Import Due to Error: {2}, Failed Import "
                + "due to already existing: {3}"
            ).format(total_data, imported, not_imported, already_existing)
        )
        self.write_log("Finished data import...")

    def write_log(self, text):
        self._log += text + "<br><br>"
