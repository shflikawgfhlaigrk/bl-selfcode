from utah import router
from utah.product import jobs_status, engine_status


def test_self_state_questions_route_to_capabilities():
    R = router.route
    assert R("what do you uphold every single day").name == "JOBS"
    assert R("what are your responsibilities").name == "JOBS"
    assert R("list your jobs").name == "JOBS"
    assert R("what is your engine state").name == "ENGINE"
    assert R("are your engines live").name == "ENGINE"


def test_reasoning_and_general_are_not_hijacked():
    R = router.route
    # the over-match bug class: a reasoning question that merely mentions engines
    assert R("why does the engine lose money on ranges").name == "BRAIN"
    assert R("how do engines work").name != "ENGINE"
    assert R("what is the capital of France").name == "BRAIN"


def test_capabilities_return_grounded_asterisk_free_text():
    j = jobs_status.answer("what do you uphold")
    assert isinstance(j, str) and j and "*" not in j
    e = engine_status.answer("engine state")
    assert isinstance(e, str) and e and "*" not in e
