import json
from pathlib import Path
import tempfile
import unittest

from vis.karaoke import clean_lyrics, find_prompt, reference_candidates, group_segments, validate_timeline, words
from vis.captions import CaptionPainter
from vis.karaoke import select_recognition, recognition_segments, caption_quality, require_caption_quality, KaraokeQualityError


class KaraokeTests(unittest.TestCase):
    def test_hallucinated_stem_loop_selects_independent_mix_transcript(self):
        phrase = "I've been waiting for you for a long time"
        stem = {'transcription': [{'text': phrase, 'offsets': {'from': i*1000, 'to': (i+1)*1000}}
                                 for i in range(30)]}
        lyrics = 'Velvet dress and silk untied blood upon the satin hide'
        mix = {'transcription': [{'text': lyrics, 'offsets': {'from': 0, 'to': 10000}}]}
        primary, secondary, report = select_recognition(stem, mix, lyrics, 60)
        self.assertIs(primary, mix)
        self.assertIs(secondary, stem)
        self.assertEqual(report['selected'], 'mix')

    def test_music_descriptions_and_segments_past_audio_end_are_not_lyrics(self):
        r = {'transcription': [{'text': '*sad music*', 'offsets': {'from': 0, 'to': 5000}},
                              {'text': 'Real words', 'offsets': {'from': 5000, 'to': 9000}},
                              {'text': 'Thank you', 'offsets': {'from': 11000, 'to': 40000}}]}
        self.assertEqual(recognition_segments(r, 10), [{'text': 'Real words', 'start': 5, 'end': 9}])

    def test_catastrophic_caption_support_and_phrase_drift_block_publication(self):
        t = {'words': [{'word': 'invented', 'start': i*.1, 'end': i*.1+.05, 'confidence': .01}
                       for i in range(30)]}
        with self.assertRaises(KaraokeQualityError): require_caption_quality(t)
        t = {'words': [{'word': 'late', 'start': 80, 'end': 81, 'confidence': .9, 'segment': 0}],
             'decisions': [{'start': 10, 'end': 20}]}
        self.assertEqual(caption_quality(t)['phrase_drift_words'], 1)
        with self.assertRaises(KaraokeQualityError): require_caption_quality(t)

    def test_alignment_never_combines_distant_phrase_targets(self):
        from unittest.mock import patch
        import torch
        import numpy as np
        import soundfile as sf
        from vis.karaoke import align_song
        calls = []
        class LocalAligner:
            def __init__(self, *args): self.torch = torch
            def emission(self, samples): return torch.zeros((1, len(samples)//320, 4))
            def align(self, emission, text, start, duration, frame_bounds=None):
                # The old whole-song pass passed both words and pulled them
                # across the gap. Every new target must belong to one window.
                assert len(text) == 1
                if frame_bounds is None:
                    a, b = start+.1, start+.2
                else:
                    a, b = float(frame_bounds[0][2]), float(frame_bounds[1][3])
                    calls.append((text[0],a,b))
                return [{'word': text[0], 'start': a, 'end': b, 'confidence': .9}], 0.
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder); audio = work/'vocals.wav'
            sf.write(audio, np.zeros(16*16000), 16000)
            recognition = {'transcription': [{'text': 'A', 'offsets': {'from': 1000, 'to': 2000}},
                                            {'text': 'B', 'offsets': {'from': 11000, 'to': 12000}}]}
            with patch('vis.karaoke.AcousticAligner', LocalAligner):
                output, _, _ = align_song(audio, recognition, 'A B', work)
            self.assertEqual(len(output), 2)
            self.assertLess(calls[0][2],3)
            self.assertGreater(calls[1][1],10)

    def test_whisper_prime_apostrophe_stays_one_word(self):
        self.assertEqual(words('I′m she’s you＇ve'), ["I'm", "she's", "you've"])

    def test_directions_removed_but_backing_vocals_kept(self):
        text = '[Verse 1]\n(female screamed)\nSpells crack! (Male: HIT!)\n(heavy guitar)\nOh (oh!)'
        self.assertEqual(clean_lyrics(text), 'Spells crack! HIT!\nOh oh!')

    def test_exact_id_wins_over_rewrite_source_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a.json').write_text(json.dumps({'id': 'new_song', 'source_id': 'song_v2', 'lyrics': 'Wrong'}))
            (root / 'b.json').write_text(json.dumps({'songs': [{'id': 'song_v2', 'lyrics': 'Correct'}]}))
            self.assertEqual(find_prompt('song_v2.ogg', root)['lyrics'], 'Correct')

    def test_explicit_json_for_another_song_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'wrong.json'
            p.write_text(json.dumps({'id':'another_song','lyrics':'Wrong song words'}))
            with self.assertRaisesRegex(ValueError, 'No exact lyric prompt'):
                find_prompt('song.flac', explicit=p)

    def test_conflicting_prompts_require_explicit_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('one', 'two'):
                (root / (name+'.json')).write_text(json.dumps({'id': 'song', 'lyrics': name}))
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                find_prompt('song.ogg', root)
            self.assertEqual(find_prompt('song.ogg', root, root/'one.json')['lyrics'], 'one')

    def test_no_full_verse_invention(self):
        candidates, issues = reference_candidates([{'text': 'I sing now'}],
            'I sing now\nHere is an entire missing verse which was never actually sung')
        self.assertEqual(candidates, [['I', 'sing', 'now']])
        self.assertTrue(issues)

    def test_bounded_correction_is_only_a_candidate(self):
        candidates, _ = reference_candidates([{'text': 'I see a blood wet sky tonight'}],
                                             'I see a blood red sky tonight')
        self.assertEqual(candidates[0], ['I', 'see', 'a', 'blood', 'red', 'sky', 'tonight'])

    def test_grouping_crosses_coarse_phrase_boundaries_not_instrumentals(self):
        segs = [{'text': 'one', 'start': 0, 'end': 4}, {'text': 'two', 'start': 4, 'end': 7},
                {'text': 'three', 'start': 15, 'end': 18}]
        self.assertEqual([s['text'] for s in group_segments(segs)], ['one two', 'three'])
        self.assertEqual(segs[0]['text'], 'one')

    def test_invalid_timestamps_rejected(self):
        for a, b in [(2, 1), (-1, 1), (0, 11), (float('nan'), 1)]:
            with self.assertRaises(ValueError):
                validate_timeline({'duration': 10, 'words': [{'word': 'Hi', 'start': a, 'end': b}]})

    def test_ctc_alignment_uses_acoustic_peaks_and_repeated_characters(self):
        import torch
        import torchaudio
        from vis.karaoke import AcousticAligner
        aligner = AcousticAligner.__new__(AcousticAligner)
        aligner.torch, aligner.ta = torch, torchaudio
        aligner.labels = {'-': 0, '|': 1, 'A': 2, 'B': 3}
        # A A | B with the blank between the two A's required by CTC.
        ids = [0, 0, 2, 0, 2, 0, 1, 0, 0, 3, 3, 0]
        emissions = torch.full((1, len(ids), 4), -12.)
        for i, token in enumerate(ids):
            emissions[0, i, token] = 0
        aligned, score = aligner.align(emissions.log_softmax(-1), ['AA', 'B'], 10, 1.2)
        self.assertAlmostEqual(aligned[0]['start'], 10.2)
        self.assertAlmostEqual(aligned[0]['end'], 10.5)
        self.assertAlmostEqual(aligned[1]['start'], 10.9)
        self.assertAlmostEqual(aligned[1]['end'], 11.1)
        self.assertGreater(score, -.01)
        # Chunked emissions can omit instrumental gaps; timestamps must retain
        # absolute audio time instead of compressing the gap out of the video.
        starts = [i*.1 + (5 if i >= 6 else 0) for i in range(len(ids))]
        ends = [t+.1 for t in starts]
        aligned, _ = aligner.align(emissions.log_softmax(-1), ['AA', 'B'], 0, 6.2, (starts, ends))
        self.assertAlmostEqual(aligned[0]['start'], .2)
        self.assertAlmostEqual(aligned[1]['start'], 5.9)

    def test_highlight_ends_during_word_gap(self):
        painter = CaptionPainter({'duration': 10, 'words': [
            {'word': 'Hello', 'start': 1, 'end': 1.5}, {'word': 'world', 'start': 1.8, 'end': 2.5}]}, 1280, 720)
        self.assertEqual(painter.state(.9), (-1, -1))
        self.assertEqual(painter.state(1.1), (0, 0))
        self.assertEqual(painter.state(1.6), (0, -1))
        self.assertEqual(painter.state(1.9), (0, 1))
        self.assertEqual(painter.state(3), (-1, -1))
        self.assertNotEqual(painter.paint((0,0)).tobytes(), painter.paint((0,1)).tobytes())

    def test_portrait_text_fits(self):
        painter = CaptionPainter({'duration': 10, 'words': [
            {'word': 'electrified', 'start': 1, 'end': 2}, {'word': 'humiliation', 'start': 2, 'end': 3},
            {'word': 'fractured', 'start': 3, 'end': 4}]}, 720, 1280)
        for i in range(len(painter.phrases)):
            box = painter.paint((i, 0)).getbbox()
            self.assertGreater(box[0], 0)
            self.assertLess(box[2], 720)

    def test_overlay_never_contaminates_feedback(self):
        import numpy as np
        from vis.renderer import ModernGLRenderer
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'words.json'
            path.write_text(json.dumps({'duration': 3, 'words': [{'word': 'Hello', 'start': 1, 'end': 2}]}))
            renderer = ModernGLRenderer(320, 180, {'layers': [], 'feedback': {'enabled': False},
                                                   'karaoke': {'words_file': str(path)}})
            try:
                result = renderer.render_frame({'time': 1.5}, renderer.fbo_a, renderer.tex_b)
                scene = np.frombuffer(renderer.scene_fbo.read(components=3), dtype=np.uint8)
                captioned = np.frombuffer(result.read(components=3), dtype=np.uint8)
                self.assertFalse(scene.any())
                self.assertTrue(captioned.any())
                result = renderer.render_frame({'time': 2.8}, renderer.fbo_a, renderer.tex_b)
                self.assertFalse(np.frombuffer(result.read(components=3), dtype=np.uint8).any())
            finally:
                renderer.destroy()


if __name__ == '__main__':
    unittest.main()
