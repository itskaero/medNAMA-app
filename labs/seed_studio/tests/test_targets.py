"""Target arithmetic: official counts x multiplier, derived shares, minimums, overrides, internal bases."""

from labs.seed_studio import targets as T


def node(id, parent, level, name, count=None, **kw):
    return {"id": id, "parent_id": parent, "level": level, "name": name, "position": id, "official_count": count,
            "min_per_child": kw.get("min_per_child"), "target_override": kw.get("override")}


def test_split_even_and_difficulty_sum_exactly():
    assert T.split_even(10, 3) == [4, 3, 3]
    assert sum(T.split_even(7, 4)) == 7
    for total in (0, 1, 7, 13, 100, 999):
        alloc = T.allocate_difficulty(total)
        assert sum(alloc.values()) == total
        assert set(alloc) == {1, 2, 3, 4, 5}
    assert T.allocate_difficulty(100) == {1: 10, 2: 25, 3: 35, 4: 20, 5: 10}
    assert T.allocate_difficulty(10, {3: 100}) == {1: 0, 2: 0, 3: 10, 4: 0, 5: 0}


def test_subject_count_split_over_topics_with_printed_minimum():
    # Anatomy: 20 MCQs, "minimum of two MCQs from each topic", 6 topics, at 5x -> 100 across topics, each >= 10
    nodes = [node(1, None, "family", "PGET"), node(2, 1, "paper", "Paper I (MBBS)", 100),
             node(3, 2, "subject", "Anatomy", 20, min_per_child=2)]
    nodes += [node(10 + i, 3, "topic", f"T{i}") for i in range(6)]
    plans = T.compute(nodes, multiplier=5)
    topic_plans = [plans[10 + i] for i in range(6)]
    assert sum(p.planned for p in topic_plans) == 100
    assert all(p.planned >= 10 and p.source == "derived" for p in topic_plans)
    assert plans[3].planned == 100 and plans[3].source == "rollup"


def test_stated_topic_counts_keep_theirs_and_the_rest_share_the_remainder():
    # Physiology 19: Neurophysiology 3, Excitable tissue 2, three more topics with no count
    nodes = [node(1, None, "family", "PGET"), node(3, 1, "subject", "Physiology", 19),
             node(4, 3, "topic", "Neurophysiology", 3), node(5, 3, "topic", "Excitable", 2),
             node(6, 3, "topic", "A"), node(7, 3, "topic", "B"), node(8, 3, "topic", "C")]
    plans = T.compute(nodes, multiplier=5)
    assert plans[4].planned == 15 and plans[4].source == "official"
    assert plans[5].planned == 10 and plans[5].source == "official"
    rest = [plans[i].planned for i in (6, 7, 8)]
    assert sum(rest) == 95 - 25 and max(rest) - min(rest) <= 1
    assert plans[3].planned == 95


def test_expanded_multiplier_doubles_the_bank_target():
    nodes = [node(1, None, "family", "PGET"), node(2, 1, "topic", "Shock", 4)]
    assert T.compute(nodes, multiplier=5)[2].planned == 20
    assert T.compute(nodes, multiplier=10)[2].planned == 40


def test_no_counts_anywhere_uses_the_internal_base_and_says_so():
    nodes = [node(1, None, "family", "FCPS"), node(2, 1, "subject", "Anatomy"), node(3, 2, "topic", "Upper limb"),
             node(4, 2, "topic", "Thorax")]
    plans = T.compute(nodes, multiplier=5, base_per_topic=2)
    assert plans[3].planned == 10 and plans[3].source == "internal"


def test_override_wins_and_counts_below_a_topic_are_summed():
    nodes = [node(1, None, "family", "PGET"), node(2, 1, "topic", "Shock", 4, override=7),
             node(3, 1, "topic", "Sepsis"), node(4, 3, "subtopic", "qSOFA", 1), node(5, 3, "subtopic", "Lactate", 2)]
    plans = T.compute(nodes, multiplier=5)
    assert plans[2].planned == 7 and plans[2].source == "override"
    assert plans[3].planned == 15 and plans[3].source == "official"


def test_units_leaves_and_batches():
    nodes = [node(1, None, "family", "PGET"), node(2, 1, "subject", "Anatomy", 4), node(3, 2, "topic", "Limb"),
             node(4, 3, "subtopic", "Plexus"), node(5, 3, "subtopic", "Joints"), node(6, 2, "topic", "Thorax")]
    assert [u["id"] for u in T.units_under(nodes, 1)] == [3, 6]
    assert [n["id"] for n in T.note_leaves_under(nodes, 1)] == [4, 5, 6]
    assert T.subtopics_of(nodes, 3) == ["Plexus", "Joints"]
    batches = T.batches_for({1: 0, 2: 3, 3: 12, 4: 0, 5: 1}, 5)
    assert batches == [(2, 0, 3), (3, 0, 5), (3, 1, 5), (3, 2, 2), (5, 0, 1)]
    assert sum(b[2] for b in batches) == 16
