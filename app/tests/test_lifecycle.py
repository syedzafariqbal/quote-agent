import pytest

from quote_core.lifecycle import (
    CUSTOMER,
    SYSTEM,
    TERMINAL,
    ConcurrentModification,
    Forbidden,
    InvalidTransition,
    QuoteRepo,
    Status,
    allowed_targets,
    check_transition,
)

S = Status


def test_happy_path_is_allowed():
    path = [
        (S.REQUESTED, S.PROCESSING, {SYSTEM}),
        (S.PROCESSING, S.PENDING_APPROVAL, {SYSTEM}),
        (S.PENDING_APPROVAL, S.APPROVED, {"Manager"}),
        (S.APPROVED, S.SENT, {"SalesRep"}),
        (S.SENT, S.VIEWED, {CUSTOMER}),
        (S.VIEWED, S.ACCEPTED, {CUSTOMER}),
    ]
    for frm, to, roles in path:
        check_transition(frm, to, roles)


@pytest.mark.parametrize("frm,to", [(S.REQUESTED, S.SENT), (S.DRAFT, S.ACCEPTED), (S.ACCEPTED, S.DRAFT),
                                    (S.EXPIRED, S.SENT), (S.REJECTED, S.PROCESSING)])
def test_illegal_transitions(frm, to):
    with pytest.raises(InvalidTransition):
        check_transition(frm, to, {"Admin", SYSTEM, CUSTOMER})


def test_roles_are_enforced():
    with pytest.raises(Forbidden):
        check_transition(S.PENDING_APPROVAL, S.APPROVED, {"SalesRep"})
    with pytest.raises(Forbidden):
        check_transition(S.SENT, S.ACCEPTED, {"SalesRep"})  # only the customer accepts
    with pytest.raises(Forbidden):
        check_transition(S.SENT, S.EXPIRED, {"Admin"})  # only the scheduler expires


def test_terminal_states_have_no_exits():
    for t in TERMINAL:
        assert allowed_targets(t, {"Admin", SYSTEM, CUSTOMER}) == []


def test_conditional_write_blocks_concurrent_transition(table):
    repo = QuoteRepo(table)
    repo.create_quote("q1", "Acme GmbH", {"country": "DE"}, "rep@example.com")
    repo.transition("q1", S.PROCESSING, actor="worker", roles={SYSTEM})
    # a second worker still thinks the quote is REQUESTED
    with pytest.raises(ConcurrentModification):
        repo.transition("q1", S.PROCESSING, actor="worker2", roles={SYSTEM}, expected=S.REQUESTED)
    assert repo.get_quote("q1")["status"] == S.PROCESSING
    audit = repo.list_audit("q1")
    assert [a["to"] for a in audit] == ["REQUESTED", "PROCESSING"]


def test_idempotent_create(table):
    repo = QuoteRepo(table)
    first = repo.create_quote("a", "Acme", {}, "rep", idempotency_key="key-1")
    second = repo.create_quote("b", "Acme", {}, "rep", idempotency_key="key-1")
    assert first == second == "a"


def test_list_by_status(table):
    repo = QuoteRepo(table)
    repo.create_quote("x", "X AG", {}, "rep")
    repo.create_quote("y", "Y AG", {}, "rep")
    repo.transition("y", S.PROCESSING, actor="worker", roles={SYSTEM})
    assert [q["quoteId"] for q in repo.list_by_status("REQUESTED")] == ["x"]
