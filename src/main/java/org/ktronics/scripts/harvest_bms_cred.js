// Read the PACEEX app's current Aliyun IoT credential straight from memory (no UI, no
// network). Used by refresh_bms_creds.py to re-bootstrap the GitHub secrets. See README_BMS.md.
Java.perform(function(){
  function run(){
    try{
      var ctx=Java.use('android.app.ActivityThread').currentApplication().getApplicationContext();
      var M=Java.use('com.aliyun.iot.aep.sdk.credential.IotCredentialManager.IoTCredentialManageImpl');
      var inst=null;
      M.getInstance.overloads.forEach(function(ov){ if(inst)return; try{ inst=(ov.argumentTypes.length>0)?ov.call(M,ctx):ov.call(M);}catch(e){} });
      if(!inst){ console.log('HARVEST-ERR no-inst'); return; }
      var o={iot:inst.getIoTToken(), refresh:inst.getIoTRefreshToken(), id:inst.getIoTIdentity()};
      if(!o.refresh||!o.id){ console.log('HARVEST-ERR empty'); return; }
      console.log('HARVEST '+JSON.stringify(o));
    }catch(e){ console.log('HARVEST-ERR '+e); }
  }
  setTimeout(run, 1200);
});
