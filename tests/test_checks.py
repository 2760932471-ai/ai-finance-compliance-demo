import tempfile, unittest
from pathlib import Path
from PIL import Image
from app import checks, model, engine


class LocalChecksTests(unittest.TestCase):
    def test_calendar_records_match_conflict_invalid_and_missing(self):
        evidence = [{'id': str(i), 'category': '现场照片', 'text': text, 'method': '测试文字'} for i, text in enumerate([
            '2026年04月18日 星期六', '2026-04-18 周一', '2026年02月30日 星期一', '水印看不清'])]
        actual = checks.collect(evidence, [])
        self.assertEqual([r['status'] for r in actual['calendar']], ['matched', 'conflict', 'invalid', 'unavailable'])
        self.assertEqual(actual['calendar'][0]['expected'], '星期六')

    def test_exif_nested_capture_time_and_missing_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'photo.jpg'
            image = Image.new('RGB', (50, 60))
            exif = Image.Exif(); exif[272] = 'Fixture Camera'; exif[34665] = {36867: '2026:04:18 14:05:00'}
            image.save(path, exif=exif)
            actual = checks.image_metadata(path)
            self.assertEqual(actual['device'], 'Fixture Camera')
            self.assertEqual(actual['captured_at'], '2026:04:18 14:05:00')
            self.assertEqual(actual['missing'], ['GPS坐标'])
            exif[34853] = {1: 'N', 2: (30., 30., 0.), 3: 'E', 4: (104., 0., 0.)}
            image.save(path, exif=exif)
            actual = checks.image_metadata(path)
            self.assertEqual(actual['gps'], {'latitude': 30.5, 'longitude': 104.0})
            self.assertFalse(actual['missing'])
            image.save(path)
            actual = checks.image_metadata(path)
            self.assertFalse(actual['exif_present'])
            self.assertEqual(actual['status'], 'read')
            self.assertEqual(len(actual['missing']), 3)
            self.assertEqual(checks.image_metadata(Path(tmp) / 'absent.jpg')['status'], 'error')

    def test_historical_snapshot_does_not_mutate_run(self):
        run = {'evidence': [], 'documents': [], 'results': [{'verdict': '不通过'}]}
        supplemental = checks.snapshot(run)
        self.assertTrue(supplemental['supplemental'])
        self.assertNotIn('technical_checks', run)
        self.assertEqual(run['results'][0]['verdict'], '不通过')
        run['technical_checks'] = supplemental
        self.assertIs(checks.snapshot(run), supplemental)
