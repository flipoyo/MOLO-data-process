import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from raw_to_processed.canonical import canonical_json, digest, dir_digest, git_tree_hash


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **(env or {})}, check=True,
                          capture_output=True, text=True).stdout.strip()


class Hashes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dir_digest_depends_on_content_and_names_not_on_mtime(self):
        d = self.tmp / "d"
        d.mkdir()
        (d / "a.csv").write_text("1\n")
        (d / "b.csv").write_text("2\n")
        before = dir_digest(d)
        os.utime(d / "a.csv", (1, 1))
        self.assertEqual(dir_digest(d), before)
        (d / "b.csv").write_text("3\n")
        self.assertNotEqual(dir_digest(d), before)
        (d / "b.csv").write_text("2\n")
        (d / "b.csv").rename(d / "c.csv")
        self.assertNotEqual(dir_digest(d), before)

    @unittest.skipIf(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, "git indisponible")
    def test_commit_sha_changes_with_the_clock_but_the_tree_hash_does_not(self):
        shas, trees = [], []
        for k, when in enumerate(("2026-01-01T00:00:00", "2027-06-15T12:00:00")):
            repo = self.tmp / f"repo{k}"
            repo.mkdir()
            (repo / "measures.csv").write_text("Date,Temp1\n2024-10-07T12:00:00Z,11.369\n")
            env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_CONFIG_GLOBAL": os.devnull}
            _git(repo, "init", "-q")
            _git(repo, "add", "-A")
            _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "same content", env=env)
            shas.append(_git(repo, "rev-parse", "HEAD"))
            trees.append(_git(repo, "rev-parse", "HEAD^{tree}"))
        self.assertNotEqual(shas[0], shas[1])        # le commit_sha d'un dépôt dépend de la date
        self.assertEqual(trees[0], trees[1])         # le hash d'arbre, non
        self.assertEqual(git_tree_hash(self.tmp / "repo0"), trees[0])

    def test_canonical_json_matches_complexgitsync_when_available(self):
        src = os.environ.get("CGS_SRC", "/home/claude/ComplexGitSync/src")
        if not Path(src, "ComplexGitSync").is_dir():
            self.skipTest("ComplexGitSync introuvable (définir CGS_SRC=<chemin vers ComplexGitSync/src>)")
        sys.path.insert(0, src)
        try:
            from ComplexGitSync.merkle import MerkleRoot
        except Exception as exc:
            self.skipTest(f"import de ComplexGitSync impossible : {exc}")
        finally:
            sys.path.remove(src)
        payload = {"b": [1, 2.5, "é"], "a": {"z": None, "y": True}}
        self.assertEqual(canonical_json(payload), MerkleRoot.canonical_json(payload))
        self.assertEqual(digest(payload), MerkleRoot.digest(payload))


if __name__ == "__main__":
    unittest.main()
