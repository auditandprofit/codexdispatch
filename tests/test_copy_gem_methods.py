import os
import tempfile
import unittest

from copy_gem_methods import resolve_paths_from_parsed


class TestResolvePathsFromParsed(unittest.TestCase):
    def test_resolves_paths(self):
        with tempfile.TemporaryDirectory() as focused_dir, tempfile.TemporaryDirectory() as parsed_dir:
            # create a fake focused gem file
            gem_root = os.path.join(focused_dir, "gem", "foo", "lib")
            os.makedirs(gem_root)
            rb_file = os.path.join(gem_root, "bar.rb")
            with open(rb_file, "w", encoding="utf-8") as fh:
                fh.write("puts 'hi'\n")

            # create corresponding parsed.json marker under another tree
            marker_dir = os.path.join(parsed_dir, "tmp", "nested", "gem", "foo", "lib")
            os.makedirs(marker_dir)
            marker_file = os.path.join(marker_dir, "bar.rb.parsed.json")
            with open(marker_file, "w", encoding="utf-8") as fh:
                fh.write("{}")

            paths = resolve_paths_from_parsed(focused_dir, parsed_dir)
            self.assertEqual(paths, [rb_file])


if __name__ == "__main__":
    unittest.main()
