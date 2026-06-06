"""Utah interface — the surfaces Michael lives in.

The web surface (the Black Gold command deck) is served here and bridged to the
daemon: ``/api/status`` reflects live daemon state and ``/events`` streams the
bus over SSE — so the deck reflects backend state by push, never stale poll.
The agentic voice+chat harness layers on top (the brain's ``tell`` is its core).
"""
