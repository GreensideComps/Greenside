"""End-to-end tests for the Greenside signup modal, driven in real Chromium.

Every Klaviyo request is intercepted, so nothing reaches the live account.
"""
import json
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_harness  # noqa: E402

HARNESS = None  # set by run(), once the harness has been built from the live snippet
KLAVIYO = "**a.klaviyo.com/client/subscriptions/**"

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))


def launch_chromium(pw):
    """Prefer Playwright's own resolution; fall back to a preinstalled Chromium."""
    try:
        return pw.chromium.launch()
    except Exception:
        for candidate in sorted(pathlib.Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
            return pw.chromium.launch(executable_path=str(candidate))
        raise


class Harness:
    """Loads the modal, captures outgoing Klaviyo payloads."""

    def __init__(self, page):
        self.page = page
        self.payloads = []
        page.route(KLAVIYO, self._intercept)

    def _intercept(self, route, request):
        try:
            self.payloads.append(json.loads(request.post_data or "{}"))
        except Exception:
            self.payloads.append({"__unparseable__": request.post_data})
        route.fulfill(status=202, content_type="application/json", body="{}")

    def visit(self, query=""):
        self.page.goto(f"file://{HARNESS}{query}")

    def signup(self, email="golfer@example.com", phone="", consent=True):
        self.page.click("[data-greenside-signup-trigger]")
        self.page.fill("#greenside-email", email)
        if phone:
            self.page.fill("#greenside-phone", phone)
        if consent:
            self.page.check("#greenside-consent")
        self.page.click(".greenside-modal__submit")
        self.page.wait_for_timeout(350)

    @property
    def last_props(self):
        p = self.payloads[-1]
        return p["data"]["attributes"]["profile"]["data"]["attributes"].get("properties", {})

    @property
    def last_profile(self):
        return self.payloads[-1]["data"]["attributes"]["profile"]["data"]["attributes"]


def run():
    global HARNESS
    HARNESS, size = build_harness.build()
    print(f"harness built from live snippet ({size} chars)\n")

    with sync_playwright() as pw:
        browser = launch_chromium(pw)
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        h = Harness(page)

        # --- 1. Paid/social attribution is captured and sent -------------------
        h.visit("?utm_source=instagram&utm_medium=social&utm_campaign=launch&utm_content=story1&ref=GSFRIEND")
        h.signup(email="Golfer@Example.com ")
        props = h.last_props
        check("utm_source captured", props.get("greenside_utm_source") == "instagram", props)
        check("utm_medium captured", props.get("greenside_utm_medium") == "social")
        check("utm_campaign captured", props.get("greenside_utm_campaign") == "launch")
        check("utm_content captured", props.get("greenside_utm_content") == "story1")
        check("derived source = instagram", props.get("greenside_signup_source") == "instagram")
        check("referrer code captured", props.get("greenside_referred_by") == "GSFRIEND")
        check("founding member flagged", props.get("greenside_founding_member") is True)
        check("double entry promised", props.get("greenside_double_entry_promised") is True)
        check("stage recorded", props.get("greenside_signup_stage") == "pre_launch_waitlist")
        check("signup timestamp is ISO", bool(re.match(r"\d{4}-\d{2}-\d{2}T", props.get("greenside_signup_at", ""))))
        check("consent still SUBSCRIBED",
              h.last_profile["subscriptions"]["email"]["marketing"]["consent"] == "SUBSCRIBED")
        check("no SMS consent without sender", "sms" not in h.last_profile.get("subscriptions", {}))

        # --- 2. Referral code is stable + case-insensitive --------------------
        code_mixed = props.get("greenside_referral_code")
        check("referral code generated", bool(code_mixed) and code_mixed.startswith("GS"), code_mixed)
        link = page.input_value("[data-greenside-referral-link]")
        check("referral link shown on success", code_mixed in link, link)
        check("referral block visible", page.is_visible("[data-greenside-referral]"))

        h.visit("")
        h.signup(email="golfer@example.com")
        check("referral code stable across case/whitespace",
              h.last_props.get("greenside_referral_code") == code_mixed,
              f"{h.last_props.get('greenside_referral_code')} vs {code_mixed}")

        # --- 3. First-touch attribution survives a later direct visit ---------
        ctx2 = browser.new_context()
        p2 = ctx2.new_page()
        h2 = Harness(p2)
        h2.visit("?utm_source=tiktok&utm_medium=social")
        h2.visit("")  # later direct return, same storage
        h2.signup(email="second@example.com")
        check("first-touch source retained on direct return",
              h2.last_props.get("greenside_utm_source") == "tiktok",
              h2.last_props.get("greenside_utm_source"))

        # A referral code arriving later must still be captured.
        h2.visit("?ref=GSLATER")
        h2.signup(email="third@example.com")
        check("late referral code captured", h2.last_props.get("greenside_referred_by") == "GSLATER")
        check("late referral does not clobber first-touch",
              h2.last_props.get("greenside_utm_source") == "tiktok")
        ctx2.close()

        # --- 4. Direct visit reports 'direct' ---------------------------------
        ctx3 = browser.new_context()
        p3 = ctx3.new_page()
        h3 = Harness(p3)
        h3.visit("")
        h3.signup(email="direct@example.com")
        check("direct visit labelled direct", h3.last_props.get("greenside_signup_source") == "direct",
              h3.last_props.get("greenside_signup_source"))
        ctx3.close()

        # --- 5. Phone handling ------------------------------------------------
        ctx4 = browser.new_context()
        p4 = ctx4.new_page()
        h4 = Harness(p4)
        h4.visit("")
        h4.signup(email="phone@example.com", phone="07123 456789")
        check("UK phone normalised to E.164",
              h4.last_profile.get("phone_number") == "+447123456789",
              h4.last_profile.get("phone_number"))

        h4.visit("")
        before = len(h4.payloads)
        h4.signup(email="bad@example.com", phone="12")
        check("invalid phone blocks submit", len(h4.payloads) == before)
        check("invalid phone shows error", p4.is_visible("[data-greenside-modal-error]"))

        # --- 6. Consent is mandatory -----------------------------------------
        h4.visit("")
        before = len(h4.payloads)
        h4.signup(email="noconsent@example.com", consent=False)
        check("missing consent blocks submit", len(h4.payloads) == before)
        ctx4.close()

        # --- 7. Storage blocked must not break signup ------------------------
        ctx5 = browser.new_context()
        p5 = ctx5.new_page()
        p5.add_init_script(
            "Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}});"
        )
        h5 = Harness(p5)
        h5.visit("?utm_source=google")
        h5.signup(email="private@example.com")
        check("signup still works with localStorage blocked", len(h5.payloads) == 1)
        check("attribution still derived in-page without storage",
              h5.last_props.get("greenside_utm_source") == "google",
              h5.last_props.get("greenside_utm_source"))
        ctx5.close()

        # --- 8. Malformed query string must not break anything ---------------
        ctx6 = browser.new_context()
        p6 = ctx6.new_page()
        h6 = Harness(p6)
        h6.visit("?utm_source=%E0%A4%A&utm_medium=email&ref=GSOK")
        h6.signup(email="malformed@example.com")
        check("malformed query survives, good params kept",
              h6.last_props.get("greenside_utm_medium") == "email"
              and h6.last_props.get("greenside_referred_by") == "GSOK",
              h6.last_props)
        ctx6.close()

        real_errors = [e for e in errors if "Failed to load resource" not in e]
        check("no uncaught JS errors", not real_errors, real_errors[:3])

        browser.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
