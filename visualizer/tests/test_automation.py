import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from vis2GPUV7 import fingerprint, render_track, write_json


class AutomationTests(unittest.TestCase):
    def test_unreliable_caption_timeline_is_held_before_render(self):
        from vis.karaoke import KaraokeQualityError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); audio, config, words = self.inputs(root)
            data = json.loads(words.read_text())
            data['words'] = [{'word':'invented','start':i*.05,'end':i*.05+.02,'confidence':.001}
                             for i in range(30)]
            write_json(words,data)
            with patch('vis.renderer.render_visualizer_pipeline') as renderer:
                with self.assertRaises(KaraokeQualityError):
                    render_track(audio, root/'out', config, words_file=words)
                renderer.assert_not_called()
            report = json.loads((root/'out/karaoke_review.json').read_text())
            self.assertEqual(report['status'],'needs_review')
            self.assertEqual(report['audio_sha256'],fingerprint(audio))

    def inputs(self, root):
        audio = root / 'wolf.flac'
        sf.write(audio, np.zeros((16000 * 2, 2)), 16000, subtype='PCM_24')
        config = root / 'config.json'
        write_json(config, {'video': {'fps': 30, 'use_vaapi': False}, 'layers': [], 'feedback': {'enabled': False}})
        words = root / 'words.json'
        write_json(words, {'duration': 2, 'audio_sha256': fingerprint(audio),
                           'words': [{'word': 'Wolf', 'start': .2, 'end': 1}], 'review_count': 0})
        return audio, config, words

    def test_wrong_audio_timeline_rejected_before_render(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio, config, words = self.inputs(root)
            data = json.loads(words.read_text())
            data['audio_sha256'] = 'wrong'
            write_json(words, data)
            with patch('vis.renderer.render_visualizer_pipeline') as renderer:
                with self.assertRaisesRegex(ValueError, 'exact audio'):
                    render_track(audio, root / 'out', config, words_file=words)
                renderer.assert_not_called()

    def test_failure_does_not_replace_published_video_or_leave_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio, config, words = self.inputs(root)
            final = root / 'out/widescreen/wolf.mp4'
            final.parent.mkdir(parents=True)
            final.write_bytes(b'previous video')
            def failed(*args, **kwargs):
                Path(args[1]).write_bytes(b'partial')
                raise RuntimeError('encoder failed')
            with patch('vis.renderer.render_visualizer_pipeline', failed):
                with self.assertRaisesRegex(RuntimeError, 'encoder failed'):
                    render_track(audio, root / 'out', config, words_file=words, formats=('widescreen',))
            self.assertEqual(final.read_bytes(), b'previous video')
            self.assertFalse((final.parent / '.wolf.inprogress.mp4').exists())
            self.assertEqual(json.loads((root / 'out/render_receipt.json').read_text())['status'], 'rendering')


if __name__ == '__main__':
    unittest.main()
