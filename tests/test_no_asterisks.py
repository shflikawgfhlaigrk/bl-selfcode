from utah import core
from utah.core import _clean_text


def test_clean_text_guarantees_zero_asterisks():
    assert _clean_text("**Pick** one *thing*") == "Pick one thing"
    assert _clean_text("* one\n* two").splitlines() == ["- one", "- two"]
    assert _clean_text("plain text") == "plain text"
    assert _clean_text("") == ""
    # whatever the shape, the hard guarantee is: no asterisk survives
    for s in ["a ** b * c *** d", "**", "x*y*z", "- already a dash"]:
        assert "*" not in _clean_text(s)


def test_tell_stream_wrapper_strips_every_chunk(monkeypatch):
    monkeypatch.setattr(core, "_tell_stream_core",
                        lambda text, **k: iter([("source", "brain"),
                                                ("thinking", "weigh **options**"),
                                                ("answer", "**bold** answer"),
                                                ("done", "final *answer*")]))
    events = list(core.tell_stream("x"))
    assert all("*" not in chunk for _, chunk in events)
    assert ("answer", "bold answer") in events
