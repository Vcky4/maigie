"""
Admin domain — Pydantic request/response schemas.

Platform administration, content management, staff operations.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminUserResponse(BaseModel):
    """User record as seen by admin."""

    id: str
    email: str
    name: str | None = None
    tier: str
    role: str
    isActive: bool
    isOnboarded: bool = False
    adminStaffRole: str | None = None
    createdAt: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminStatsResponse(BaseModel):
    """Platform-level statistics."""

    totalUsers: int
    activeUsers: int
    premiumUsers: int
    totalCourses: int
    totalSpaces: int
    totalMessages: int


# ===========================================================================
# User management
# ===========================================================================


class UserAdminResponse(BaseModel):
    """A user as the admin dashboard sees them.

    Deliberately omits the retired credit columns (`creditsUsed`, `creditsHardCap`, …). They are
    optional in the client type and no longer exist on ``User`` — the commercial model replaced credit
    caps with a usage window and passes (`MAIGIE_PLUS_COMMERCIAL_PLAN.md`), and the entitlement view is
    where that state now lives. See Decision 5.
    """

    id: str
    email: str
    name: str | None = None
    tier: str
    role: str
    adminStaffRole: str | None = None
    isActive: bool
    isOnboarded: bool = False
    createdAt: datetime
    updatedAt: datetime


class UserAdminListResponse(BaseModel):
    """Paginated user list."""

    users: list[UserAdminResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


class UserAdminCreateRequest(BaseModel):
    """Create a user from the admin tool."""

    email: str
    name: str | None = None
    password: str | None = None
    tier: str | None = None
    role: str | None = None
    adminStaffRole: str | None = None
    isActive: bool | None = None
    isOnboarded: bool | None = None


class UserAdminUpdateRequest(BaseModel):
    """Update a user's editable fields."""

    name: str | None = None
    email: str | None = None
    tier: str | None = None
    role: str | None = None
    adminStaffRole: str | None = None
    isActive: bool | None = None
    isOnboarded: bool | None = None


# ===========================================================================
# Entitlements (reshaped from "credits" — Decision 5)
# ===========================================================================


class AdminPassView(BaseModel):
    """One Plus pass held by the learner, in whatever state."""

    id: str
    productId: str
    status: str
    unitsAllowance: int
    unitsUsed: int
    durationMinutes: int
    source: str
    activatedAt: datetime | None = None
    expiresAt: datetime | None = None
    endedReason: str | None = None
    createdAt: datetime


class AdminEntitlementView(BaseModel):
    """A learner's personal entitlement as staff see it — the successor to the credits panel.

    Every field is read from the one authority (`entitlement_service.resolve`) plus the billing
    domain's own voice and points services, so the tool cannot disagree with what the learner is
    served (Decision 2, honesty invariant). Usage is shown as raw window figures here because this is
    an operational view, not the learner's percentage-and-reset-time surface.
    """

    tier: str
    source: str
    expiresAt: datetime | None = None
    isTrial: bool = False
    trialDaysRemaining: int | None = None
    subscriptionTier: str | None = None

    windowAllowance: int
    windowUnitsUsed: int
    windowStartedAt: datetime | None = None
    monthlyBackstop: int | None = None
    monthlyUnitsUsed: int

    voiceAvailable: bool
    voiceSecondsRemaining: int
    voiceSecondsPurchased: int
    voiceMinutesIncluded: int

    pointsBalance: int

    activePassId: str | None = None
    passes: list[AdminPassView] = []


class GrantPassRequest(BaseModel):
    """Grant a complimentary (inventory) pass to a learner.

    Grants into inventory only — it does not activate. The learner starts the clock when they choose
    to, exactly as a purchased pass behaves (`pass_service`). ``reason`` is recorded in the audit trail.
    """

    productId: str
    reason: str | None = None


class StaffRoleUpdateRequest(BaseModel):
    """Update a user's staff role."""

    userId: str
    staffRole: str  # SUPER_ADMIN | CONTENT_MANAGER


# ===========================================================================
# Analytics (Phase 2 — honest, real-row metrics only)
# ===========================================================================


class PlatformStatistics(BaseModel):
    """Platform-wide counts. Every field traces to a COUNT/SUM/AVG over real rows."""

    totalUsers: int
    activeUsers: int
    totalCourses: int
    activeCourses: int
    archivedCourses: int
    totalModules: int
    totalTopics: int
    completedTopics: int
    totalEstimatedHours: float
    completedEstimatedHours: float
    averageCourseProgress: float
    averageUserProgress: float
    usersByTier: dict[str, int]
    coursesByDifficulty: dict[str, int]
    aiGeneratedCourses: int
    manualCourses: int


