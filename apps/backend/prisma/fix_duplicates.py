"""Fix duplicate enum/model definitions in split schema files."""
import re
import pathlib

schema_dir = pathlib.Path(__file__).parent / "schema"

# Remove duplicate enums from personal_learning.prisma
f = schema_dir / "personal_learning.prisma"
content = f.read_text(encoding="utf-8")
for enum_name in ["ExamPrepStatus", "MaterialCategory", "QuestionSource", "QuestionType", "QuestionDifficulty", "QuizMode"]:
    content = re.sub(rf"enum {enum_name} \{{[^}}]+\}}\s*", "", content)
f.write_text(content, encoding="utf-8")
print("Fixed personal_learning.prisma")

# Remove AchievementType from progress.prisma
f = schema_dir / "progress.prisma"
content = f.read_text(encoding="utf-8")
content = re.sub(r"enum AchievementType \{[^}]+\}\s*", "", content)
f.write_text(content, encoding="utf-8")
print("Fixed progress.prisma")

# Remove ModelPreference model from intelligence.prisma (already in identity.prisma)
f = schema_dir / "intelligence.prisma"
content = f.read_text(encoding="utf-8")
content = re.sub(r"model ModelPreference \{[^}]+\}\s*", "", content)
f.write_text(content, encoding="utf-8")
print("Fixed intelligence.prisma")

# Remove duplicate enum defs from admin.prisma (already in base.prisma)
f = schema_dir / "admin.prisma"
content = f.read_text(encoding="utf-8")
for enum_name in ["FeedbackType", "FeedbackStatus", "CareerApplicationStatus"]:
    content = re.sub(rf"enum {enum_name} \{{[^}}]+\}}\s*", "", content)
f.write_text(content, encoding="utf-8")
print("Fixed admin.prisma")

# Also check if base.prisma has the @default values right
# FeedbackStatus needs NEW, REVIEWING, RESOLVED, DISMISSED
f = schema_dir / "base.prisma"
content = f.read_text(encoding="utf-8")
# Check if FeedbackStatus has the right values
if "NEW" not in content and "FeedbackStatus" in content:
    # Fix: replace FeedbackStatus enum with correct values
    content = re.sub(
        r"enum FeedbackStatus \{[^}]+\}",
        "enum FeedbackStatus {\n  NEW\n  REVIEWING\n  RESOLVED\n  DISMISSED\n}",
        content,
    )
    f.write_text(content, encoding="utf-8")
    print("Fixed FeedbackStatus values in base.prisma")

# Check CareerApplicationStatus has NEW
if "CareerApplicationStatus" in content:
    current_match = re.search(r"enum CareerApplicationStatus \{([^}]+)\}", content)
    if current_match and "NEW" not in current_match.group(1):
        # Needs to have PENDING changed to include a NEW-compatible default
        # Actually the admin model uses @default(NEW) but enum has PENDING
        # Let's add NEW to the enum
        content = content.replace(
            "enum CareerApplicationStatus {\n  PENDING\n  REVIEWED\n  ACCEPTED\n  REJECTED\n}",
            "enum CareerApplicationStatus {\n  NEW\n  PENDING\n  REVIEWED\n  ACCEPTED\n  REJECTED\n}",
        )
        f.write_text(content, encoding="utf-8")
        print("Fixed CareerApplicationStatus in base.prisma")

print("\nDone! Run `prisma generate --schema prisma/schema` again.")
