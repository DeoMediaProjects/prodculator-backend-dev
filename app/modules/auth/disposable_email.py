"""Disposable / throwaway email detection for signup.

The Explorer (free) plan grants a report per person, so the main abuse vector is
one person farming free accounts with temporary email addresses. Combined with
the existing "email must be unique" and "email must be verified before login"
rules, blocking known disposable-email domains is the practical enforcement of
"one free signup per person" — it can't stop a determined user with many real
inboxes, but it removes the easy, high-volume path.

The list covers the most common disposable providers. It's intentionally a
static allow-through-by-default set (unknown domains are always allowed) so a
real customer is never blocked by a stale list.
"""
from __future__ import annotations

# Common disposable / temporary email domains (kept lowercase, no leading dot).
DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com", "10minutemail.net", "20minutemail.com",
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "guerrillamail.biz", "guerrillamail.de", "sharklasers.com",
    "grr.la", "guerrillamailblock.com", "pokemail.net", "spam4.me",
    "mailinator.com", "mailinator.net", "mailinator2.com", "mailinater.com",
    "mailin8r.com", "notmailinator.com", "reallymymail.com",
    "tempmail.com", "temp-mail.org", "temp-mail.io", "tempmailo.com",
    "tempail.com", "tempmailer.com", "tempinbox.com", "tempr.email",
    "throwawaymail.com", "throwaymail.com", "trashmail.com", "trashmail.net",
    "trashmail.io", "trash-mail.com", "wegwerfmail.de",
    "getnada.com", "nada.email", "getairmail.com",
    "yopmail.com", "yopmail.net", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf",
    "dispostable.com", "fakeinbox.com", "fakemailgenerator.com",
    "maildrop.cc", "mailnesia.com", "mintemail.com", "mohmal.com",
    "moakt.com", "mytemp.email", "emailondeck.com", "burnermail.io",
    "spambox.us", "spamgourmet.com", "mailcatch.com", "inboxbear.com",
    "tempmailaddress.com", "33mail.com", "anonaddy.me", "mail-temp.com",
    "discard.email", "discardmail.com", "one-time.email", "1secmail.com",
    "1secmail.org", "1secmail.net", "email-temp.com", "luxusmail.org",
    "harakirimail.com", "mailexpire.com", "mailsac.com", "tmail.ws",
    "tmails.net", "tmpmail.org", "tmpmail.net", "vomoto.com", "vpn.tf",
})


def email_domain(email: str) -> str:
    """Return the lowercased domain part of an email, or '' if malformed."""
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def is_disposable_email(email: str) -> bool:
    """True if the email uses a known disposable / temporary provider."""
    return email_domain(email) in DISPOSABLE_EMAIL_DOMAINS