class UserAnalyticsItem(BaseModel):
    """A learner's course rollup. Understanding, not a verdict (`ch16`, `ch14-behaviour`)."""

    userId: str
    email: str
    name: str | None = None
    tier: str
    totalCourses: int
    activeCourses: int
    completedCourses: int
    totalTopics: int
    completedTopics: int
    overallProgress: float
    createdAt: datetime


class CourseAnalyticsItem(BaseModel):
    """One course with its module/topic completion, attributed to its owner."""

    courseId: str
    title: str
    userId: str
    userEmail: str
    userName: str | None = None
    progress: float
    totalTopics: int
    completedTopics: int
    totalModules: int
    completedModules: int
    difficulty: str
    isAIGenerated: bool
    isArchived: bool
    createdAt: datetime


class AdminAnalyticsResponse(BaseModel):
    """Platform analytics landing payload."""

    platformStats: PlatformStatistics
    topUsers: list[UserAnalyticsItem]
    topCourses: list[CourseAnalyticsItem]
    recentCourses: list[CourseAnalyticsItem]


class UserProgressSummary(BaseModel):
    """One learner's aggregate progress across their courses."""

    userId: str
    totalCourses: int
    activeCourses: int
    completedCourses: int
    archivedCourses: int
    totalModules: int
    completedModules: int
    totalTopics: int
    completedTopics: int
    overallProgress: float
    totalEstimatedHours: float
    completedEstimatedHours: float
    averageCourseProgress: float


class UserDetailAnalyticsResponse(BaseModel):
    """Per-user analytics for the user-detail page."""

    user: UserAnalyticsItem
    courses: list[CourseAnalyticsItem]
    summary: UserProgressSummary


# ===========================================================================
# Re-engagement (Phase 3 — never-guilt, consent-gated)
# ===========================================================================


class AtRiskUser(BaseModel):
    """A learner who has been inactive, described plainly — not judged (`ch16`, `ch14-behaviour`)."""

    userId: str
    email: str
    name: str | None = None
    tier: str
    daysInactive: int
    currentStreak: int
    longestStreak: int
    lastActivity: str
    signupDate: str
    riskLevel: str  # "high" | "medium" | "low"


class RiskCounts(BaseModel):
    high: int
    medium: int
    low: int


class UsersAtRiskResponse(BaseModel):
    """Inactive learners, most-inactive first. A read; sending anything is a separate, gated action."""

    users: list[AtRiskUser]
    total: int
    riskCounts: RiskCounts


class WakeRequest(BaseModel):
    """Ask to send one learner a gentle return nudge.

    ``customMessage`` overrides only the body copy; if omitted the guilt-free default is used.
    ``regenerateSchedule`` is accepted for client compatibility but not acted on here — schedule
    regeneration is a personal-learning concern, deferred (see `docs/ADMIN_DASHBOARD_PLAN.md`).
    """

    customMessage: str | None = None
    regenerateSchedule: bool | None = None


class WakeResponse(BaseModel):
    status: str
    userId: str
    notificationId: str | None = None


class WakeBulkResponse(BaseModel):
    total: int
    sent: int
    failed: int


class DeepWakeConfigUpdateRequest(BaseModel):
    max_inactive_days: int


class RegenerateSchedulesRequest(BaseModel):
    max_users: int = 100
    only_inactive_days: int | None = None


# ===========================================================================
# System — AI task / action-log reads (Phase 4)
# ===========================================================================


class AiAgentTaskItem(BaseModel):
    id: str
    userId: str
    taskType: str
    status: str
    priority: int
    title: str
    message: str
    actionData: dict | None = None
    scheduledAt: datetime
    sentAt: datetime | None = None
    dismissedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime


