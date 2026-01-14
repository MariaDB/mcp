# Review Pull Request

Review PR #$ARGUMENTS

## Workflow

1. **Fetch PR details** using `gh pr view $ARGUMENTS`
2. **Get the diff** using `gh pr diff $ARGUMENTS`
3. **Understand the changes** - what problem does this PR solve?
4. **Review code quality**:
   - Check for bugs or logic errors
   - Verify error handling
   - Look for security issues (SQL injection, XSS, etc.)
   - Check naming conventions and code clarity
5. **Verify test coverage** - are changes adequately tested?
6. **Check for breaking changes** - is backward compatibility maintained?
7. **Provide constructive feedback** with specific suggestions

## Review Checklist

- [ ] Code follows project conventions
- [ ] No obvious security vulnerabilities
- [ ] Error cases are handled appropriately
- [ ] Tests cover the changes
- [ ] Documentation updated if needed
- [ ] No unrelated changes included

## Output Format

Provide a summary with:
- Overall assessment (approve/request changes/comment)
- Specific issues found (with line references)
- Suggestions for improvement
- Questions for the author
