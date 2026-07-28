from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import storage_backup_regression


class StorageBackupRegressionTests(unittest.TestCase):
    def test_checkpoint_parser(self) -> None:
        output = (
            b"STORAGE_BACKUP HEADER=4D434150 MAGICBUS_FAILURES=00000000\n"
            b"STORAGE_RESTORE HEADER=4D434150 MAGICBUS_FAILURES=00000000 "
            b"DIALOG=886C0C0D\n"
        )
        self.assertEqual(
            storage_backup_regression.parse_checkpoints(output),
            {
                "BACKUP": {
                    "HEADER": 0x4D434150,
                    "MAGICBUS_FAILURES": 0,
                },
                "RESTORE": {
                    "HEADER": 0x4D434150,
                    "MAGICBUS_FAILURES": 0,
                    "DIALOG": 0x886C0C0D,
                },
            },
        )

    def test_scripts_cover_backup_and_restore(self) -> None:
        backup = storage_backup_regression.backup_script("/tmp/card")
        restore = storage_backup_regression.restore_script("/tmp/card")
        self.assertIn('image:load("/tmp/card")', backup)
        self.assertIn("storage-backup-progress.png", backup)
        self.assertIn("STORAGE_BACKUP HEADER=", backup)
        self.assertIn("MAGICBUS_FAILURES=", backup)
        self.assertIn('image:load("/tmp/card")', restore)
        self.assertIn("storage-restore-progress.png", restore)
        self.assertIn("STORAGE_RESTORE HEADER=", restore)
        self.assertIn("MAGICBUS_FAILURES=", restore)
        self.assertIn("dialog_checksum", restore)

    def test_machine_config_selects_magicbus_keyboard(self) -> None:
        config = storage_backup_regression.deterministic_machine_config()
        self.assertIn('tag=":MAGICBUS_ACCESSORY"', config)
        self.assertIn('value="1"', config)


if __name__ == "__main__":
    unittest.main()
