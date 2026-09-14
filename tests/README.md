# Tests

Browser tests for the signup modal — the one piece of this theme that carries real business
logic, and the only path by which anyone joins the waitlist.

## Running

```bash
pip install playwright
playwright install chromium   # skip if PLAYWRIGHT_BROWSERS_PATH already has one
python3 tests/test_signup_modal.py
```

Exit code is 0 only if every check passes.

## How it works

`build_harness.py` extracts the modal from `snippets/greenside-signup-modal.liquid` into a
standalone page, so the tests run against **the code that actually ships** rather than a copy.
It asserts no Liquid remains after stripping the `{% doc %}` block — if someone adds Liquid to
the modal, the harness fails loudly instead of testing something subtly different.

Every request to Klaviyo is intercepted and fulfilled locally. **Nothing reaches the live
Klaviyo account and no profile is ever created.**

## What's covered

- UTM, referrer and `?ref=` capture, and the derived `signup_source`
- First-touch attribution surviving a later direct visit, and a late referral code still
  being captured without clobbering first touch
- The promise flags that make pre-launch commitments honourable later
  (founding member, double entry, signup stage, timestamps)
- Referral code generation: stable across casing and whitespace, shown on success
- UK phone normalisation to E.164, and rejection of unusable numbers
- Consent being mandatory
- Signup still working when `localStorage` throws (private browsing / blocked site data)
- Malformed query strings not discarding the valid parameters
- No uncaught JS errors in any flow

## Not covered

Rendering of the theme's Liquid sections, and anything requiring a real Shopify or Klaviyo
response. Those need a staff-session theme preview — see the deployment note in `CLAUDE.md`.
