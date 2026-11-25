import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import upgrade as mac


@unittest.skipIf(os.name == "nt", "Mac updater uses POSIX file locks")
class MacUpgradeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.source=self.root/"new"
        self.target=self.root/"old 财务演示"
        self.files=mac.ESSENTIAL|{Path(p) for p in (
            "app/model.py","compliance/seed.py","README.md","requirements.txt","static/nested/site.js",
            "docs/schemas/report-snapshot-v2.schema.json",".env.example")}
        for rel in self.files:
            dest=self.source/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text("version = 'new'\n")
        (self.source/mac.MANIFEST).write_text("\n".join(sorted(str(rel) for rel in self.files)))
        for rel in ("start-demo.sh","app/model.py","compliance/seed.py","server.py","static/index.html",
                    "data/audit.sqlite3","data/runs/fixed/source.zip",".venv/keep",".env","user-notes.txt"):
            dest=self.target/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(("原文件 "+rel).encode())

    def sentinel(self):
        for rel in ("data/audit.sqlite3","data/runs/fixed/source.zip",".venv/keep",".env","user-notes.txt"):
            self.assertEqual((self.target/rel).read_bytes(),("原文件 "+rel).encode())

    def test_upgrade_installs_all_manifest_files_and_preserves_state(self):
        backup=mac.upgrade(self.source,self.target)
        self.sentinel()
        for rel in self.files:
            self.assertEqual((self.target/rel).read_bytes(),(self.source/rel).read_bytes())
        self.assertEqual((backup/"app/model.py").read_text(),"原文件 app/model.py")
        self.assertFalse((self.target/mac.RECOVERY).exists())
        self.assertIsNone(mac.upgrade(self.source,self.target))
        self.sentinel()

    def test_write_failure_rolls_back_updated_and_new_files(self):
        real=os.replace
        def fail_once(src,dst):
            if Path(dst)==self.target/"static/index.html" and ".mac-update-stage." in str(src):
                raise OSError("simulated interrupted installation")
            return real(src,dst)
        with patch.object(mac.os,"replace",side_effect=fail_once), self.assertRaisesRegex(OSError,"simulated"):
            mac.upgrade(self.source,self.target)
        self.sentinel()
        self.assertEqual((self.target/"app/model.py").read_text(),"原文件 app/model.py")
        self.assertFalse((self.target/"docs").exists())
        self.assertFalse((self.target/"app/casework.py").exists())
        self.assertFalse((self.target/mac.RECOVERY).exists())

    def test_missing_source_or_unsafe_destination_is_rejected_before_changes(self):
        (self.source/"app/casework.py").unlink()
        with self.assertRaisesRegex(ValueError,"缺少文件"):
            mac.upgrade(self.source,self.target)
        self.assertEqual((self.target/"server.py").read_text(),"原文件 server.py")
        (self.source/"app/casework.py").write_text("new=True")
        (self.target/"scripts").symlink_to(self.target/"data",target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"符号链接"):
            mac.upgrade(self.source,self.target)
        self.sentinel()

    def test_abrupt_exit_is_recovered_on_the_next_invocation(self):
        program = """import os,sys
from pathlib import Path
from scripts import upgrade
real = upgrade.os.replace
def stop_after_commit(src,dst):
    real(src,dst)
    if Path(dst)==Path(sys.argv[2])/'app/model.py' and '.mac-update-stage.' in str(src):
        os._exit(99)
upgrade.os.replace = stop_after_commit
upgrade.upgrade(Path(sys.argv[1]),Path(sys.argv[2]))
"""
        result=subprocess.run([sys.executable,"-c",program,str(self.source),str(self.target)],cwd=mac.ROOT)
        self.assertEqual(result.returncode,99)
        self.assertTrue((self.target/mac.RECOVERY).exists())
        # If the new package is now incomplete, recovery still happens before validation.
        (self.source/"app/casework.py").unlink()
        with self.assertRaises(ValueError):
            mac.upgrade(self.source,self.target)
        self.assertEqual((self.target/"app/model.py").read_text(),"原文件 app/model.py")
        self.assertFalse((self.target/"app/casework.py").exists())
        self.assertFalse((self.target/mac.RECOVERY).exists())
        self.sentinel()

    def test_manifest_cannot_overwrite_runtime_data(self):
        with (self.source/mac.MANIFEST).open("a") as stream:
            stream.write("\ndata/audit.sqlite3\n")
        with self.assertRaisesRegex(ValueError,"升级范围"):
            mac.upgrade(self.source,self.target)
        self.sentinel()

class ReleaseManifestTests(unittest.TestCase):
    def test_release_runtime_assets_are_in_upgrade_manifest(self):
        listed=set(mac.entries(mac.ROOT))
        for tree in ("app","compliance","examples","static","assets","docs/schemas"):
            for item in (mac.ROOT/tree).rglob("*"):
                if item.is_file() and "__pycache__" not in item.parts and not item.name.startswith("."):
                    self.assertIn(item.relative_to(mac.ROOT),listed)
        for item in mac.ROOT.glob("*.command"):
            self.assertIn(item.relative_to(mac.ROOT),listed)
