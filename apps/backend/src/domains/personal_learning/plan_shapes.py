"""The path shapes offered by the study plan create wizard.

Step 1 of the wizard asks the learner to "choose a proven path shape", and step 4 renders
the chosen shape's phases under the heading "Generated roadmap". Until now that catalogue
lived only in the web bundle, which made the heading false: the phases previewed came from
the client's list, and the phases the plan was actually built with came from whatever the
model returned. The learner approved one roadmap and got another.

Moving the catalogue here is what lets the two agree. `generate_plan` passes the chosen
shape's phase titles to the generator as the structure to fill, so the plan is grouped by
the phases the learner accepted, and the wizard reads the same list from
`GET /learning/study-plans/shapes` rather than carrying its own copy.

**Paces and session lengths deliberately stayed in the client.** They look like catalogue
content too, but they differ in what survives the request: "Focused" and "35 minutes"
resolve to `sessionsPerWeek: 5` and `sessionMinutes: 35`, two plain numbers, and nothing on
the server needs the labels to schedule or to describe a plan afterwards. The shape is
different because the generator has to be told what it means.
"""

from typing import Any

#: The shape catalogue. `id` is what a plan stores in `StudyPlan.shape`.
#:
#: `phases` is the ordered spine the generator is asked to fill. `duration` is a human
#: week range shown in the preview and is not used for scheduling — the real dates come
#: from the deadline and the learner's available days, so a shape cannot promise a
#: timetable it has no way to honour.
PLAN_SHAPES: list[dict[str, Any]] = [
    {
        "id": "career-switch",
        "title": "Prepare for a career move",
        "category": "Career goal",
        "description": "Build capability, proof of work, and confidence around a target role.",
        "defaultTitle": "Frontend engineering transition",
        "defaultOutcome": (
            "Be ready to interview for a frontend engineering role with strong "
            "fundamentals, two portfolio projects, and clear technical communication."
        ),
        "phases": [
            {
                "id": "foundations",
                "title": "Close the foundations gap",
                "description": (
                    "Audit current knowledge and strengthen the concepts the role " "depends on."
                ),
                "duration": "Week 1-2",
                "outcomes": ["Skill baseline", "Core concept refresh", "Weekly recall"],
            },
            {
                "id": "deliberate-practice",
                "title": "Build through deliberate practice",
                "description": ("Turn understanding into repeatable skill with focused exercises."),
                "duration": "Week 3-5",
                "outcomes": ["Guided practice", "Feedback loops", "Weak-area reviews"],
            },
            {
                "id": "proof-of-work",
                "title": "Create proof of work",
                "description": (
                    "Apply the skill in a realistic project and document key decisions."
                ),
                "duration": "Week 6-7",
                "outcomes": ["Capstone project", "Decision log", "Peer-ready artifact"],
            },
            {
                "id": "readiness",
                "title": "Demonstrate readiness",
                "description": (
                    "Rehearse the work, close final gaps, and prepare to explain it clearly."
                ),
                "duration": "Week 8",
                "outcomes": ["Mock review", "Final assessment", "Next-step plan"],
            },
        ],
    },
    {
        "id": "skill-mastery",
        "title": "Master a complex skill",
        "category": "Skill development",
        "description": ("Move from fragmented knowledge to confident, independent application."),
        "defaultTitle": "System design mastery",
        "defaultOutcome": (
            "Design scalable systems independently and explain architecture trade-offs "
            "with evidence and confidence."
        ),
        "phases": [
            {
                "id": "map",
                "title": "Map the system",
                "description": (
                    "Identify the major concepts, dependencies, and current knowledge gaps."
                ),
                "duration": "Week 1",
                "outcomes": ["Knowledge map", "Baseline check", "Priority topics"],
            },
            {
                "id": "patterns",
                "title": "Learn the core patterns",
                "description": (
                    "Build reusable mental models through examples and retrieval practice."
                ),
                "duration": "Week 2-4",
                "outcomes": ["Pattern library", "Concept reviews", "Scenario drills"],
            },
            {
                "id": "cases",
                "title": "Solve realistic cases",
                "description": (
                    "Apply patterns to increasingly ambiguous problems and constraints."
                ),
                "duration": "Week 5-7",
                "outcomes": ["Case studies", "Trade-off analysis", "Timed practice"],
            },
            {
                "id": "capstone",
                "title": "Complete a mastery capstone",
                "description": "Integrate the skill in one substantial, reviewable challenge.",
                "duration": "Week 8",
                "outcomes": ["Capstone", "Self-assessment", "Retention plan"],
            },
        ],
    },
    {
        "id": "portfolio",
        "title": "Complete a meaningful project",
        "category": "Portfolio outcome",
        "description": ("Organize learning around a project you can finish, explain, and share."),
        "defaultTitle": "Portfolio project launch",
        "defaultOutcome": (
            "Ship a polished portfolio project that demonstrates research, execution, "
            "iteration, and clear communication."
        ),
        "phases": [
            {
                "id": "scope",
                "title": "Frame and scope",
                "description": "Choose a useful problem and define a finishable version.",
                "duration": "Week 1",
                "outcomes": ["Project brief", "Success criteria", "Work plan"],
            },
            {
                "id": "build",
                "title": "Build the core",
                "description": ("Create the essential experience in small, reviewable increments."),
                "duration": "Week 2-4",
                "outcomes": ["Core workflow", "Weekly demos", "Decision notes"],
            },
            {
                "id": "refine",
                "title": "Test and refine",
                "description": (
                    "Collect feedback, fix the largest gaps, and improve presentation."
                ),
                "duration": "Week 5",
                "outcomes": ["Feedback review", "Quality pass", "Case-study draft"],
            },
            {
                "id": "publish",
                "title": "Publish and reflect",
                "description": ("Package the result, share it, and capture the next growth edge."),
                "duration": "Week 6",
                "outcomes": ["Published project", "Case study", "Reflection"],
            },
        ],
    },
    {
        "id": "habit",
        "title": "Build a consistent practice",
        "category": "Learning habit",
        "description": ("Create a sustainable rhythm for a subject that rewards regular practice."),
        "defaultTitle": "Daily language practice",
        "defaultOutcome": (
            "Build a consistent practice rhythm and use the skill comfortably in "
            "realistic weekly situations."
        ),
        "phases": [
            {
                "id": "start",
                "title": "Make starting easy",
                "description": ("Define a small repeatable session and remove common friction."),
                "duration": "Week 1",
                "outcomes": ["Practice cue", "Starter routine", "Simple tracking"],
            },
            {
                "id": "stabilize",
                "title": "Stabilize the rhythm",
                "description": ("Repeat the routine and adjust the load before increasing it."),
                "duration": "Week 2-3",
                "outcomes": ["Consistent sessions", "Weekly review", "Recovery plan"],
            },
            {
                "id": "expand",
                "title": "Expand the challenge",
                "description": "Add variety and progressively harder real-world practice.",
                "duration": "Week 4-5",
                "outcomes": ["Challenge ladder", "Mixed practice", "Confidence check"],
            },
            {
                "id": "sustain",
                "title": "Create a sustainable system",
                "description": ("Turn the temporary plan into an adaptable long-term practice."),
                "duration": "Week 6",
                "outcomes": ["Maintenance plan", "Progress review", "Next milestone"],
            },
        ],
    },
]

#: Valid `StudyPlan.shape` values, for contract validation.
SHAPE_IDS = frozenset(shape["id"] for shape in PLAN_SHAPES)


def find_shape(shape_id: str | None) -> dict[str, Any] | None:
    """The shape with this id, or None.

    None for an unknown id rather than an error: a plan created against a shape later
    retired should still read and still redistribute, and generation falls back to letting
    the model choose its own phases — which is what every plan did before shapes existed.
    """
    if not shape_id:
        return None
    return next((shape for shape in PLAN_SHAPES if shape["id"] == shape_id), None)


def phase_titles(shape_id: str | None) -> list[str]:
    """The ordered phase labels of a shape, or an empty list."""
    shape = find_shape(shape_id)
    return [phase["title"] for phase in shape["phases"]] if shape else []
