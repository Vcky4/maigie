"""Bug Hunt — Pydantic request/response models.

camelCase on the wire, matching the rest of this API and the generated client types. Responses are built
field by field in `routes.py` rather than via `from_attributes`, because the ORM attributes are
snake_case and attribute coercion would silently drop every timestamp — the same reason
`admin/routes.py:_user_response` exists.

**Money is always `…Kobo`, always an integer.** The suffix is not decoration: it is the only thing
stopping a client from rendering ₦200,000 where ₦2,000 was meant. Nothing in this contract carries a
float or a formatted currency string.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Seasons — public
# ---------------------------------------------------------------------------


class RewardTier(BaseModel):
    """One row of the published reward table."""

    category: str
    severity: str
    amountKobo: int


class SeasonSummary(BaseModel):
    """A season as the public site sees it.

    Carries its **own** reward table rather than referring to a global one, so a closed season keeps
    showing what it actually paid after a later season changes the amounts.
    """

    id: str
    seasonNumber: int
    slug: str
    name: str
    status: str
    startsAt: datetime
    endsAt: datetime
    rewards: list[RewardTier]
    countryAllowlist: list[str]
    perParticipantCapKobo: int
    minWithdrawalKobo: int
    passUpliftPercent: int
    rulesVersion: int
    submissionDailyLimit: int


class ProgramStateResponse(BaseModel):
    """What `GET /program` answers, in all three of the states the landing page has to render.

    `state` is the discriminator the app switches on, and it exists so the client never has to infer a
    page from a null: `open` shows the season, `scheduled` shows a date, `between` shows the programme
    and a wallet link for anyone still holding a balance. Getting this wrong is how a returning tester
    who followed a link from a friend lands on a dead page the day after a season ends.
    """

    state: str = Field(description="open | scheduled | between")
    season: SeasonSummary | None = None
    nextSeason: SeasonSummary | None = None
    #: Highest season number ever run, so copy can say "Season 2 is coming" without a second request.
    latestSeasonNumber: int | None = None


class SeasonListResponse(BaseModel):
    seasons: list[SeasonSummary]


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


class ParticipationView(BaseModel):
    """One person's participation in one season."""

    id: str
    programId: str
    seasonNumber: int
    seasonName: str
    seasonStatus: str
    status: str
    attemptCount: int
    carriedForward: bool
    acceptedRulesVersion: int | None = None
    rejectionReason: str | None = None
    createdAt: datetime
    decidedAt: datetime | None = None


class EligibilityView(BaseModel):
    """Why the caller can or cannot take part right now.

    `reasonCode` mirrors the exception codes in `exceptions.py`, so the app renders the same copy whether
    it learned about the refusal by asking up front or by being refused on submit. Two sources of truth
    for that message is how they drift.
    """

    eligible: bool
    country: str | None = None
    reasonCode: str | None = None


class MeResponse(BaseModel):
    """The single call the participant app boots on.

    Deliberately one round trip covering the season, the caller's standing in it, their history and
    their wallet summary — because the app's first decision is *which screen to show*, and four requests
    to answer that is four chances to render a flash of the wrong one.
    """

    state: str
    season: SeasonSummary | None = None
    eligibility: EligibilityView
    #: Participation in the currently open season, if any.
    participation: ParticipationView | None = None
    #: Every season the caller has taken part in, newest first.
    history: list[ParticipationView] = []
    #: `null` until Phase 4 lands the ledger. Present in the contract from the start so the app's shape
    #: does not change when it does.
    wallet: WalletSummary | None = None
    #: True when the caller is approved but owes an acknowledgement of this season's terms.
    needsTermsAcceptance: bool = False


class WalletSummary(BaseModel):
    """Lifetime, not per season — the wallet belongs to the person (§6.1).

    `openWithdrawalKobo` is reported alongside the balance rather than folded into it, even though the debit
    was written when the request was made. A tester waiting on a ₦1,500 transfer should see a balance of ₦0
    *and* ₦1,500 on its way, not one number that could mean either.
    """

    balanceKobo: int
    lifetimeAwardedKobo: int
    openWithdrawalKobo: int
    earnedThisSeasonKobo: int
    capRemainingKobo: int


class LedgerEntryView(BaseModel):
    """One line of the wallet's own explanation of its number.

    `seasonNumber` is present on credits and `null` on spends, which is not a gap: an award belongs to the
    season that earned it, and a redemption or withdrawal belongs to no season. The client groups on it.
    """

    id: str
    kind: str
    amountKobo: int
    seasonNumber: int | None = None
    submissionId: str | None = None
    withdrawalId: str | None = None
    passId: str | None = None
    note: str | None = None
    createdAt: datetime


