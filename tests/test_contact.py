"""The policy pages' contact details (transitnav/contact.py)."""

import unittest
from unittest import mock

from transitnav import contact

PAGE = "{{OWNER}} | {{PLACE}} | {{CONTACT_LINE}} | {{CONTACT_LIST}}"


def filled(**env):
    with mock.patch.object(contact.settings, "get", lambda key: env.get(key)):
        return contact.fill(PAGE)


class ContactTest(unittest.TestCase):
    def test_without_settings_points_to_the_form(self):
        page = filled()
        self.assertNotIn("{{", page)
        self.assertIn("Ashil J Thomas", page)
        self.assertIn("Kerala, India", page)
        self.assertIn('href="/contact"', page)
        self.assertNotIn("mailto:", page)
        self.assertIn("Use the form on this page", page)

    def test_email_phone_and_address(self):
        page = filled(VAZHI_CONTACT_EMAIL="rider.help@example.com", VAZHI_CONTACT_PHONE="+91 98765 43210",
                      VAZHI_ADDRESS="Kottayam, Kerala", VAZHI_OWNER="A Maker")
        self.assertIn('href="mailto:rider.help@example.com"', page)
        self.assertIn('href="tel:+919876543210"', page)
        self.assertIn("A Maker | Kottayam, Kerala |", page)
        self.assertNotIn("Use the form on this page", page)

    def test_bad_values_are_ignored_and_escaped(self):
        page = filled(VAZHI_CONTACT_EMAIL="not an email", VAZHI_CONTACT_PHONE="call me",
                      VAZHI_OWNER="<script>x</script>")
        self.assertNotIn("mailto:", page)
        self.assertNotIn("tel:", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main()
