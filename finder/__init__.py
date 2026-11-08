"""Email finder engine. Phase 1: email verification.

    from finder import verify_email
    result = verify_email("someone@company.com")
"""
from .models import CheckResult, SmtpResponse, VerificationResult
from .verify import Verifier, default_verifier, verify_email

__all__ = ["verify_email", "Verifier", "default_verifier", "VerificationResult", "CheckResult", "SmtpResponse"]
