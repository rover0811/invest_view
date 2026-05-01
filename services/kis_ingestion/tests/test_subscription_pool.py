import pytest
from kis_ingestion.subscription_pool import KISSubscriptionPool

def test_set_desired_and_diff():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930", "000660"], "H0STCNT0")
    
    to_sub, to_unsub = pool.diff()
    
    assert len(to_sub) == 2
    assert ("H0STCNT0", "005930") in to_sub
    assert ("H0STCNT0", "000660") in to_sub
    assert len(to_unsub) == 0
    assert pool.desired_count == 2

def test_confirm_subscribed():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930"], "H0STCNT0")
    pool.confirm_subscribed("H0STCNT0", "005930")
    
    to_sub, to_unsub = pool.diff()
    
    assert len(to_sub) == 0
    assert len(to_unsub) == 0
    assert pool.actual_count == 1

def test_cap_exceeded():
    pool = KISSubscriptionPool(cap=2)
    with pytest.raises(ValueError, match="Subscription cap exceeded"):
        pool.set_desired(["005930", "000660", "035420"], "H0STCNT0")

def test_diff_with_unsubscribe():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930", "000660"], "H0STCNT0")
    pool.confirm_subscribed("H0STCNT0", "005930")
    pool.confirm_subscribed("H0STCNT0", "000660")
    
    pool.set_desired(["005930"], "H0STCNT0")
    
    to_sub, to_unsub = pool.diff()
    
    assert len(to_sub) == 0
    assert len(to_unsub) == 1
    assert ("H0STCNT0", "000660") in to_unsub

def test_switch_market():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930"], "H0STCNT0")
    pool.confirm_subscribed("H0STCNT0", "005930")
    
    pool.switch_market("H0STCNT0", "H0NXCNT0")
    
    assert pool.desired_count == 1
    assert pool.actual_count == 1
    
    to_sub, to_unsub = pool.diff()
    assert ("H0NXCNT0", "005930") in to_sub
    assert ("H0STCNT0", "005930") in to_unsub

def test_clear_actual():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930"], "H0STCNT0")
    pool.confirm_subscribed("H0STCNT0", "005930")
    
    pool.clear_actual()
    
    assert pool.actual_count == 0
    to_sub, to_unsub = pool.diff()
    assert len(to_sub) == 1
    assert ("H0STCNT0", "005930") in to_sub

def test_multiple_tr_ids():
    pool = KISSubscriptionPool(cap=10)
    pool.set_desired(["005930"], "H0STCNT0")
    pool.set_desired(["005930"], "H0STASP0")
    
    assert pool.desired_count == 2
    to_sub, _ = pool.diff()
    assert ("H0STCNT0", "005930") in to_sub
    assert ("H0STASP0", "005930") in to_sub
