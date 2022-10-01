async function submit() {
    let button = document.getElementById('submit-button');
    button.disabled = false;
    let form = document.querySelector('#customer-form');
    if (!form.checkValidity()) {
        form.reportValidity();
        button.disabled = false;
        return;
    }
    let contact = get_form_data();
    let appointment =  frappe.call({
        method: 'network_billing_system.www.resend_code.index.process_sms',
        args: {
            'contact': contact,
        },
        callback: (response)=>{
            if (response.message == 201) {
                frappe.show_alert("Code has been resent");
            }
            setTimeout(()=>{
                window.history.go(-1);},4000)
                frappe.show_alert("Something went wrong please check the number you paid with");
    
        },
        error: (err)=>{
            frappe.show_alert("Something went wrong please try again or call 0718215557");
            button.disabled = false;
        }
    });
}

function get_form_data() {
    contact = {};
    let inputs = ['number'];
    inputs.forEach((id) => contact[id] = document.getElementById(`customer_${id}`).value)
    return contact
}