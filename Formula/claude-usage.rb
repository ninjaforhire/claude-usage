class ClaudeUsage < Formula
  desc "HotFix Ops dashboard for local Claude and Codex usage"
  homepage "https://github.com/ninjaforhire/claude-usage"
  url "https://github.com/ninjaforhire/claude-usage/archive/refs/tags/v1.4.0.tar.gz"
  version "1.4.0"
  sha256 "3f06925e6791a208130ac4264289fdfb0b91f6382386d813b8302a9485c90b6d"
  license "MIT"
  head "https://github.com/ninjaforhire/claude-usage.git", branch: "main"

  depends_on "python@3.13"

  def install
    libexec.install Dir["*.py"]
    libexec.install "assets", "connectors", "vendor"

    wrapper = <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.13"].opt_bin}/python3" "#{libexec}/cli.py" "$@"
    EOS
    (bin/"hotfix-ops-usage").write wrapper
    (bin/"claude-usage").write wrapper
    chmod 0755, [bin/"hotfix-ops-usage", bin/"claude-usage"]
  end

  test do
    # The HotFix Ops command is primary; claude-usage remains a compatibility alias.
    output = shell_output("#{bin}/hotfix-ops-usage")
    assert_match "HotFix Ops Usage Dashboard", output
    assert_match "scan", output
    assert_match "dashboard", output
    assert_match "HotFix Ops Usage Dashboard", shell_output("#{bin}/claude-usage")

    (testpath/"projects").mkpath
    scan_output = shell_output("#{bin}/hotfix-ops-usage scan --projects-dir #{testpath}/projects")
    assert_match "Scan complete", scan_output
  end
end
