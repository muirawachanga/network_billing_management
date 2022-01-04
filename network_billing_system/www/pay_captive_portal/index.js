async function submit() {
    let button = document.getElementById('submit-button');
    button.disabled = true;
    let form = document.querySelector('#customer-form');
    if (!form.checkValidity()) {
        form.reportValidity();
        button.disabled = false;
        return;
    }
    let contact = get_form_data();
    let appointment =  frappe.call({
        method: 'network_billing_system.www.pay_captive_portal.index.process_payment',
        args: {
            'contact': contact,
        },
        callback: (response)=>{
            if (response.message == "201") {
                frappe.show_alert("Please keep your phone on");
            }
            setTimeout(()=>{
                let redirect_url = "/";
                window.location.href = redirect_url;},5000)
        },
        error: (err)=>{
            frappe.show_alert("Something went wrong please try again");
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