class LedgerResponse(BaseModel):
    entries: list[LedgerEntryView]
    total: int
    page: int
    pageSize: int
    hasMore: bool
    balanceKobo: int


class AdjustmentRequest(BaseModel):
    """A super admin's correction — the only free-typed amount in the programme.

    Either sign. The note is mandatory because the tester can read this ledger, and an unexplained line on
    it is worse than no line. Attributed to the current season, so it counts against that tester's cap —
    an adjustment that sidestepped the cap would make the cap advisory for anyone holding this permission.
    """

    amountKobo: int
    note: str = Field(min_length=1, max_length=500)


class AdjustmentResponse(BaseModel):
    entry: LedgerEntryView
    balanceKobo: int


# ---------------------------------------------------------------------------
# Seasons — admin
# ---------------------------------------------------------------------------


class SeasonAdminView(SeasonSummary):
    """A season as staff see it: the public shape plus the money and the cohort."""

    budgetKobo: int
    awardedKobo: int
    remainingBudgetKobo: int
    participantCounts: dict[str, int] = {}
    rewardMatrix: dict[str, dict[str, int]] = {}


class SeasonAdminListResponse(BaseModel):
    seasons: list[SeasonAdminView]


class SeasonDefaultsResponse(BaseModel):
    """The season editor's initial values, copied from the previous season.

    Served rather than computed in the browser so that "Season 2 starts from Season 1's numbers" is one
    rule in one place, and the admin app cannot drift into inventing its own defaults.
    """

    seasonNumber: int
    budgetKobo: int
    perParticipantCapKobo: int
    minWithdrawalKobo: int
    passUpliftPercent: int
    submissionDailyLimit: int
    countryAllowlist: list[str]
    rewardMatrix: dict[str, dict[str, int]]
    rulesVersion: int
    previousSeasonNumber: int | None = None


class SeasonCreateRequest(BaseModel):
    """Create a season. Everything optional is defaulted from the previous season.

    The four required fields are the four a human must decide: what it is called, how it is addressed,
    and when it runs.
    """

    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    startsAt: datetime
    endsAt: datetime
    seasonNumber: int | None = Field(default=None, ge=1)
    budgetKobo: int | None = Field(default=None, ge=0)
    perParticipantCapKobo: int | None = Field(default=None, gt=0)
    minWithdrawalKobo: int | None = Field(default=None, gt=0)
    passUpliftPercent: int | None = Field(default=None, ge=0, le=90)
    submissionDailyLimit: int | None = Field(default=None, gt=0)
    countryAllowlist: list[str] | None = None
    rewardMatrix: dict[str, dict[str, int]] | None = None
    rulesVersion: int | None = Field(default=None, ge=1)


class SeasonUpdateRequest(BaseModel):
    """Patch a season's configuration. `status` is absent by design — see `SeasonTransition`."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    startsAt: datetime | None = None
    endsAt: datetime | None = None
    budgetKobo: int | None = Field(default=None, ge=0)
    perParticipantCapKobo: int | None = Field(default=None, gt=0)
    minWithdrawalKobo: int | None = Field(default=None, gt=0)
    passUpliftPercent: int | None = Field(default=None, ge=0, le=90)
    submissionDailyLimit: int | None = Field(default=None, gt=0)
    countryAllowlist: list[str] | None = None
    rewardMatrix: dict[str, dict[str, int]] | None = None
    rulesVersion: int | None = Field(default=None, ge=1)


class CarryForwardRequest(BaseModel):
    """Seed a previous season's approved participants into this one.

    `fromProgramId` is explicit rather than "the previous season" so that a gap year, a cancelled season
    or a re-run cannot silently pull the wrong cohort.
    """

    fromProgramId: str


class CarryForwardPreviewResponse(BaseModel):
    """How many would be added. Shown before the button commits anything."""

    fromProgramId: str
    intoProgramId: str
    count: int


class CarryForwardResponse(BaseModel):
    fromProgramId: str
    intoProgramId: str
    added: int


# ---------------------------------------------------------------------------
# Triage — admin
# ---------------------------------------------------------------------------


class ParticipantAdminView(BaseModel):
    """A participant in the review queue.

    Carries the applicant's email and name from the identity join, because a queue of opaque ids is not a
    queue anybody can work.
    """

    id: str
    programId: str
    seasonNumber: int
    userId: str
    email: str
    name: str | None = None
    status: str
    attemptCount: int
    carriedForward: bool
    acceptedRulesVersion: int | None = None
    rejectionReason: str | None = None
    createdAt: datetime
    decidedAt: datetime | None = None


class ParticipantAdminListResponse(BaseModel):
    participants: list[ParticipantAdminView]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class ParticipantAdminDetail(BaseModel):
    """One participant, with the cross-season record that answers "is this a good reporter".

    `earnedLifetimeKobo` spans every season and `earnedThisSeasonKobo` does not — the difference is the whole
    point of a per-user wallet, and a returning applicant's history is the most useful thing on this screen.
    """

    participant: ParticipantAdminView
    country: str | None = None
    submissionCounts: dict[str, int] = {}
    earnedThisSeasonKobo: int
    earnedLifetimeKobo: int
    history: list[ParticipationView] = []


class ParticipantDecisionRequest(BaseModel):
    """Approve or reject. A rejection needs a reason, and the applicant reads it verbatim."""

    decision: str = Field(description="approve | reject")
    reason: str | None = Field(default=None, max_length=2000)


class ParticipantSuspendRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class SubmissionAdminView(BaseModel):
    """A finding as staff see it: the reporter's view plus the private half and the identity join."""

    id: str
    programId: str
    seasonNumber: int
    participantId: str | None = None
    userId: str | None = None
    email: str | None = None
    name: str | None = None
    platform: str
    appVersion: str | None = None
    buildNumber: str | None = None
    deviceModel: str | None = None
    osVersion: str | None = None
    route: str | None = None
    title: str
    stepsToReproduce: str
    expectedResult: str
    actualResult: str
    reportedSeverity: str | None = None
    category: str | None = None
    type: str | None = None
    severity: str | None = None
    status: str
    isApplication: bool
    duplicateOfId: str | None = None
    publicResponse: str | None = None
    #: Staff-only, and the reason this model exists separately from `SubmissionView`.
    adminNotes: str | None = None
    awardKobo: int | None = None
    attachments: list[AttachmentView] = []
    createdAt: datetime
    triagedAt: datetime | None = None
    triagedByUserId: str | None = None


