# Fix GitHub Issue

Fix GitHub issue #$ARGUMENTS

## Workflow

1. **Fetch issue details** using `gh issue view $ARGUMENTS`
2. **Understand the root cause** by reading relevant code
3. **Create a plan** for the fix using extended thinking
4. **Implement the fix** with minimal changes
5. **Write or update tests** to cover the fix
6. **Run tests** to verify the fix works
7. **Create a commit** with message "Fixes #$ARGUMENTS: <description>"

## Guidelines

- Focus on the specific issue - avoid unrelated changes
- Follow existing code patterns in the repository
- Ensure backward compatibility unless explicitly requested
- Update documentation if behavior changes
