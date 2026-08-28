"""Whether learners answer what this programme asks them — the arithmetic.

Every case here corresponds to a wrong conclusion someone could draw from a number, not to a line of code:

 - **"Asked and ignored" must not be reported as the same thing as "never asked".** They have opposite
   remedies — rewrite the copy, or find out why the question never left the building — and a rate whose
   denominator is candidates rather than asks hides the second inside the first. This database has 18
   preparations closed by the old sweep and 4 that were asked, so the naive rate would read 0% against a
   denominator that is 82% noise.
 - **A decline is engagement, not silence.** Tapping "Not now" is a learner responding, and averaging it with
   silence loses the only signal that says the ask was seen.
 - **`None` is never `0`.** A programme that has asked nobody has not been ignored.
 - **An answer counts once**, however many times it was asked or revised.

Dates are local-time literals: elapsed days are the arithmetic, and the timezone of the literal is not.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domains.progress.services.adaptive_response_metrics import (
    Funnel,
    NudgeActionRow,
    ReviewAskRow,
    nudge_funnel,
    response_breakdown,
    review_funnel,
    split_by,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _review(**overrides) -> ReviewAskRow:
    return ReviewAskRow(
        **{
            "prep_id": "prep-1",
            "exam_date": NOW - timedelta(days=3),
            "asked_at": NOW - timedelta(days=2),
            "reminders_sent": 0,
            "declined_at": None,
            "answered_at": None,
            "status": "AWAITING_REVIEW",
            **overrides,
        }
    )


def _nudge(**overrides) -> NudgeActionRow:
    return NudgeActionRow(
        **{
            "action": "warned",
            "trigger": "at_risk_due_soon",
            "created_at": NOW - timedelta(days=4),
            "learner_response": None,
            "responded_at": None,
            **overrides,
        }
    )


class TestNeverAskedIsNotIgnored:
    """The distinction the whole module exists for."""

    def test_a_preparation_never_asked_is_counted_apart(self):
        funnel = review_funnel([_review(asked_at=None), _review(asked_at=None)])

        assert funnel.candidates == 2
        assert funnel.never_asked == 2
        # Not in the denominator, and not silent. Silence requires having been asked.
        assert funnel.asked == 0
        assert funnel.silent == 0

    def test_the_rate_is_none_rather_than_zero_when_nothing_was_asked(self):
        """A programme that has asked nobody has not been ignored.

        `0.0` here is the exact misreading this module was built to prevent, and it is what a naive
        `answered / candidates` produces for the state this database is in today.
        """
        funnel = review_funnel([_review(asked_at=None)])

        assert funnel.response_rate is None
        assert funnel.engagement_rate is None

    def test_unasked_candidates_do_not_dilute_the_rate(self):
        """The 18-versus-4 case, in miniature.

        One ask, one answer. Three preparations closed before anyone was ever asked. The rate is 100%,
        because it is a rate of *asks* — and `never_asked` carries the other fact rather than burying it.
        """
        rows = [_review(answered_at=NOW)] + [
            _review(asked_at=None, status="COMPLETED") for _ in range(3)
        ]

        funnel = review_funnel(rows)

        assert funnel.response_rate == 1.0
        assert funnel.never_asked == 3
        assert funnel.candidates == 4


class TestReviewFunnel:
    def test_counts_the_three_outcomes_of_an_ask(self):
        funnel = review_funnel(
            [
                _review(answered_at=NOW),
                _review(declined_at=NOW - timedelta(days=1)),
                _review(),
            ]
        )

        assert (funnel.asked, funnel.answered, funnel.declined, funnel.silent) == (3, 1, 1, 1)

    def test_a_decline_is_engagement_but_not_an_answer(self):
        """The gap between the two rates is the population who saw the ask and chose not to answer.

        A copy problem. The gap between engagement and 1.0 is a delivery problem. Collapsing them into one
        number would point at the wrong team.
        """
        funnel = review_funnel([_review(answered_at=NOW), _review(declined_at=NOW)])

        assert funnel.response_rate == 0.5
        assert funnel.engagement_rate == 1.0

    def test_an_answer_after_a_decline_counts_as_answered(self):
        """The learner changed their mind, which the review surface deliberately allows.

        That is the outcome we wanted; the earlier decline is not a separate learner. Counting it in both
        buckets would make the two rates sum past the number of people asked.
        """
        funnel = review_funnel(
            [_review(declined_at=NOW - timedelta(days=1), answered_at=NOW)]
        )

        assert funnel.answered == 1
        assert funnel.declined == 0

    def test_answered_means_a_recorded_outcome_not_a_completed_status(self):
        """The 18 rows the old date-based sweep closed.

        `COMPLETED` with no outcome behind it. Counting the status as an answer would report a response rate
        for a question that was never asked — the precise lie the whole review flow exists to remove.
        """
        funnel = review_funnel([_review(asked_at=None, status="COMPLETED", answered_at=None)])

        assert funnel.answered == 0
        assert funnel.never_asked == 1


class TestTimeToAnswer:
    def test_measures_from_the_ask_not_from_the_exam(self):
        """The ask is what the learner reacted to.

        Measuring from the exam date would fold the sweep's own latency into the learner's response time, and
        the sweep runs once a night — so a same-day answer would read as a day slower than it was.
        """
        funnel = review_funnel(
            [_review(asked_at=NOW - timedelta(days=2), answered_at=NOW - timedelta(days=1))]
        )

        assert funnel.days_to_answer == [1.0]
        assert funnel.median_days_to_answer == 1.0

    def test_the_median_resists_one_very_late_reply(self):
        """Median rather than mean, and this is the case that decides it.

        Three quick answers and one after six weeks: the mean says a fortnight, the median says a day. Only
        one of those describes what a learner typically does.
        """
        rows = [
            _review(asked_at=NOW - timedelta(days=50), answered_at=NOW - timedelta(days=49)),
            _review(asked_at=NOW - timedelta(days=50), answered_at=NOW - timedelta(days=49)),
            _review(asked_at=NOW - timedelta(days=50), answered_at=NOW - timedelta(days=49)),
            _review(asked_at=NOW - timedelta(days=50), answered_at=NOW - timedelta(days=8)),
        ]

        funnel = review_funnel(rows)

        assert funnel.median_days_to_answer == 1.0
        assert sum(funnel.days_to_answer) / len(funnel.days_to_answer) > 10

    def test_is_none_with_no_answers_rather_than_zero(self):
        assert review_funnel([_review()]).median_days_to_answer is None

    def test_averages_the_middle_pair_on_an_even_count(self):
        rows = [
            _review(asked_at=NOW - timedelta(days=10), answered_at=NOW - timedelta(days=8)),
            _review(asked_at=NOW - timedelta(days=10), answered_at=NOW - timedelta(days=6)),
        ]

        assert review_funnel(rows).median_days_to_answer == 3.0

    def test_reads_a_naive_timestamp_without_raising(self):
        """Several of these columns are stored without an offset while the ORM declares otherwise.

        A subtraction with one naive side raises `TypeError`, which is the defect that made
        `GET /progress/goals` a 500 for any goal with a target date.
        """
        funnel = review_funnel(
            [
                _review(
                    asked_at=datetime(2026, 8, 26, 12, 0),
                    answered_at=datetime(2026, 8, 27, 12, 0),
                )
            ]
        )

        assert funnel.days_to_answer == [1.0]


class TestNudgeFunnel:
    def test_every_action_is_an_ask(self):
        """`candidates == asked`, and `never_asked` is always zero.

        A row exists only because the pass decided something, so unlike the review there is no population
        here we failed to ask. The field stays in the shape so the two funnels remain comparable, and its
        being zero is the honest statement rather than a missing measurement.
        """
        funnel = nudge_funnel([_nudge(), _nudge()])

        assert (funnel.candidates, funnel.asked, funnel.never_asked) == (2, 2, 0)

    def test_counts_answers_and_silence(self):
        funnel = nudge_funnel(
            [
                _nudge(learner_response="keep_going", responded_at=NOW - timedelta(days=3)),
                _nudge(),
                _nudge(),
            ]
        )

        assert (funnel.answered, funnel.silent) == (1, 2)
        assert funnel.response_rate == pytest.approx(1 / 3)

    def test_nothing_is_ever_declined(self):
        """The nudge has three answers and none of them is "not now".

        Its dialog is dismissible and a dismissal writes nothing, so `silent` here mixes "saw it and closed
        it" with "never saw it" — which the review funnel can separate and this one cannot. Asserted so the
        two response rates are never compared as though their denominators were equally clean.
        """
        funnel = nudge_funnel([_nudge(), _nudge(learner_response="set_aside", responded_at=NOW)])

        assert funnel.declined == 0
        assert funnel.engagement_rate == funnel.response_rate


class TestTheCutPhaseEightNeeds:
    def test_splits_by_rung_so_a_useless_one_is_visible(self):
        """A single overall rate cannot say which rung is worth keeping.

        An `asked_to_confirm` answered half the time beside a `warned` answered never is a very different
        programme from two rungs at 25%, and only the split can tell them apart.
        """
        rows = [
            _nudge(action="asked_to_confirm", learner_response="keep_going", responded_at=NOW),
            _nudge(action="asked_to_confirm"),
            _nudge(action="warned"),
            _nudge(action="warned"),
            _nudge(action="warned"),
        ]

        by_action = split_by(rows, "action")

        assert by_action["asked_to_confirm"].response_rate == 0.5
        assert by_action["warned"].response_rate == 0.0
        # Ordered by volume, so the rungs that actually fire lead.
        assert list(by_action) == ["warned", "asked_to_confirm"]

    def test_splits_by_trigger_too(self):
        rows = [
            _nudge(trigger="deadline_passed", learner_response="already_done", responded_at=NOW),
            _nudge(trigger="at_risk_due_soon"),
        ]

        by_trigger = split_by(rows, "trigger")

        assert by_trigger["deadline_passed"].response_rate == 1.0
        assert by_trigger["at_risk_due_soon"].response_rate == 0.0

    def test_a_zero_rate_is_zero_not_none_once_something_was_asked(self):
        """The mirror of the `None` rule. Asked three times and heard nothing is a real 0%."""
        assert nudge_funnel([_nudge(), _nudge(), _nudge()]).response_rate == 0.0


class TestResponseBreakdown:
    def test_counts_each_answer_commonest_first(self):
        rows = [
            _nudge(learner_response="keep_going", responded_at=NOW),
            _nudge(learner_response="keep_going", responded_at=NOW),
            _nudge(learner_response="already_done", responded_at=NOW),
            _nudge(),
        ]

        assert response_breakdown(rows) == {"keep_going": 2, "already_done": 1}

    def test_is_empty_rather_than_zero_filled_when_nobody_answered(self):
        """An empty mapping says "no answers". Zeros for all three would imply the answers were observed."""
        assert response_breakdown([_nudge(), _nudge()]) == {}


class TestFunnelDefaults:
    def test_an_empty_funnel_reports_nothing_rather_than_zero(self):
        funnel = Funnel()

        assert funnel.response_rate is None
        assert funnel.engagement_rate is None
        assert funnel.median_days_to_answer is None
