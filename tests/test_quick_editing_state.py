from attendance_lab.presentation.quick.editing_state import CardMatchDraft, CourseEditingState


def test_course_drafts_survive_switch_and_do_not_share_mutable_values():
    editing = CourseEditingState(1)
    coefficients = {"report": 0.8}
    editing.set_draft("grading", "coefficients", coefficients)
    coefficients["report"] = 9
    editing.switch_course(2)
    assert editing.drafts == {}
    editing.set_draft("grading", "coefficients", {"report": 0.4})
    editing.switch_course(1)
    draft = editing.drafts
    assert draft == {"grading": {"coefficients": {"report": 0.8}}}
    draft["grading"]["coefficients"]["report"] = 10
    assert editing.drafts["grading"]["coefficients"] == {"report": 0.8}


def test_cell_range_uses_current_visible_identity_order_in_both_directions():
    editing = CourseEditingState(1)
    students, topics = ["c", "a", "b"], [9, 2, 6]
    editing.select_cell("b", 6, students, topics)
    editing.select_cell("a", 2, students, topics, extend=True)
    assert editing.selected_cells == [
        {"student_id": "a", "topic_id": 2},
        {"student_id": "a", "topic_id": 6},
        {"student_id": "b", "topic_id": 2},
        {"student_id": "b", "topic_id": 6},
    ]
    # Changing only the visible order keeps the original stable-ID anchor.
    editing.select_cell("c", 9, ["b", "a", "c"], [6, 9, 2], extend=True)
    assert editing.selected_cells == [
        {"student_id": student, "topic_id": topic}
        for student in ["b", "a", "c"]
        for topic in [6, 9]
    ]


def test_hidden_cell_anchor_does_not_create_range_when_it_reappears():
    editing = CourseEditingState()
    editing.select_cell("a", 1, ["a", "b"], [1, 2])
    editing.select_cell("b", 2, ["a", "b"], [1, 2], extend=True)
    editing.reconcile([], ["b"], [2])
    assert editing.selected_cells == [{"student_id": "b", "topic_id": 2}]
    editing.select_cell("b", 2, ["a", "b"], [1, 2], extend=True)
    assert editing.selected_cells == [{"student_id": "b", "topic_id": 2}]
    exposed = editing.selected_cells
    exposed[0]["student_id"] = "modified"
    assert editing.selected_cells[0]["student_id"] == "b"


def test_switch_course_clears_cell_anchor_even_for_overlapping_ids():
    editing = CourseEditingState(1)
    editing.select_cell("a", 1, ["a", "b"], [1, 2])
    editing.switch_course(2)
    assert editing.selected_cells == []
    editing.select_cell("b", 2, ["a", "b"], [1, 2], extend=True)
    assert editing.selected_cells == [{"student_id": "b", "topic_id": 2}]


def test_student_range_and_reconciliation_use_visible_stable_ids():
    editing = CourseEditingState()
    editing.select_student("c", ["c", "a", "b"])
    editing.select_student("b", ["c", "a", "b"], extend=True)
    assert editing.selected_students == ["c", "a", "b"]
    editing.reconcile(["a", "b"], [], [])
    assert editing.selected_students == ["a", "b"]
    editing.select_student("b", ["c", "a", "b"], extend=True)
    assert editing.selected_students == ["b"]
    exposed = editing.selected_students
    exposed.clear()
    assert editing.selected_students == ["b"]


def test_stale_selection_targets_are_ignored():
    editing = CourseEditingState()
    editing.select_cell("a", 1, ["a"], [1])
    editing.select_cell("removed", 1, ["a"], [1])
    editing.select_cell("a", 2, ["a"], [1], extend=True)
    assert editing.selected_cells == [{"student_id": "a", "topic_id": 1}]
    editing.select_student("a", ["a"])
    editing.select_student("removed", ["a"], extend=True)
    assert editing.selected_students == ["a"]


def test_same_card_keeps_edit_and_original_binding_context_until_cleared():
    match = CardMatchDraft()
    match.pick({"uid": "AA", "student_id": "old", "binding_revision": 4})
    match.set("student_id", "new")
    match.set("query", "Иван")
    # A refresh must not replace the optimistic-concurrency context of an edit/retry.
    match.pick({"uid": "AA", "student_id": "another", "binding_revision": 5}, True)
    assert match.snapshot() == {
        "uid": "AA",
        "expected_student_id": "old",
        "binding_revision": 4,
        "owner_name": "",
        "correction": True,
        "student_id": "new",
        "query": "Иван",
    }
    match.clear()
    assert match.snapshot() == {}
    match.pick({"uid": "AA", "student_id": "another", "binding_revision": 5})
    assert match.get("expected_student_id") == "another"
    assert match.get("binding_revision") == 5


def test_different_card_resets_candidate_and_query_with_new_context():
    match = CardMatchDraft()
    match.pick({"uid": "AA", "student_id": "old", "binding_revision": 4}, True)
    match.set("student_id", "candidate")
    match.set("query", "query")
    match.pick({"uid": "BB"})
    assert match.snapshot() == {
        "uid": "BB",
        "expected_student_id": None,
        "binding_revision": 0,
        "owner_name": "",
        "correction": False,
        "student_id": "",
        "query": "",
    }


def test_match_snapshots_and_fields_do_not_expose_mutable_draft_values():
    match = CardMatchDraft()
    value = {"text": ["query"]}
    match.set("query", value)
    value["text"].append("changed")
    snapshot = match.snapshot()
    snapshot["query"]["text"].append("snapshot")
    field = match.get("query")
    field["text"].append("field")
    assert match.get("query") == {"text": ["query"]}
