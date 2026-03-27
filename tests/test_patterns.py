"""Tests for error detection patterns."""

import pytest

from infra_agent.patterns import ToolType, detect_tool_type, match_error

# --- detect_tool_type ---


class TestDetectToolType:
    """Tests for detect_tool_type."""

    def test_terraform_version(self) -> None:
        """Detects Terraform from version string."""
        assert detect_tool_type("Terraform v1.9.5") == ToolType.TERRAFORM

    def test_terraform_init(self) -> None:
        """Detects Terraform from init output."""
        assert detect_tool_type("Initializing the backend...") == ToolType.TERRAFORM

    def test_packer_builder(self) -> None:
        """Detects Packer from builder output."""
        assert detect_tool_type("==> azure-arm: Running builder ...") == ToolType.PACKER

    def test_packer_version(self) -> None:
        """Detects Packer from version string."""
        assert detect_tool_type("packer v1.10.3") == ToolType.PACKER

    def test_ansible_play(self) -> None:
        """Detects Ansible from PLAY header."""
        assert detect_tool_type("PLAY [webservers]") == ToolType.ANSIBLE

    def test_ansible_task(self) -> None:
        """Detects Ansible from TASK header."""
        assert detect_tool_type("TASK [install nginx]") == ToolType.ANSIBLE

    def test_unknown(self) -> None:
        """Returns None for unrecognized text."""
        assert detect_tool_type("just a normal log line") is None

    def test_empty_line(self) -> None:
        """Returns None for empty line."""
        assert detect_tool_type("") is None


# --- match_error positive cases ---


class TestMatchErrorPositive:
    """Test that known error strings are matched correctly."""

    @pytest.mark.parametrize(
        ("line", "tool_type", "expected_pattern"),
        [
            ('Error: creating Resource Group "rg-example"', ToolType.TERRAFORM, "terraform_error"),
            ("| Error: something wrong", ToolType.TERRAFORM, "terraform_pipe_error"),
            ("on main.tf line 12", ToolType.TERRAFORM, "terraform_on_tf_line"),
            ("Failed to load backend", ToolType.TERRAFORM, "terraform_failed_to"),
            ("Operation timed out: timeout exceeded", ToolType.TERRAFORM, "terraform_timeout"),
            ("HTTP 403 Forbidden", ToolType.TERRAFORM, "terraform_403"),
            ("HTTP 404 Not Found", ToolType.TERRAFORM, "terraform_404"),
            ("Error: quota exceeded for region", ToolType.TERRAFORM, "terraform_error"),
            ("==> azure-arm: errored after 5m", ToolType.PACKER, "packer_errored"),
            ("Build 'azure-arm' errored after 5 minutes", ToolType.PACKER, "packer_build_errored"),
            (
                "Error launching source instance: timeout",
                ToolType.PACKER,
                "packer_error_launching",
            ),
            ("Timeout waiting for SSH", ToolType.PACKER, "packer_timeout_waiting"),
            ("fatal: [webserver1]: FAILED!", ToolType.ANSIBLE, "ansible_fatal"),
            ("FAILED! => changed=false", ToolType.ANSIBLE, "ansible_failed"),
            ("ERROR! the playbook could not be found", ToolType.ANSIBLE, "ansible_error"),
            ("UNREACHABLE! Last task failed", ToolType.ANSIBLE, "ansible_unreachable"),
            ("FATAL: kernel panic", ToolType.GENERIC, "generic_fatal"),
            ("ERROR something went wrong", ToolType.GENERIC, "generic_error"),
            ("panic: runtime error", ToolType.GENERIC, "generic_panic"),
            (
                "Traceback (most recent call last)",
                ToolType.GENERIC,
                "generic_traceback",
            ),
            ("ValueError: invalid literal", ToolType.GENERIC, "generic_exception"),
        ],
    )
    def test_match(self, line: str, tool_type: ToolType, expected_pattern: str) -> None:
        """Pattern matches expected error line."""
        result = match_error(line, tool_type)
        assert result is not None
        assert result.tool_type == tool_type
        assert result.pattern_name == expected_pattern


# --- match_error negative cases (false positive prevention) ---


class TestMatchErrorNegative:
    """Test that normal log lines do NOT match error patterns."""

    @pytest.mark.parametrize(
        "line",
        [
            "azurerm_resource_group.example: Creating...",
            "Initializing provider plugins...",
            "Terraform has been successfully initialized!",
            "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
            "==> azure-arm: Running builder ...",
            "ok: [webserver1]",
            "PLAY RECAP *****",
            "changed=1    unreachable=0    failed=0",
            "this is a normal info message about errors in the documentation",
            "the error_count variable was reset",
            "no errors found in the log",
            "",
        ],
    )
    def test_no_match(self, line: str) -> None:
        """Normal log lines should not trigger error detection."""
        result = match_error(line)
        assert result is None


# --- generic fallback ---


class TestGenericFallback:
    """Test that generic patterns work as fallback when tool_type is set."""

    def test_generic_fallback_with_terraform(self) -> None:
        """Generic FATAL pattern matches even when tool_type is TERRAFORM."""
        result = match_error("FATAL: cannot continue", ToolType.TERRAFORM)
        assert result is not None
        assert result.tool_type == ToolType.GENERIC
        assert result.pattern_name == "generic_fatal"

    def test_generic_fallback_without_tool(self) -> None:
        """Generic patterns match when no tool_type is specified."""
        result = match_error("panic: runtime error: index out of range")
        assert result is not None
        assert result.tool_type == ToolType.GENERIC
        assert result.pattern_name == "generic_panic"

    def test_tool_specific_takes_priority(self) -> None:
        """Tool-specific pattern takes priority over generic."""
        result = match_error("Error: resource not found", ToolType.TERRAFORM)
        assert result is not None
        assert result.tool_type == ToolType.TERRAFORM
        assert result.pattern_name == "terraform_error"
