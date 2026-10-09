"""pre_tool_call identity guard (requester identity binding, issue 03):
terminal and execute_code calls that override the session identity or reach
the ERPNext credentials around the erp_* tools are blocked. Pattern matching,
defence in depth only."""

import unittest

from qkeee_erp_plugin import identity_guard

ALLOWED_TERMINAL = [
    "python /opt/data/skills/qkeee-erp/qkeee-erp-associate/scripts/client.py query Customer",
    "python scripts/client.py whoami",
    "echo $HERMES_SESSION_USER_ID",
    'test "$HERMES_SESSION_PLATFORM" = google_chat && echo chat',
    'test "${HERMES_SESSION_PLATFORM}" == google_chat',
    "printenv | grep HERMES_SESSION_",
    "env | sort",
    "ls /opt/cwd && cat notes.env",
    "cat ~/skills/qkeee-erp/qkeee-erp-associate/SKILL.md",
    "MY_HERMES_SESSION_USER_ID=x python run.py",
]

SESSION_OVERRIDES = [
    "HERMES_SESSION_USER_ID=boss@org.com python scripts/client.py execute-write-op x",
    "export HERMES_SESSION_USER_ID=boss@org.com; python client.py query Customer",
    "export HERMES_SESSION_PLATFORM",
    "unset HERMES_SESSION_PLATFORM && python client.py query Customer",
    "env HERMES_SESSION_USER_ID=boss@org.com python client.py query Customer",
    "env -u HERMES_SESSION_PLATFORM python client.py query Customer",
    "env --unset=HERMES_SESSION_PLATFORM python client.py query Customer",
    "env -i python client.py query Customer",
    "env --ignore-environment PATH=/usr/bin python client.py",
    "declare -x HERMES_SESSION_USER_ID=boss@org.com",
    "echo ${HERMES_SESSION_USER_ID:=boss@org.com}",
    "python -c \"import os; os.environ['HERMES_SESSION_USER_ID']='boss@org.com'\"",
    "python -c \"import os; os.environ.pop('HERMES_SESSION_PLATFORM')\"",
    "python -c \"import os; del os.environ['HERMES_SESSION_PLATFORM']\"",
    "python -c \"import subprocess; subprocess.run(['python','client.py'], env={'HERMES_SESSION_USER_ID': 'x'})\"",
    "printf -v HERMES_SESSION_USER_ID '%s' boss@org.com; python client.py query Customer",
    "read HERMES_SESSION_USER_ID <<< boss@org.com",
    "HERMES_SESSION_USER_ID+=x python client.py query Customer",
    "python -c \"import os; os.environ.__setitem__('HERMES_SESSION_USER_ID', 'x')\"",
    "python -c \"import os; os.environ.setdefault('HERMES_SESSION_USER_ID', 'x')\"",
]

ENV_FILE_READS = [
    "cat /opt/data/plugin-data/qkeee-erp/qkeee-erp.env",
    "source /opt/data/qkeee-erp.env",
    ". /opt/data/qkeee-erp.env && curl $ERPNEXT_URL",
    "grep API_KEY /opt/data/plugin-data/qkeee-erp/QKEEE-ERP.ENV",
    "python -c \"print(open('/opt/data/qkeee-erp.env').read())\"",
    "ls /opt/data/plugin-data/qkeee-erp/",
    "tar cf - plugin-data/qkeee-erp | base64",
    "cat /opt/data/profiles/*/plugin-data/*/*.env",
    "cat /opt/data/qkeee-erp.e?v",
    "cat /opt/data/qkeee*.env",
]

ALLOWED_CODE = [
    "print(1 + 1)",
    "import json\nprint(json.dumps({'a': 1}))",
    "from hermes_tools import terminal\nterminal('ls')",
]

BLOCKED_CODE = [
    ("env_file", "print(open('/opt/data/plugin-data/qkeee-erp/qkeee-erp.env').read())"),
    ("core_client", "import core.client as c\nc.query('Customer')"),
    ("core_client", "from core import client\nclient.query('Customer')"),
    ("core_client", "from core.client import Client"),
    ("erpnext_api", "import requests\nrequests.get(url + '/api/resource/Customer')"),
    ("erpnext_api", "requests.post(url + '/api/method/frappe.client.insert')"),
    ("session_override", "import os\nos.environ['HERMES_SESSION_USER_ID'] = 'boss@org.com'"),
]