class SubmissionAdminListResponse(BaseModel):
    submissions: list[SubmissionAdminView]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class SubmissionAdminDetail(BaseModel):
    """Everything needed to grade one finding on one screen.

    `rewardMatrix` is the **submission's own season's** table, not the current one, so a triager grading a
    late Season 1 finding sees Season 1's amounts. `reporterSubmissionCount` and `duplicateOfTitle` answer
    the two questions that would otherwise be two more page loads each.
    """

    submission: SubmissionAdminView
    reporterSubmissionCount: int
    duplicateOfTitle: str | None = None
    rewardMatrix: dict[str, dict[str, int]] = {}


class TriageRequest(BaseModel):
    """Grade a finding.

    **There is no amount field, and that is the design.** A triager sets category and severity; the season's
    matrix decides the kobo. Off-matrix money is an `adjustment` and needs a super admin, so that every
    figure on the reward path traces to a published rule rather than to a keystroke.
    """

    status: str = Field(description="in_review | accepted | rejected | duplicate | known_issue")
    category: str | None = Field(default=None, description="bug | feedback")
    type: str | None = None
    severity: str | None = Field(
        default=None, description="bug: critical|high|medium|low · feedback: high_value|standard"
    )
    #: Required for `duplicate` and `known_issue`. Not constrained to the same season — a Season 2 report of
    #: an unfixed Season 1 bug points back across the boundary, which is what `known_issue` is for.
    duplicateOfId: str | None = None
    #: Shown to the reporter verbatim.
    publicResponse: str | None = Field(default=None, max_length=4000)
    #: Never shown to the reporter.
    adminNotes: str | None = Field(default=None, max_length=4000)


class TriageResponse(BaseModel):
    """The graded finding, what it was worth, and what was actually paid.

    Two amounts, because they can differ and the difference matters. `matrixKobo` is what the season's table
    says the grading is worth; `awardKobo` is what reached the ledger. When they differ, `awardBlocked`
    names why — `cap_reached`, `budget_exhausted`, `already_awarded` or `not_priced` — and `awardMessage`
    says it in words a triager can act on.

    A single amount could not express "accepted, owed ₦2,000, paid nothing because the season is out of
    budget", and that is precisely the state an operator must be able to see.
    """

    submission: SubmissionAdminView
    awardKobo: int
    matrixKobo: int
    awardBlocked: str | None = None
    awardMessage: str | None = None


class KnownIssueView(BaseModel):
    """An accepted finding from an earlier season, for marking a repeat.

    `seasonNumber` is on the row because "reported in Season 1 and still open" is the sentence a triager
    needs, and it is the sentence that keeps the blame for our backlog off the reporter.
    """

    id: str
    seasonNumber: int
    platform: str
    title: str
    severity: str | None = None
    category: str | None = None
    createdAt: datetime


class KnownIssueListResponse(BaseModel):
    knownIssues: list[KnownIssueView]


