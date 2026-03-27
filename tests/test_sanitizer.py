"""Tests for the sanitizer module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from infra_agent.sanitizer import (
    SanitizedResult,
    SanitizerConfig,
    _detector_name_to_tag,
    sanitize,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestDetectorNameToTag:
    """Tests for the _detector_name_to_tag helper."""

    def test_aws_access_key(self) -> None:
        assert _detector_name_to_tag("AWS Access Key") == "aws_access_key"

    def test_base64_high_entropy_string(self) -> None:
        assert _detector_name_to_tag("Base64 High Entropy String") == "base64_high_entropy"

    def test_private_key(self) -> None:
        assert _detector_name_to_tag("Private Key") == "private_key"

    def test_secret_keyword(self) -> None:
        assert _detector_name_to_tag("Secret Keyword") == "secret_keyword"

    def test_github_token(self) -> None:
        assert _detector_name_to_tag("GitHub Token") == "git_hub_token"

    def test_json_web_token(self) -> None:
        assert _detector_name_to_tag("JSON Web Token") == "json_web_token"


class TestSanitizerConfig:
    """Tests for SanitizerConfig."""

    def test_default_config_has_plugins(self) -> None:
        config = SanitizerConfig()
        plugins = config.to_plugins_list()
        assert len(plugins) > 0

    def test_all_disabled_returns_empty(self) -> None:
        config = SanitizerConfig(
            enable_aws=False,
            enable_azure=False,
            enable_private_keys=False,
            enable_high_entropy=False,
            enable_keywords=False,
            enable_common_tokens=False,
        )
        assert config.to_plugins_list() == []

    def test_only_aws_enabled(self) -> None:
        config = SanitizerConfig(
            enable_aws=True,
            enable_azure=False,
            enable_private_keys=False,
            enable_high_entropy=False,
            enable_keywords=False,
            enable_common_tokens=False,
        )
        plugins = config.to_plugins_list()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "AWSKeyDetector"

    def test_custom_entropy_limit(self) -> None:
        config = SanitizerConfig(entropy_limit=3.0)
        plugins = config.to_plugins_list()
        entropy_plugins = [p for p in plugins if "limit" in p]
        assert all(p["limit"] == 3.0 for p in entropy_plugins)


class TestSanitizePositive:
    """Positive tests: secrets ARE caught and redacted."""

    def test_aws_access_key_redacted(self) -> None:
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        result = sanitize(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result.cleaned_text
        assert "[REDACTED:" in result.cleaned_text
        assert result.redaction_count >= 1

    def test_private_key_redacted(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"
        result = sanitize(text)
        assert "[REDACTED:private_key]" in result.cleaned_text
        assert result.redaction_count >= 1
        tags = [r.redaction_type for r in result.redactions]
        assert any("private_key" in t for t in tags)

    def test_private_key_block_fully_redacted(self) -> None:
        """C2: Verify the entire private key block is removed, including body and END marker."""
        text = (
            "some preamble\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MhgHcTz6sE2I2yPB\n"
            "aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcdefghijklmnopqrstuvwxyz==\n"
            "-----END RSA PRIVATE KEY-----\n"
            "some postamble\n"
        )
        result = sanitize(text)
        assert "-----BEGIN RSA PRIVATE KEY-----" not in result.cleaned_text
        assert "MIIEpAIBAAKCAQEA" not in result.cleaned_text
        assert "-----END RSA PRIVATE KEY-----" not in result.cleaned_text
        assert "[REDACTED:private_key]" in result.cleaned_text
        # Preamble and postamble should remain
        assert "some preamble" in result.cleaned_text
        assert "some postamble" in result.cleaned_text

    def test_jwt_token_redacted(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        text = f"Authorization: Bearer {jwt}\n"
        result = sanitize(text)
        assert "eyJhbGci" not in result.cleaned_text
        assert result.redaction_count >= 1

    def test_github_token_redacted(self) -> None:
        """T1: Verify the FULL GitHub token is removed, not just the prefix."""
        text = "export GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234\n"
        result = sanitize(text)
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in result.cleaned_text
        # T1: Verify the rest of the token is also gone
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234" not in result.cleaned_text
        assert result.redaction_count >= 1

    def test_url_password_redacted(self) -> None:
        """C3: Verify passwords in connection-string URLs are redacted."""
        text = "database_url = postgresql://admin:SuperSecret123!@db.example.com:5432/production\n"
        result = sanitize(text)
        assert "SuperSecret123!" not in result.cleaned_text
        assert "[REDACTED:url_password]" in result.cleaned_text
        # The rest of the URL should remain
        assert "db.example.com" in result.cleaned_text

    def test_azure_sas_token_redacted(self) -> None:
        """C4: Verify Azure SAS token signatures are redacted."""
        text = (
            "https://myaccount.blob.core.windows.net/container/blob"
            "?sv=2021-06-08&ss=b&srt=sco&sp=rwdlacitfx"
            "&sig=abc123def456ghi789jkl012mno345pqr678stu901vwx234yz%3D"
            "&se=2025-01-01T00:00:00Z\n"
        )
        result = sanitize(text)
        assert "abc123def456ghi789jkl012mno345pqr678stu901vwx234yz" not in result.cleaned_text
        assert "[REDACTED:azure_sas_token]" in result.cleaned_text

    def test_full_fixture_no_raw_secrets(self) -> None:
        """T2: Verify ALL secrets from the fixture file are redacted."""
        fixture_text = (FIXTURES_DIR / "secrets_sample.txt").read_text()
        result = sanitize(fixture_text)
        # AWS access key
        assert "AKIAIOSFODNN7EXAMPLE" not in result.cleaned_text
        # AWS secret key
        assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in result.cleaned_text
        # Azure connection string AccountKey
        assert "abc123def456ghi789jkl012mno345pqr678stu901vwx234yz==" not in result.cleaned_text
        # Private key BEGIN marker
        assert "-----BEGIN RSA PRIVATE KEY-----" not in result.cleaned_text
        # Private key body
        assert "MIIEpAIBAAKCAQEA" not in result.cleaned_text
        # Private key END marker
        assert "-----END RSA PRIVATE KEY-----" not in result.cleaned_text
        # JWT token body
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result.cleaned_text
        # GitHub token (full token, not just prefix)
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234" not in result.cleaned_text
        # Database password
        assert "SuperSecret123" not in result.cleaned_text
        # API key
        assert "sk-1234567890abcdefghijklmnopqrstuvwxyz1234567890ab" not in result.cleaned_text
        assert result.redaction_count >= 3

    def test_high_entropy_string_caught(self) -> None:
        # A high-entropy base64 string assigned to a keyword should be detected
        text = "secret_key = aGVsbG93b3JsZHRoaXNpc2FiYXNlNjRzdHJpbmc=\n"
        result = sanitize(text)
        assert result.redaction_count >= 1


class TestSanitizeNegative:
    """Negative tests: normal text is NOT mangled."""

    def test_normal_terraform_resource(self) -> None:
        text = (
            'resource "azurerm_resource_group" "example" {\n'
            '  name = "rg-example"\n'
            '  location = "eastus"\n'
            "}\n"
        )
        result = sanitize(text)
        assert result.cleaned_text == text
        assert result.redaction_count == 0

    def test_error_message_unchanged(self) -> None:
        text = 'Error: creating Resource Group "rg-example": authorization failed\n'
        result = sanitize(text)
        assert result.cleaned_text == text
        assert result.redaction_count == 0

    def test_normal_english_text(self) -> None:
        text = "This is a normal log line with nothing suspicious.\nAnother log entry.\n"
        result = sanitize(text)
        assert result.cleaned_text == text
        assert result.redaction_count == 0

    def test_ip_address_not_redacted(self) -> None:
        text = "Server listening on 192.168.1.1:8080\n"
        config = SanitizerConfig(enable_high_entropy=False)
        result = sanitize(text, config)
        assert "192.168.1.1:8080" in result.cleaned_text


class TestSanitizeEdgeCases:
    """Edge case tests."""

    def test_empty_string(self) -> None:
        result = sanitize("")
        assert result.cleaned_text == ""
        assert result.redaction_count == 0

    def test_text_with_no_secrets_unchanged(self) -> None:
        text = "Hello world\nThis is fine\n"
        result = sanitize(text)
        assert result.cleaned_text == text
        assert result.redaction_count == 0

    def test_all_detectors_disabled_returns_unchanged(self) -> None:
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        config = SanitizerConfig(
            enable_aws=False,
            enable_azure=False,
            enable_private_keys=False,
            enable_high_entropy=False,
            enable_keywords=False,
            enable_common_tokens=False,
        )
        result = sanitize(text, config)
        assert result.cleaned_text == text
        assert result.redaction_count == 0

    def test_multiple_secrets_on_one_line(self) -> None:
        """T4: Verify ALL secrets on a single line are fully redacted."""
        text = "keys: AKIAIOSFODNN7EXAMPLE and ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234\n"
        result = sanitize(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result.cleaned_text
        # Full GitHub token must also be gone (not just prefix)
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234" not in result.cleaned_text
        assert result.redaction_count >= 2

    def test_secret_value_none_no_false_redaction(self) -> None:
        """T3: When secret_value is None, no false Redaction metadata is emitted.

        Uses a line that does NOT match the custom keyword post-processor
        patterns, so the only detection path is via detect-secrets (which
        we mock to return secret_value=None).
        """
        mock_secret = MagicMock()
        mock_secret.type = "Hex High Entropy String"
        mock_secret.line_number = 1
        mock_secret.secret_value = None

        text = "value: 4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d\n"
        with patch("infra_agent.sanitizer.SecretsCollection") as mock_collection_cls:
            mock_instance = MagicMock()
            mock_collection_cls.return_value = mock_instance
            mock_instance.data = {"fake_file": [mock_secret]}
            mock_instance.scan_file = MagicMock()

            result = sanitize(text)

        # H1: No redaction metadata should be emitted for None secret_value
        # since we can't determine what to redact from detect-secrets alone.
        hex_redactions = [r for r in result.redactions if r.redaction_type == "hex_high_entropy"]
        assert len(hex_redactions) == 0


class TestSanitizedResult:
    """Tests for the SanitizedResult dataclass."""

    def test_redaction_count_property(self) -> None:
        result = SanitizedResult(cleaned_text="test", redactions=())
        assert result.redaction_count == 0

    def test_redactions_is_tuple(self) -> None:
        """Q3: Verify redactions field is a tuple (immutable) on a frozen dataclass."""
        result = SanitizedResult(cleaned_text="test")
        assert isinstance(result.redactions, tuple)


class TestConfigDetectorControl:
    """Tests for config controlling which detectors run."""

    def test_disabling_aws_lets_keys_through(self) -> None:
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        config = SanitizerConfig(
            enable_aws=False,
            enable_high_entropy=False,
            enable_keywords=False,
        )
        result = sanitize(text, config)
        assert "AKIAIOSFODNN7EXAMPLE" in result.cleaned_text

    def test_low_entropy_limit_catches_more(self) -> None:
        # With a very low entropy limit, even moderate-entropy strings get caught
        text = "token = YWJjZGVmZw==\n"
        config_high = SanitizerConfig(entropy_limit=6.0)
        config_low = SanitizerConfig(entropy_limit=1.0)
        result_high = sanitize(text, config_high)
        result_low = sanitize(text, config_low)
        # Low limit should catch more or equal
        assert result_low.redaction_count >= result_high.redaction_count
