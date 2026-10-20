"""Legacy dispatch must flush BOTH eye buffers and propagate either failure."""
from test_rpc_failures import lib, call, f, result, status

f.result=result.value
assert call('ArArmWarp2DTo3DInit',result)==0
initial=lib.test_legacy_calls()
assert initial>0, 'Legacy branch was not called'
for physical in (*f.src,*f.dst):
    lib.test_faults(0,physical)
    status().value=0
    assert call('ArArmWarp2DTo3DProcess',f)==-5
    assert status().value==-5
assert lib.test_legacy_calls()==initial+6
lib.test_faults(0,0)
assert call('ArArmWarp2DTo3DProcess',f)==0 and status().value==0
assert call('ArArmWarp2DTo3DClose',result)==0
print('Legacy RPC: selected branch and all six source/destination flush failures passed.')