class TriageStatsResponse(BaseModel):
    """The morning read for whoever owns the queue.

    `acceptanceRate` and `medianTriageHours` are `null` rather than `0` before anything has been decided. A
    displayed acceptance rate of zero reads as "we reject everything", which on day one is both false and
    the worst possible thing to show.
    """

    seasonId: str | None = None
    seasonNumber: int | None = None
    seasonStatus: str | None = None
    queues: dict[str, int] = {}
    submissionsByStatus: dict[str, int] = {}
    submissionsByPlatform: dict[str, int] = {}
    submissionsBySeverity: dict[str, int] = {}
    participantCounts: dict[str, int] = {}
    budgetKobo: int
    awardedKobo: int
    remainingBudgetKobo: int
    acceptanceRate: float | None = None
    medianTriageHours: float | None = None


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


class AttachmentView(BaseModel):
    id: str
    url: str
    contentType: str
    sizeBytes: int
    createdAt: datetime


class SubmissionFields(BaseModel):
    """What a tester writes. Shared by the application and by every later submission.

    Note what is **absent**: `category`, `severity`, `status` and `publicResponse`. Those belong to triage,
    and a submitter who could set their own severity could set their own payment. `reportedSeverity` is
    their guess, kept deliberately separate and never used for money.

    The four required text fields are the structure that makes a report reproducible. A single free-text
    box would collect "the app is broken", and a triager cannot grade that or pay for it.
    """

    platform: str = Field(description="web | android | ios")
    title: str = Field(min_length=4, max_length=200)
    stepsToReproduce: str = Field(min_length=10, max_length=5000)
    expectedResult: str = Field(min_length=3, max_length=2000)
    actualResult: str = Field(min_length=3, max_length=2000)
    #: Version and device, because "it crashes" on an eighteen-month-old build is a different finding from
    #: the same words on the current one. Optional, since a web tester has no build number and guessing
    #: would be worse than a null.
    appVersion: str | None = Field(default=None, max_length=40)
    buildNumber: str | None = Field(default=None, max_length=40)
    deviceModel: str | None = Field(default=None, max_length=120)
    osVersion: str | None = Field(default=None, max_length=60)
    #: The route or screen, as the tester describes it. Not a URL: a mobile screen has no URL.
    route: str | None = Field(default=None, max_length=300)
    reportedSeverity: str | None = Field(
        default=None, description="critical | high | medium | low — the tester's own estimate"
    )


class ApplicationCreateRequest(SubmissionFields):
    """Register by submitting a first finding.

    `acceptedRulesVersion` is the version the applicant actually read, echoed back so the server can
    refuse consent to terms it has since replaced. Sending the current version blind would record an
    agreement to amounts they were never shown.
    """

    acceptTerms: bool = Field(description="Must be true. Covers 18+, eligibility, tax and IP.")
    acceptedRulesVersion: int = Field(ge=1)


class SubmissionCreateRequest(SubmissionFields):
    pass


class TermsAcceptanceRequest(BaseModel):
    """A returning participant's acknowledgement of this season's terms."""

    accept: bool
    rulesVersion: int = Field(ge=1)


class SubmissionView(BaseModel):
    """A finding as its reporter sees it.

    `awardKobo` is **joined from the ledger**, not stored here. `null` means not yet awarded, which the
    dashboard shows differently from an award of `0` — the first is "still being reviewed", the second is
    "reviewed, and this one does not pay". Collapsing them would tell a tester their accepted finding was
    worthless while it was still in the queue.

    `adminNotes` is absent by construction. `publicResponse` is the half a tester reads.
    """

    id: str
    programId: str
    seasonNumber: int
    platform: str
    appVersion: str | None = None
    buildNumber: str | None = None
    deviceModel: str | None = None
    osVersion: str | None = None
    route: str | None = None
    title: str
    stepsToReproduce: str
    expectedResult: str
    actualResult: str
    reportedSeverity: str | None = None
    category: str | None = None
    type: str | None = None
    severity: str | None = None
    status: str
    isApplication: bool
    duplicateOfId: str | None = None
    publicResponse: str | None = None
    awardKobo: int | None = None
    attachments: list[AttachmentView] = []
    createdAt: datetime
    triagedAt: datetime | None = None


class SubmissionListResponse(BaseModel):
    submissions: list[SubmissionView]
    total: int
    page: int
    pageSize: int
    hasMore: bool


class ApplicationResponse(BaseModel):
    """The result of applying: where you stand, and the finding that got you there."""

    participation: ParticipationView
    submission: SubmissionView


# Deferred annotation resolution: `MeResponse` refers to `WalletSummary`, which is declared after it so
# the participant-facing models read in the order the app consumes them.
MeResponse.model_rebuild()
