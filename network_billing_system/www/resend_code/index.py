import frappe


def get_context(context):
    is_enabled = 1
    # is_enabled = frappe.db.get_single_value(
    #     "Kopokopo Mpesa Setting", "allow_code_resend"
    # )
    if is_enabled:
        return context
    else:
        frappe.local.flags.redirect_location = "/404"
        raise frappe.Redirect

def get_sms(number):
    sms_list = frappe.get_list(
        "SMS Logs",
        filters=[["mobile_number", "=", number], ["captive_portal_code", "!=", ""]],
        limit=1,
        fields=["name", "mobile_number", "captive_portal_code"],
        order_by="creation desc",
        ignore_permissions=True,
    )
    if sms_list:
        return sms_list[0]

@frappe.whitelist(allow_guest=True)
def process_sms(contact):
    import json
    from network_billing_system.network_billing_system.doctype.sms_logs.sms_logs import send_msg, sanitize_mobile_number

    contact = json.loads(contact)
    # provide the number to the sdk
    number = contact.get("number", None)
    number = sanitize_mobile_number(number)
    sms_dic = get_sms(number)
    try:
        if sms_dic:
            send_msg(sms_dic.get("mobile_number"), sms_dic.get("name"), sms_dic.get("captive_portal_code"))
            return 201
        else:
            return 4002
    except Exception as err:
        print(err)
        return 4001

    code_ = process_stk(contact.get("number", None))
    return code_
