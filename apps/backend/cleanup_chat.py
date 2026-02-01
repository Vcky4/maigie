#!/usr/bin/env python3
"""Clean up chat.py by removing old code between continue and exception handler."""

with open("src/routes/chat.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the continue statement and the correct exception handler
continue_idx = None
exception_idx = None

for i, line in enumerate(lines):
    if "continue  # Skip to next message" in line and continue_idx is None:
        continue_idx = i
    if (
        "except WebSocketDisconnect:" in line and "manager.disconnect" in lines[i + 1]
        if i + 1 < len(lines)
        else False
    ):
        exception_idx = i
        break

if continue_idx is not None and exception_idx is not None:
    # Keep lines up to continue, then jump to exception handler
    new_lines = lines[: continue_idx + 1] + ["\n"] + lines[exception_idx:]
    with open("src/routes/chat.py", "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"Removed {exception_idx - continue_idx - 1} lines of old code")
else:
    print(f"Could not find boundaries: continue_idx={continue_idx}, exception_idx={exception_idx}")