class TerminalTests(unittest.TestCase):
    def test_allowed_commands_pass(self):
        for command in ALLOWED_TERMINAL:
            with self.subTest(command=command):
                self.assertIsNone(identity_guard.pre_tool_call(tool_name="terminal", args={"command": command}))

    def test_session_overrides_are_blocked(self):
        for command in SESSION_OVERRIDES:
            with self.subTest(command=command):
                out = identity_guard.pre_tool_call(tool_name="terminal", args={"command": command})
                self.assertEqual(out["action"], "block")
                self.assertIn("session_override", out["message"])

    def test_env_file_reads_are_blocked(self):
        for command in ENV_FILE_READS:
            with self.subTest(command=command):
                out = identity_guard.pre_tool_call(tool_name="terminal", args={"command": command})
                self.assertEqual(out["action"], "block")
                self.assertIn("env_file", out["message"])

    def test_block_message_points_at_erp_tools(self):
        out = identity_guard.pre_tool_call(tool_name="terminal", args={"command": SESSION_OVERRIDES[0]})
        self.assertIn("erp_", out["message"])


class ExecuteCodeTests(unittest.TestCase):
    def test_allowed_code_passes(self):
        for code in ALLOWED_CODE:
            with self.subTest(code=code):
                self.assertIsNone(identity_guard.pre_tool_call(tool_name="execute_code", args={"code": code}))

    def test_blocked_code(self):
        for rule, code in BLOCKED_CODE:
            with self.subTest(code=code):
                out = identity_guard.pre_tool_call(tool_name="execute_code", args={"code": code})
                self.assertEqual(out["action"], "block")
                self.assertIn(rule, out["message"])


class ScopeTests(unittest.TestCase):
    def test_env_file_rule_covers_file_tools(self):
        # read_file and friends reach the same files as `cat`.
        for tool_name, args in (("read_file", {"path": "/opt/data/plugin-data/qkeee-erp/qkeee-erp.env"}),
                                ("search_files", {"pattern": "API_KEY", "path": "/opt/data/plugin-data/qkeee-erp"}),
                                ("patch", {"edits": [{"path": "/opt/data/qkeee-erp.env"}]})):
            with self.subTest(tool=tool_name):
                out = identity_guard.pre_tool_call(tool_name=tool_name, args=args)
                self.assertEqual(out["action"], "block")
                self.assertIn("env_file", out["message"])

    def test_file_tool_content_may_mention_the_env_file(self):
        # Only path arguments count: editing docs that name the file is fine.
        for tool_name, args in (("write_file", {"path": "/opt/cwd/notes.md", "content": "creds: qkeee-erp.env"}),
                                ("search_files", {"pattern": "qkeee-erp.env", "path": "/opt/cwd"}),
                                ("patch", {"edits": [{"path": "/opt/cwd/a.md", "new": "see qkeee-erp.env"}]})):
            with self.subTest(tool=tool_name):
                self.assertIsNone(identity_guard.pre_tool_call(tool_name=tool_name, args=args))

    def test_other_tools_are_ignored(self):
        self.assertIsNone(identity_guard.pre_tool_call(tool_name="read_file", args={"path": "/opt/cwd/notes.md"}))
        # erp_* tools bind the requester themselves and run gateway-side.
        self.assertIsNone(identity_guard.pre_tool_call(
            tool_name="erp_query", args={"doctype": "Customer", "filters": "HERMES_SESSION_USER_ID=x"}))
        # Session-override rules are terminal/execute_code only.
        self.assertIsNone(identity_guard.pre_tool_call(
            tool_name="write_file", args={"path": "/opt/cwd/a.sh", "content": "unset HERMES_SESSION_PLATFORM"}))

    def test_terminal_only_rules_do_not_apply_to_other_tools(self):
        # `/api/resource` in a shell is not in scope: without the env file
        # the terminal has no credentials to call it with.
        self.assertIsNone(identity_guard.pre_tool_call(
            tool_name="terminal", args={"command": "curl https://erp.example.com/api/resource/Customer"}))

    def test_malformed_args_do_not_raise(self):
        for args in (None, {}, {"command": None}, {"command": 42}, "rm -rf /"):
            with self.subTest(args=args):
                self.assertIsNone(identity_guard.pre_tool_call(tool_name="terminal", args=args))


if __name__ == "__main__":
    unittest.main()
