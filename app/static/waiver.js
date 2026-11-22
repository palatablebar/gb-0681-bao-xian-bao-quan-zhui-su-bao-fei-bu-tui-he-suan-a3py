'use strict';
const waiverFeedback=document.getElementById('feedback');
async function waiverCall(url,method,payload){
  try{
    const options={method,headers:{'Content-Type':'application/json'}};
    if(payload)options.body=JSON.stringify(payload);
    const response=await fetch(url,options);
    const body=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(body.message||'操作失败');
    window.location.reload();
  }catch(error){if(waiverFeedback)waiverFeedback.textContent=error.message;}
}
function waiverFormValues(form){const data={};new FormData(form).forEach((value,key)=>{if(value!=='')data[key]=value;});return data;}
const settleButton=document.getElementById('settle');
if(settleButton)settleButton.addEventListener('click',()=>waiverCall('/api/m8_waiver/determinations/'+settleButton.dataset.det+'/settle','POST',{}));
document.querySelectorAll('[data-delete]').forEach(button=>button.addEventListener('click',()=>waiverCall(button.dataset.delete,'DELETE')));
const detForm=document.getElementById('det-form');
if(detForm)detForm.addEventListener('submit',event=>{event.preventDefault();waiverCall('/api/m8_waiver/determinations','POST',waiverFormValues(detForm));});
const payForm=document.getElementById('pay-form');
if(payForm)payForm.addEventListener('submit',event=>{event.preventDefault();waiverCall('/api/m8_waiver/determinations/'+payForm.dataset.det+'/payments','POST',waiverFormValues(payForm));});
const recvForm=document.getElementById('recv-form');
if(recvForm)recvForm.addEventListener('submit',event=>{event.preventDefault();waiverCall('/api/m8_waiver/determinations/'+recvForm.dataset.det+'/receivables','POST',waiverFormValues(recvForm));});
