# Login Page Research

Common login page failure modes include unclear validation, missing loading
states, and inaccessible form labels.

## UX Notes

- Keep the primary action close to the password field.
- Use browser autocomplete values for email and current password.
- Show authentication errors without clearing the email field.
- Make keyboard submission work from either input.

## Testing Notes

- Test empty form submission.
- Test malformed email validation.
- Test disabled submit state while submitting.
- Test successful and failed authentication responses.
