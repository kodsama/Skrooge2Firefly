from skrooge2firefly.writers.orphans import FixedDecider, MappingDecider, PromptDecider


def test_fixed_decider_returns_its_action():
    assert FixedDecider("delete").decide("transaction", "k", "x") == "delete"


def test_prompt_decider_delete_all_is_sticky():
    outputs = []
    answers = iter(["D"])  # Delete all remaining
    d = PromptDecider(input_fn=lambda _: next(answers), output_fn=outputs.append)
    assert d.decide("transaction", "k1", "l1") == "delete"
    assert d.decide("transaction", "k2", "l2") == "delete"  # no second prompt


def test_prompt_decider_quit_ignores_rest():
    answers = iter(["q"])
    d = PromptDecider(input_fn=lambda _: next(answers), output_fn=lambda _: None)
    assert d.decide("transaction", "k1", "l1") == "ignore"
    assert d.decide("transaction", "k2", "l2") == "ignore"


def test_mapping_decider_uses_file_then_fallback():
    d = MappingDecider({"skrooge:op:1": "delete"}, fallback=FixedDecider("ignore"))
    assert d.decide("transaction", "skrooge:op:1", "x") == "delete"
    assert d.decide("transaction", "skrooge:op:2", "x") == "ignore"
