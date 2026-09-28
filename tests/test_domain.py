from lead_engine.domain import (
    EmailClass,
    is_safe_public_host,
    mask_email,
    normalize_website,
    parse_email,
    registered_domain,
)


def test_business_email_with_subdomain_and_multipart_suffix():
    p = parse_email("Jane@Mail.AcmeGroup.co.uk")
    assert p.email_class is EmailClass.BUSINESS
    assert p.registered_domain == "acmegroup.co.uk"
    assert p.host == "mail.acmegroup.co.uk"


def test_personal_email_detected():
    assert parse_email("someone@gmail.com").email_class is EmailClass.PERSONAL
    assert parse_email("someone@YAHOO.COM").email_class is EmailClass.PERSONAL


def test_disposable_email_detected():
    assert parse_email("x@mailinator.com").email_class is EmailClass.DISPOSABLE


def test_invalid_email_variants():
    assert parse_email("not-an-email").email_class is EmailClass.INVALID
    assert parse_email("double@@acme.com").email_class is EmailClass.INVALID
    assert parse_email("user@fake.zzzz").email_class is EmailClass.INVALID
    assert parse_email("").email_class is EmailClass.INVALID


def test_public_suffix_domain_like_github_io():
    # github.io is itself a public suffix (users get subdomains under it), so a
    # bare "user@github.io" has no further registrable label underneath it.
    assert registered_domain("myproject.github.io") == "github.io"


def test_com_au_multipart_suffix():
    assert registered_domain("finance.bankcorp.com.au") == "bankcorp.com.au"


def test_naive_regex_would_have_failed_on_co_uk():
    # Documents the bug in the original blueprint's domain-extraction regex
    # `(?<=@)[^.]+\.[^.]+`: on a host with a subdomain plus a multi-part public
    # suffix, it greedily grabs the first two labels ("london.acmegroup")
    # instead of the real registrable domain ("acmegroup.co.uk") — wrong either
    # way, and it would misfile this business lead as a completely different
    # (nonexistent) company domain.
    import re

    naive = re.search(r"(?<=@)[^.]+\.[^.]+", "ops@london.acmegroup.co.uk")
    assert naive.group(0) == "london.acmegroup"  # the bug
    assert registered_domain("london.acmegroup.co.uk") == "acmegroup.co.uk"  # the fix


def test_website_normalization_strips_scheme_and_path():
    assert normalize_website("https://www.acme.io/pricing?x=1") == "acme.io"
    assert normalize_website("acme.io") == "acme.io"


def test_website_ssrf_guards_reject_internal_hosts():
    assert normalize_website("http://127.0.0.1:8080/admin") == ""
    assert normalize_website("http://internal-admin.local/dashboard") == ""
    assert normalize_website("http://localhost") == ""
    assert normalize_website("http://10.0.0.5") == ""
    assert normalize_website("ftp://acme.io") == ""
    assert normalize_website("") == ""


def test_is_safe_public_host():
    assert is_safe_public_host("acme.io") is True
    assert is_safe_public_host("8.8.8.8") is True
    assert is_safe_public_host("127.0.0.1") is False
    assert is_safe_public_host("169.254.169.254") is False  # cloud metadata endpoint
    assert is_safe_public_host("") is False


def test_mask_email():
    assert mask_email("jane.doe@acme.io") == "j***@acme.io"
    assert mask_email("nodomain") == "***"