class AiAgentTaskListResponse(BaseModel):
    items: list[AiAgentTaskItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


class AiActionLogItem(BaseModel):
    id: str
    messageId: str
    userId: str | None = None
    userName: str | None = None
    userEmail: str | None = None
    actionType: str
    actionData: dict | None = None
    status: str
    error: str | None = None
    createdAt: datetime


class AiActionLogListResponse(BaseModel):
    logs: list[AiActionLogItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ===========================================================================
# Courses (admin view over the knowledge domain)
# ===========================================================================


class AdminCourseItem(BaseModel):
    id: str
    userId: str
    userEmail: str
    userName: str | None = None
    title: str
    description: str | None = None
    difficulty: str
    isAIGenerated: bool
    archived: bool
    progress: float
    totalTopics: int
    completedTopics: int
    moduleCount: int
    createdAt: datetime
    updatedAt: datetime


class AdminCourseListResponse(BaseModel):
    courses: list[AdminCourseItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


class AdminTopicItem(BaseModel):
    id: str
    title: str
    content: str | None = None
    order: float
    completed: bool
    estimatedHours: float | None = None
    createdAt: datetime


class AdminModuleItem(BaseModel):
    id: str
    title: str
    description: str | None = None
    order: float
    completed: bool
    progress: float
    totalTopics: int
    completedTopics: int
    topics: list[AdminTopicItem]


class AdminCourseDetail(BaseModel):
    id: str
    userId: str
    userEmail: str
    userName: str | None = None
    title: str
    description: str | None = None
    difficulty: str
    targetDate: datetime | None = None
    isAIGenerated: bool
    archived: bool
    progress: float
    totalTopics: int
    completedTopics: int
    modules: list[AdminModuleItem]
    createdAt: datetime
    updatedAt: datetime


# ===========================================================================
# Staff
# ===========================================================================


class StaffMember(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str
    adminStaffRole: str | None = None
    isActive: bool


class StaffRoleUpdateBody(BaseModel):
    adminStaffRole: str


# ===========================================================================
# Referrals (reshaped onto the points model — Decision 5)
# ===========================================================================


class ReferralItem(BaseModel):
    id: str
    referrerId: str
    referrerEmail: str | None = None
    referrerName: str | None = None
    referredUserId: str | None = None
    referredUserEmail: str | None = None
    referredUserName: str | None = None
    rewardType: str
    tokens: int
    isClaimed: bool
    claimedAt: datetime | None = None
    createdAt: datetime


class ReferralListResponse(BaseModel):
    rewards: list[ReferralItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


class TopReferrer(BaseModel):
    email: str | None = None
    name: str | None = None
    totalReferrals: int
    totalTokens: int


class ReferralStatistics(BaseModel):
    totalRewards: int
    claimedRewards: int
    unclaimedRewards: int
    totalTokensAwarded: int
    totalTokensClaimed: int
    topReferrers: list[TopReferrer]
    signupRewards: int
    subscriptionRewards: int


# ===========================================================================
# Chat monitoring (aggregate + metadata only — no message content)
# ===========================================================================


class ChatSessionItem(BaseModel):
    id: str
    userId: str
    userEmail: str | None = None
    userName: str | None = None
    title: str | None = None
    isActive: bool
    messageCount: int
    totalTokens: int
    totalCostUsd: float
    totalRevenueUsd: float
    profitUsd: float
    createdAt: datetime
    updatedAt: datetime


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionItem]
    total: int
    page: int
    pageSize: int
    totalPages: int


class ChatStatisticsResponse(BaseModel):
    totalSessions: int
    totalMessages: int
    totalTokens: int
    averageTokensPerMessage: float
    uniqueUsers: int
    totalCostUsd: float
    totalRevenueUsd: float
    totalProfitUsd: float
    profitMargin: float
    dailyStats: dict[str, dict]


# ===========================================================================
# System configuration
# ===========================================================================


class SystemConfigResponse(BaseModel):
    creditLimits: dict[str, dict[str, int]] = {}
    maintenanceMode: bool = False
    featureFlags: dict[str, bool] = {}


class SystemConfigUpdateRequest(BaseModel):
    creditLimits: dict[str, dict[str, int]] | None = None
    maintenanceMode: bool | None = None
    featureFlags: dict[str, bool] | None = None


class AuditLogEntry(BaseModel):
    """One recorded privileged action, joined to the administrator who performed it.

    ``adminEmail``/``adminName`` are joined from ``User`` so the log reads without a second lookup per
    row. Matches the ``AuditLog`` shape the admin client expects (`admin.types.ts`).
    """

    id: str
    timestamp: datetime
    adminUserId: str
    adminEmail: str
    adminName: str | None = None
    actionType: str
    resourceType: str
    resourceId: str | None = None
    details: dict | None = None


class AuditLogListResponse(BaseModel):
    """Paginated audit log, newest first."""

    logs: list[AuditLogEntry]
    total: int
    page: int
    pageSize: int
    totalPages: int
