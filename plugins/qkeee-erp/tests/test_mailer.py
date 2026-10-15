"""mailer.py: gateway-side HTML email from the agent's mailbox (EMAIL_*)."""

import smtplib
import unittest
from unittest.mock import MagicMock, patch

from qkeee_erp_plugin.qkeee_erp import mailer

ENV = {"EMAIL_ADDRESS": "agent@bot.example", "EMAIL_PASSWORD": "pw",
       "EMAIL_SMTP_HOST": "smtp.bot.example", "EMAIL_SMTP_PORT": "587",
       "EMAIL_FROM_NAME": "ERPNext Associate"}


class MailerTests(unittest.TestCase):

    def setUp(self):
        mailer.set_secret_reader(lambda name, default=None: ENV.get(name, default))
        self.addCleanup(mailer.set_secret_reader, None)

    def _send(self, smtp_cls="SMTP", **kw):
        server = MagicMock()
        server.send_message.return_value = kw.pop("refused", {})
        with patch.object(mailer.smtplib, smtp_cls, return_value=server) as cls:
            result = mailer.send_html("r@org.com", "S", "<p>h</p>", "t")
        return result, server, cls

    def test_sends_multipart_html_and_text_to_one_recipient(self):
        result, server, cls = self._send()
        self.assertEqual(cls.call_args.args, ("smtp.bot.example", 587))
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("agent@bot.example", "pw")
        msg = server.send_message.call_args.args[0]
        self.assertEqual(server.send_message.call_args.kwargs["to_addrs"], ["r@org.com"])
        self.assertEqual(msg["To"], "r@org.com")
        self.assertEqual(msg["From"], "ERPNext Associate <agent@bot.example>")
        self.assertEqual(msg["Auto-Submitted"], "auto-generated")
        self.assertEqual([p.get_content_type() for p in msg.get_payload()],
                         ["text/plain", "text/html"])
        self.assertEqual(result["message_id"], msg["Message-ID"])
        self.assertTrue(result["message_id"].endswith("@bot.example>"))

    def test_port_465_uses_implicit_tls(self):
        ENV_465 = dict(ENV, EMAIL_SMTP_PORT="465")
        mailer.set_secret_reader(lambda name, default=None: ENV_465.get(name, default))
        _, server, cls = self._send("SMTP_SSL")
        cls.assert_called_once()
        server.starttls.assert_not_called()

    def test_missing_mailbox_sends_nothing(self):
        mailer.set_secret_reader(lambda name, default=None: None)
        with patch.dict(mailer.os.environ, {}, clear=True), \
                patch.object(mailer.smtplib, "SMTP") as cls:
            with self.assertRaises(mailer.EmailNotConfiguredError) as e:
                mailer.send_html("r@org.com", "S", "h", "t")
            cls.assert_not_called()
        self.assertIn("EMAIL_ADDRESS", str(e.exception))
        self.assertNotIn("pw", str(e.exception))

    def test_falls_back_to_process_env(self):
        mailer.set_secret_reader(lambda name, default=None: None)
        with patch.dict(mailer.os.environ, ENV, clear=True):
            self.assertEqual(mailer.mailbox()["address"], "agent@bot.example")

    def test_only_one_plain_address_is_accepted(self):
        for to in ("", "a@x.com, b@y.com", "a@x.com;b@y.com", "a@x.com\r\nBcc: b@y.com", "nobody"):
            with self.subTest(to=to), patch.object(mailer.smtplib, "SMTP") as cls:
                with self.assertRaises(mailer.EmailSendError):
                    mailer.send_html(to, "S", "h", "t")
                cls.assert_not_called()

    def test_auth_and_transport_failures_are_send_errors(self):
        for exc in (smtplib.SMTPAuthenticationError(535, b"bad"), OSError("refused")):
            server = MagicMock()
            server.login.side_effect = exc
            with self.subTest(exc=type(exc).__name__), \
                    patch.object(mailer.smtplib, "SMTP", return_value=server):
                with self.assertRaises(mailer.EmailSendError) as e:
                    mailer.send_html("r@org.com", "S", "h", "t")
                self.assertNotIn("pw", str(e.exception))

    def test_refused_recipient_is_an_error(self):
        with self.assertRaises(mailer.EmailSendError):
            self._send(refused={"r@org.com": (550, b"no")})


if __name__ == "__main__":
    unittest.main()
